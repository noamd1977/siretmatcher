"""File d'attente pour le matching batch asynchrone.

Flow :
1. Client envoie POST /api/v3/batch avec les prospects
2. L'API retourne un job_id immediatement (202 Accepted)
3. Les prospects sont mis en file d'attente Redis
4. Un worker traite les prospects en arriere-plan
5. Le client peut verifier l'avancement via GET /api/v3/batch/{job_id}
6. Quand c'est fini, le webhook match.batch_complete est emis
"""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import httpx

from siret_matcher import cache as siret_cache
from siret_matcher.logging_config import log_structured
from siret_matcher.metrics import (
    BATCH_JOBS_TOTAL,
    BATCH_JOBS_ACTIVE,
    BATCH_PROSPECTS_PROCESSED,
    BATCH_DURATION,
)

logger = logging.getLogger("siret_matcher.queue")

_JOB_PREFIX = "batch:job:"
_QUEUE_PREFIX = "batch:queue:"
_RESULTS_PREFIX = "batch:results:"
_JOB_TTL = 86400  # 24h


@dataclass
class BatchJob:
    job_id: str
    status: str = "queued"  # queued, processing, completed, failed
    total: int = 0
    processed: int = 0
    matched: int = 0
    not_found: int = 0
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    callback_url: str | None = None
    webhook_events: bool = True
    concurrency: int = 5
    error: str | None = None
    duration_seconds: float | None = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def percent(self) -> float:
        return round(self.processed / self.total * 100, 1) if self.total else 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["percent"] = self.percent
        return d


class BatchQueue:
    def __init__(self):
        self._running = False
        self._db = None
        self._http_client: httpx.AsyncClient | None = None
        self._pool = None

    def set_deps(self, db, http_client: httpx.AsyncClient, pool):
        """Set dependencies from app startup."""
        self._db = db
        self._http_client = http_client
        self._pool = pool

    async def enqueue(
        self,
        prospects: list[dict],
        concurrency: int = 5,
        callback_url: str | None = None,
        webhook_events: bool = True,
    ) -> str:
        """Create a job and queue prospects. Returns job_id."""
        if not siret_cache.is_connected():
            raise RuntimeError("Redis required for async batch")

        job_id = str(uuid.uuid4())
        job = BatchJob(
            job_id=job_id,
            total=len(prospects),
            concurrency=concurrency,
            callback_url=callback_url,
            webhook_events=webhook_events,
        )

        redis = siret_cache._redis

        # Store job metadata
        await redis.set(
            f"{_JOB_PREFIX}{job_id}",
            json.dumps(job.to_dict()),
            ex=_JOB_TTL,
        )

        # Push prospects to queue (as a list)
        pipe = redis.pipeline()
        for p in prospects:
            pipe.rpush(f"{_QUEUE_PREFIX}{job_id}", json.dumps(p))
        pipe.expire(f"{_QUEUE_PREFIX}{job_id}", _JOB_TTL)
        await pipe.execute()

        # Initialize empty results list
        await redis.delete(f"{_RESULTS_PREFIX}{job_id}")

        BATCH_JOBS_TOTAL.inc()

        log_structured(
            logger, logging.INFO, "batch_enqueued",
            job_id=job_id, total=len(prospects), concurrency=concurrency,
        )

        return job_id

    async def get_job(self, job_id: str) -> BatchJob | None:
        """Get job status from Redis."""
        if not siret_cache.is_connected():
            return None
        redis = siret_cache._redis
        data = await redis.get(f"{_JOB_PREFIX}{job_id}")
        if not data:
            return None
        d = json.loads(data)
        return BatchJob(
            job_id=d["job_id"],
            status=d["status"],
            total=d["total"],
            processed=d["processed"],
            matched=d["matched"],
            not_found=d["not_found"],
            created_at=d["created_at"],
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            callback_url=d.get("callback_url"),
            webhook_events=d.get("webhook_events", True),
            concurrency=d.get("concurrency", 5),
            error=d.get("error"),
            duration_seconds=d.get("duration_seconds"),
        )

    async def _update_job(self, job: BatchJob):
        """Persist job state to Redis."""
        if not siret_cache.is_connected():
            return
        redis = siret_cache._redis
        await redis.set(
            f"{_JOB_PREFIX}{job.job_id}",
            json.dumps(job.to_dict()),
            ex=_JOB_TTL,
        )

    async def get_results(self, job_id: str, offset: int = 0, limit: int = 1000) -> list[dict]:
        """Get paginated results for a job."""
        if not siret_cache.is_connected():
            return []
        redis = siret_cache._redis
        raw = await redis.lrange(f"{_RESULTS_PREFIX}{job_id}", offset, offset + limit - 1)
        return [json.loads(r) for r in raw]

    async def get_all_results(self, job_id: str) -> list[dict]:
        """Get all results for a job."""
        if not siret_cache.is_connected():
            return []
        redis = siret_cache._redis
        raw = await redis.lrange(f"{_RESULTS_PREFIX}{job_id}", 0, -1)
        return [json.loads(r) for r in raw]

    async def _process_job(self, job_id: str):
        """Process all prospects in a job."""
        from siret_matcher.matcher import match_one
        from siret_matcher.models import Prospect
        from siret_matcher.api_v3 import _build_etablissement, _compute_lead_score, _confidence
        from siret_matcher.lookups import NAF_TO_OPCO

        job = await self.get_job(job_id)
        if not job:
            return

        job.status = "processing"
        job.started_at = datetime.now(timezone.utc).isoformat()
        await self._update_job(job)
        BATCH_JOBS_ACTIVE.inc()

        redis = siret_cache._redis
        sem = asyncio.Semaphore(job.concurrency)
        t0 = time.perf_counter()

        async def _match_prospect(prospect_json: str) -> dict:
            async with sem:
                try:
                    p_data = json.loads(prospect_json)
                    prospect = Prospect(
                        nom=p_data.get("nom", ""),
                        adresse=p_data.get("adresse", ""),
                        code_postal=p_data.get("code_postal", ""),
                        ville=p_data.get("ville", ""),
                        telephone=p_data.get("telephone", ""),
                        site_web=p_data.get("site_web", ""),
                        email=p_data.get("email", ""),
                    )

                    result = await match_one(self._http_client, self._db, prospect, use_db=True)
                    r = result.result

                    if r and r.siret and r.methode != "NON_TROUVE":
                        # Verify active
                        async with self._pool.acquire() as conn:
                            check = await conn.fetchval(
                                "SELECT siret FROM etablissements WHERE siret = $1 AND etat_administratif = 'A'",
                                r.siret,
                            )
                        if not check:
                            r = None

                    if r and r.siret and r.methode != "NON_TROUVE":
                        return {
                            "matched": True,
                            "score": r.score,
                            "methode": r.methode,
                            "siret": r.siret,
                            "siren": r.siren,
                            "denomination": r.denomination,
                            "prospect_nom": p_data.get("nom", ""),
                        }
                    else:
                        return {
                            "matched": False,
                            "score": 0,
                            "methode": "NON_TROUVE",
                            "prospect_nom": p_data.get("nom", ""),
                        }

                except Exception as e:
                    logger.debug("batch prospect error: %s", e)
                    return {
                        "matched": False,
                        "score": 0,
                        "methode": "ERREUR",
                        "error": str(e),
                    }

        try:
            # Get all prospects from queue
            prospects_raw = await redis.lrange(f"{_QUEUE_PREFIX}{job_id}", 0, -1)

            # Process in chunks for progress updates
            chunk_size = max(job.concurrency * 2, 10)
            for i in range(0, len(prospects_raw), chunk_size):
                chunk = prospects_raw[i:i + chunk_size]
                results = await asyncio.gather(
                    *[_match_prospect(p) for p in chunk]
                )

                # Store results and update progress
                pipe = redis.pipeline()
                for res in results:
                    pipe.rpush(f"{_RESULTS_PREFIX}{job_id}", json.dumps(res))
                    if res["matched"]:
                        job.matched += 1
                    else:
                        job.not_found += 1
                    job.processed += 1
                    BATCH_PROSPECTS_PROCESSED.inc()
                pipe.expire(f"{_RESULTS_PREFIX}{job_id}", _JOB_TTL)
                await pipe.execute()
                await self._update_job(job)

            # Job complete
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc).isoformat()
            job.duration_seconds = round(time.perf_counter() - t0, 1)
            await self._update_job(job)

            BATCH_DURATION.observe(job.duration_seconds)

            log_structured(
                logger, logging.INFO, "batch_completed",
                job_id=job_id, total=job.total, matched=job.matched,
                duration_s=job.duration_seconds,
            )

            # Emit webhook
            if job.webhook_events:
                try:
                    from siret_matcher.webhooks import webhook_manager
                    await webhook_manager.emit("match.batch_complete", {
                        "job_id": job_id,
                        "total": job.total,
                        "matched": job.matched,
                        "taux": round(job.matched / job.total, 4) if job.total else 0,
                        "duration_seconds": job.duration_seconds,
                        "results_summary": {
                            "matched": job.matched,
                            "not_found": job.not_found,
                        },
                    })
                except Exception:
                    pass

            # Call callback URL
            if job.callback_url:
                try:
                    client = self._http_client or httpx.AsyncClient()
                    await client.post(
                        job.callback_url,
                        json={
                            "job_id": job_id,
                            "status": "completed",
                            "total": job.total,
                            "matched": job.matched,
                            "not_found": job.not_found,
                            "duration_seconds": job.duration_seconds,
                            "results_url": f"/api/v3/batch/{job_id}/results",
                        },
                        timeout=10,
                    )
                except Exception as e:
                    logger.warning("callback failed for job %s: %s", job_id, e)

        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            job.completed_at = datetime.now(timezone.utc).isoformat()
            await self._update_job(job)
            logger.error("batch job %s failed: %s", job_id, e)

        finally:
            BATCH_JOBS_ACTIVE.dec()
            # Clean up queue (results kept for TTL)
            await redis.delete(f"{_QUEUE_PREFIX}{job_id}")

    async def start_worker(self, concurrency: int = 5):
        """Background worker that polls for queued jobs."""
        self._running = True
        logger.info("Batch worker started")

        while self._running:
            try:
                if not siret_cache.is_connected():
                    await asyncio.sleep(5)
                    continue

                redis = siret_cache._redis
                # Scan for queued jobs
                cursor = 0
                found_job = False
                while True:
                    cursor, keys = await redis.scan(cursor, match=f"{_JOB_PREFIX}*", count=50)
                    for key in keys:
                        data = await redis.get(key)
                        if not data:
                            continue
                        job_data = json.loads(data)
                        if job_data.get("status") in ("queued", "processing"):
                            found_job = True
                            await self._process_job(job_data["job_id"])
                    if cursor == 0:
                        break

                if not found_job:
                    await asyncio.sleep(2)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("batch worker error: %s", e)
                await asyncio.sleep(5)

        logger.info("Batch worker stopped")

    def stop(self):
        self._running = False


# Singleton
batch_queue = BatchQueue()
