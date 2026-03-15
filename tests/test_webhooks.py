"""Tests du systeme de webhook."""
import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from siret_matcher.webhooks import WebhookManager, WebhookConfig


def _make_config(webhooks: list[dict]) -> str:
    """Cree un fichier config temporaire et retourne son chemin."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"webhooks": webhooks}, f)
    f.close()
    return f.name


def _default_webhook(**overrides) -> dict:
    base = {
        "id": "test-wh",
        "name": "Test Webhook",
        "url": "https://example.com/hook",
        "events": ["match.success"],
        "active": True,
        "retry": 3,
        "timeout": 5,
    }
    base.update(overrides)
    return base


# ══════════════════════════════════════════════════════════════════════════════
# Emission basique
# ══════════════════════════════════════════════════════════════════════════════


class TestEmit:
    @pytest.mark.asyncio
    async def test_emit_calls_webhook(self):
        """Un webhook abonne recoit le bon payload."""
        path = _make_config([_default_webhook()])
        mgr = WebhookManager(config_path=path)

        mock_response = httpx.Response(200, request=httpx.Request("POST", "https://example.com/hook"))
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mgr.set_client(mock_client)

        await mgr.emit("match.success", {"siret": "12345678901234"})

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert body["event"] == "match.success"
        assert body["data"]["siret"] == "12345678901234"

        os.unlink(path)

    @pytest.mark.asyncio
    async def test_emit_payload_structure(self):
        """Le payload contient event, timestamp et data."""
        path = _make_config([_default_webhook()])
        mgr = WebhookManager(config_path=path)

        captured = {}
        async def capture_post(url, **kwargs):
            captured.update(kwargs.get("json", {}))
            return httpx.Response(200, request=httpx.Request("POST", url))

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = capture_post
        mgr.set_client(mock_client)

        await mgr.emit("match.success", {"key": "value"})

        assert "event" in captured
        assert "timestamp" in captured
        assert "data" in captured
        assert captured["data"]["key"] == "value"

        os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# Filtrage par event
# ══════════════════════════════════════════════════════════════════════════════


class TestEventFiltering:
    @pytest.mark.asyncio
    async def test_only_subscribed_events(self):
        """Un webhook abonne a match.success ne recoit pas enrich.complete."""
        path = _make_config([_default_webhook(events=["match.success"])])
        mgr = WebhookManager(config_path=path)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("POST", "x")))
        mgr.set_client(mock_client)

        await mgr.emit("enrich.complete", {"siret": "123"})

        mock_client.post.assert_not_called()

        os.unlink(path)

    @pytest.mark.asyncio
    async def test_multiple_events(self):
        """Un webhook abonne a plusieurs events recoit les deux."""
        path = _make_config([_default_webhook(events=["match.success", "enrich.complete"])])
        mgr = WebhookManager(config_path=path)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("POST", "x")))
        mgr.set_client(mock_client)

        await mgr.emit("match.success", {})
        await mgr.emit("enrich.complete", {})

        assert mock_client.post.call_count == 2

        os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# Retry
# ══════════════════════════════════════════════════════════════════════════════


class TestRetry:
    @pytest.mark.asyncio
    async def test_retry_on_500(self):
        """Le webhook est reessaye sur erreur 500."""
        path = _make_config([_default_webhook(retry=3)])
        mgr = WebhookManager(config_path=path)

        call_count = 0
        async def failing_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            return httpx.Response(500, request=httpx.Request("POST", url))

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = failing_post
        mgr.set_client(mock_client)

        # Patch sleep to avoid real delays
        with patch("siret_matcher.webhooks.asyncio.sleep", new_callable=AsyncMock):
            await mgr.emit("match.success", {})

        assert call_count == 3

        os.unlink(path)

    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        """Le webhook reussit apres un echec."""
        path = _make_config([_default_webhook(retry=3)])
        mgr = WebhookManager(config_path=path)

        call_count = 0
        async def retry_then_ok(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return httpx.Response(500, request=httpx.Request("POST", url))
            return httpx.Response(200, request=httpx.Request("POST", url))

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = retry_then_ok
        mgr.set_client(mock_client)

        with patch("siret_matcher.webhooks.asyncio.sleep", new_callable=AsyncMock):
            await mgr.emit("match.success", {})

        assert call_count == 2
        # Check log shows success
        assert mgr.log_entries[-1].status == "success"

        os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# Timeout
# ══════════════════════════════════════════════════════════════════════════════


class TestTimeout:
    @pytest.mark.asyncio
    async def test_timeout_does_not_block(self):
        """Un timeout ne bloque pas l'appelant."""
        path = _make_config([_default_webhook(retry=1, timeout=1)])
        mgr = WebhookManager(config_path=path)

        async def timeout_post(url, **kwargs):
            raise httpx.TimeoutException("Connection timed out")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = timeout_post
        mgr.set_client(mock_client)

        # Should not raise
        await mgr.emit("match.success", {})

        assert len(mgr.log_entries) == 1
        assert mgr.log_entries[0].status == "error"
        assert mgr.log_entries[0].error == "timeout"

        os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# Config reload
# ══════════════════════════════════════════════════════════════════════════════


class TestConfigReload:
    def test_reload_updates_webhooks(self):
        """Modifier le JSON puis recharger met a jour la liste."""
        path = _make_config([_default_webhook(id="wh-1")])
        mgr = WebhookManager(config_path=path)
        assert len(mgr.webhooks) == 1
        assert mgr.webhooks[0].id == "wh-1"

        # Update config
        with open(path, "w") as f:
            json.dump({"webhooks": [
                _default_webhook(id="wh-1"),
                _default_webhook(id="wh-2", url="https://other.com/hook"),
            ]}, f)

        mgr.reload_config()
        assert len(mgr.webhooks) == 2
        assert {w.id for w in mgr.webhooks} == {"wh-1", "wh-2"}

        os.unlink(path)

    def test_missing_config_file(self):
        """Un fichier config inexistant donne 0 webhooks."""
        mgr = WebhookManager(config_path="/tmp/nonexistent_webhooks.json")
        assert len(mgr.webhooks) == 0


# ══════════════════════════════════════════════════════════════════════════════
# Webhook inactif
# ══════════════════════════════════════════════════════════════════════════════


class TestInactiveWebhook:
    @pytest.mark.asyncio
    async def test_inactive_not_called(self):
        """Un webhook avec active=false n'est pas appele."""
        path = _make_config([_default_webhook(active=False)])
        mgr = WebhookManager(config_path=path)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("POST", "x")))
        mgr.set_client(mock_client)

        await mgr.emit("match.success", {})

        mock_client.post.assert_not_called()

        os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# Endpoint /test
# ══════════════════════════════════════════════════════════════════════════════


class TestSendTest:
    @pytest.mark.asyncio
    async def test_send_test_payload(self):
        """send_test envoie un payload de test."""
        path = _make_config([_default_webhook(id="my-wh")])
        mgr = WebhookManager(config_path=path)

        captured = {}
        async def capture_post(url, **kwargs):
            captured.update(kwargs.get("json", {}))
            return httpx.Response(200, request=httpx.Request("POST", url))

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = capture_post
        mgr.set_client(mock_client)

        result = await mgr.send_test("my-wh")

        assert result["success"] is True
        assert result["status_code"] == 200
        assert captured["event"] == "test"
        assert captured["data"]["test"] is True

        os.unlink(path)

    @pytest.mark.asyncio
    async def test_send_test_unknown_webhook(self):
        """send_test retourne une erreur pour un id inconnu."""
        path = _make_config([])
        mgr = WebhookManager(config_path=path)

        result = await mgr.send_test("unknown")
        assert "error" in result

        os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# Log
# ══════════════════════════════════════════════════════════════════════════════


class TestLog:
    @pytest.mark.asyncio
    async def test_log_entries_recorded(self):
        """Les envois sont logues."""
        path = _make_config([_default_webhook()])
        mgr = WebhookManager(config_path=path)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("POST", "x")))
        mgr.set_client(mock_client)

        await mgr.emit("match.success", {})

        assert len(mgr.log_entries) == 1
        entry = mgr.log_entries[0]
        assert entry.webhook_id == "test-wh"
        assert entry.event == "match.success"
        assert entry.status == "success"

        os.unlink(path)

    @pytest.mark.asyncio
    async def test_log_max_entries(self):
        """Le log est tronque a max_log entrees."""
        path = _make_config([_default_webhook()])
        mgr = WebhookManager(config_path=path)
        mgr._max_log = 5

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("POST", "x")))
        mgr.set_client(mock_client)

        for _ in range(10):
            await mgr.emit("match.success", {})

        assert len(mgr.log_entries) == 5

        os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# Integration API endpoints
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestWebhookAPI:
    async def test_list_webhooks(self, api_client_with_key):
        """GET /api/v3/webhooks retourne une liste."""
        resp = await api_client_with_key.get("/api/v3/webhooks")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_reload_webhooks(self, api_client_with_key):
        """POST /api/v3/webhooks/reload retourne ok."""
        resp = await api_client_with_key.post("/api/v3/webhooks/reload")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    async def test_webhook_log(self, api_client_with_key):
        """GET /api/v3/webhooks/log retourne une liste."""
        resp = await api_client_with_key.get("/api/v3/webhooks/log")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_webhooks_require_auth(self, api_client):
        """Les endpoints webhooks requierent une API key."""
        resp = await api_client.get("/api/v3/webhooks")
        assert resp.status_code == 401
