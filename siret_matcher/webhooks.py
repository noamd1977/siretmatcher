"""Systeme de webhook pour notifier des systemes externes.

Usages :
- Pousser un prospect enrichi vers le CRM Akol
- Notifier n8n d'un nouveau match
- Integrer avec Zapier, Make, etc.
"""
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from siret_matcher.logging_config import log_structured
from siret_matcher.metrics import WEBHOOK_SENT, WEBHOOK_ERRORS, WEBHOOK_DURATION

logger = logging.getLogger("siret_matcher.webhooks")

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "webhooks.json"


@dataclass
class WebhookConfig:
    id: str
    name: str
    url: str
    events: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    active: bool = True
    retry: int = 3
    timeout: int = 10


@dataclass
class WebhookLogEntry:
    webhook_id: str
    event: str
    status: str  # "success", "error"
    status_code: int | None = None
    error: str | None = None
    timestamp: str = ""
    duration_ms: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class WebhookManager:
    def __init__(self, config_path: str | Path | None = None):
        self._config_path = Path(config_path) if config_path else _CONFIG_PATH
        self._webhooks: list[WebhookConfig] = []
        self._log: list[WebhookLogEntry] = []
        self._max_log = 100
        self._client: httpx.AsyncClient | None = None
        self.reload_config()

    def reload_config(self):
        """Recharge la config des webhooks depuis le JSON."""
        self._webhooks = []
        if not self._config_path.exists():
            logger.debug("webhooks config not found: %s", self._config_path)
            return
        try:
            with open(self._config_path) as f:
                data = json.load(f)
            for wh in data.get("webhooks", []):
                self._webhooks.append(WebhookConfig(
                    id=wh["id"],
                    name=wh.get("name", wh["id"]),
                    url=wh["url"],
                    events=wh.get("events", []),
                    headers=self._resolve_headers(wh.get("headers", {})),
                    active=wh.get("active", True),
                    retry=wh.get("retry", 3),
                    timeout=wh.get("timeout", 10),
                ))
            logger.info("Loaded %d webhooks from config", len(self._webhooks))
        except Exception as e:
            logger.error("Failed to load webhooks config: %s", e)

    @staticmethod
    def _resolve_headers(headers: dict[str, str]) -> dict[str, str]:
        """Resolve {ENV_VAR} placeholders in header values."""
        resolved = {}
        for k, v in headers.items():
            if "{" in v and "}" in v:
                # Extract env var name from {VAR_NAME}
                import re
                def _replace(m):
                    return os.environ.get(m.group(1), m.group(0))
                v = re.sub(r"\{(\w+)\}", _replace, v)
            resolved[k] = v
        return resolved

    @property
    def webhooks(self) -> list[WebhookConfig]:
        return list(self._webhooks)

    @property
    def log_entries(self) -> list[WebhookLogEntry]:
        return list(self._log)

    def set_client(self, client: httpx.AsyncClient):
        """Set the shared httpx client."""
        self._client = client

    async def emit(self, event: str, payload: dict):
        """Emit an event to all subscribed webhooks (fire-and-forget)."""
        tasks = []
        for wh in self._webhooks:
            if not wh.active:
                continue
            if event not in wh.events:
                continue
            tasks.append(self._send_webhook(wh, event, payload))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_webhook(self, wh: WebhookConfig, event: str, payload: dict):
        """Send a webhook with retry and exponential backoff."""
        body = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        }

        client = self._client or httpx.AsyncClient()
        own_client = self._client is None

        last_error = None
        attempts = max(wh.retry, 1)

        try:
            for attempt in range(attempts):
                t0 = time.perf_counter()
                try:
                    resp = await client.post(
                        wh.url,
                        json=body,
                        headers=wh.headers,
                        timeout=wh.timeout,
                    )
                    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
                    WEBHOOK_DURATION.labels(webhook_id=wh.id).observe(duration_ms / 1000)

                    if resp.status_code < 400:
                        WEBHOOK_SENT.labels(webhook_id=wh.id, event=event).inc()
                        self._add_log(WebhookLogEntry(
                            webhook_id=wh.id, event=event, status="success",
                            status_code=resp.status_code, duration_ms=duration_ms,
                        ))
                        log_structured(
                            logger, logging.INFO, "webhook_sent",
                            webhook_id=wh.id, event=event,
                            status_code=resp.status_code, duration_ms=duration_ms,
                        )
                        return

                    # Server error — retry
                    last_error = f"HTTP {resp.status_code}"
                    if attempt < attempts - 1:
                        await asyncio.sleep(2 ** attempt)

                except httpx.TimeoutException:
                    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
                    last_error = "timeout"
                    if attempt < attempts - 1:
                        await asyncio.sleep(2 ** attempt)

                except httpx.HTTPError as e:
                    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
                    last_error = str(e)
                    if attempt < attempts - 1:
                        await asyncio.sleep(2 ** attempt)

            # All retries exhausted
            error_type = "timeout" if last_error == "timeout" else "http_error"
            WEBHOOK_ERRORS.labels(webhook_id=wh.id, error_type=error_type).inc()
            self._add_log(WebhookLogEntry(
                webhook_id=wh.id, event=event, status="error",
                error=last_error, duration_ms=duration_ms,
            ))
            log_structured(
                logger, logging.WARNING, "webhook_failed",
                webhook_id=wh.id, event=event, error=last_error,
                attempts=attempts,
            )

        finally:
            if own_client:
                await client.aclose()

    async def send_test(self, webhook_id: str) -> dict:
        """Send a test payload to a specific webhook. Returns result."""
        wh = next((w for w in self._webhooks if w.id == webhook_id), None)
        if not wh:
            return {"error": f"Webhook '{webhook_id}' not found"}

        test_payload = {
            "test": True,
            "message": f"Test webhook '{wh.name}' from SIRET Matcher",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        client = self._client or httpx.AsyncClient()
        own_client = self._client is None

        try:
            t0 = time.perf_counter()
            resp = await client.post(
                wh.url,
                json={"event": "test", "timestamp": test_payload["timestamp"], "data": test_payload},
                headers=wh.headers,
                timeout=wh.timeout,
            )
            duration_ms = round((time.perf_counter() - t0) * 1000, 1)

            self._add_log(WebhookLogEntry(
                webhook_id=wh.id, event="test",
                status="success" if resp.status_code < 400 else "error",
                status_code=resp.status_code, duration_ms=duration_ms,
            ))

            return {
                "webhook_id": wh.id,
                "status_code": resp.status_code,
                "duration_ms": duration_ms,
                "success": resp.status_code < 400,
            }
        except Exception as e:
            self._add_log(WebhookLogEntry(
                webhook_id=wh.id, event="test", status="error", error=str(e),
            ))
            return {"webhook_id": wh.id, "error": str(e), "success": False}
        finally:
            if own_client:
                await client.aclose()

    def _add_log(self, entry: WebhookLogEntry):
        self._log.append(entry)
        if len(self._log) > self._max_log:
            self._log = self._log[-self._max_log:]


# Singleton instance
webhook_manager = WebhookManager()
