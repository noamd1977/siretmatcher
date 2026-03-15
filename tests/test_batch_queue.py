"""Tests du systeme de file d'attente batch asynchrone."""
import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


# ══════════════════════════════════════════════════════════════════════════════
# Unit tests (BatchQueue with mocked Redis)
# ══════════════════════════════════════════════════════════════════════════════


class FakeRedis:
    """Minimal in-memory Redis mock for testing."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._lists: dict[str, list[str]] = {}
        self._expiry: dict[str, int] = {}

    async def set(self, key, value, ex=None):
        self._store[key] = value
        if ex:
            self._expiry[key] = ex

    async def get(self, key):
        return self._store.get(key)

    async def delete(self, *keys):
        for k in keys:
            self._store.pop(k, None)
            self._lists.pop(k, None)

    async def rpush(self, key, *values):
        if key not in self._lists:
            self._lists[key] = []
        self._lists[key].extend(values)

    async def lrange(self, key, start, end):
        lst = self._lists.get(key, [])
        if end == -1:
            return lst[start:]
        return lst[start:end + 1]

    async def expire(self, key, ttl):
        self._expiry[key] = ttl

    async def scan(self, cursor, match=None, count=None):
        import fnmatch
        keys = list(self._store.keys())
        if match:
            keys = [k for k in keys if fnmatch.fnmatch(k, match)]
        return (0, keys)

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._ops: list = []

    def rpush(self, key, *values):
        self._ops.append(("rpush", key, values))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    async def execute(self):
        for op in self._ops:
            if op[0] == "rpush":
                await self._redis.rpush(op[1], *op[2])
            elif op[0] == "expire":
                await self._redis.expire(op[1], op[2])
        self._ops.clear()


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def batch_queue(fake_redis):
    from siret_matcher.queue import BatchQueue
    from siret_matcher import cache as siret_cache

    # Patch Redis
    original = siret_cache._redis
    siret_cache._redis = fake_redis

    q = BatchQueue()
    yield q

    siret_cache._redis = original


class TestEnqueue:
    @pytest.mark.asyncio
    async def test_enqueue_returns_job_id(self, batch_queue):
        """enqueue retourne un UUID."""
        job_id = await batch_queue.enqueue([
            {"nom": "Test", "code_postal": "75001"},
        ])
        assert job_id
        assert len(job_id) == 36  # UUID format

    @pytest.mark.asyncio
    async def test_enqueue_stores_job(self, batch_queue):
        """Le job est stocke dans Redis."""
        job_id = await batch_queue.enqueue([
            {"nom": "A", "code_postal": "75001"},
            {"nom": "B", "code_postal": "69001"},
        ])
        job = await batch_queue.get_job(job_id)
        assert job is not None
        assert job.status == "queued"
        assert job.total == 2
        assert job.processed == 0

    @pytest.mark.asyncio
    async def test_enqueue_stores_prospects(self, batch_queue, fake_redis):
        """Les prospects sont mis en file Redis."""
        job_id = await batch_queue.enqueue([
            {"nom": "X", "code_postal": "75001"},
            {"nom": "Y", "code_postal": "69001"},
            {"nom": "Z", "code_postal": "13001"},
        ])
        queue_key = f"batch:queue:{job_id}"
        items = await fake_redis.lrange(queue_key, 0, -1)
        assert len(items) == 3


class TestJobStatus:
    @pytest.mark.asyncio
    async def test_get_unknown_job(self, batch_queue):
        """Un job inexistant retourne None."""
        job = await batch_queue.get_job("nonexistent-id")
        assert job is None

    @pytest.mark.asyncio
    async def test_job_progress(self, batch_queue):
        """Le pourcentage est calcule correctement."""
        job_id = await batch_queue.enqueue([
            {"nom": f"P{i}", "code_postal": "75001"} for i in range(10)
        ])
        job = await batch_queue.get_job(job_id)
        assert job.percent == 0

        # Simulate progress
        job.processed = 5
        job.total = 10
        assert job.percent == 50.0


class TestCallback:
    @pytest.mark.asyncio
    async def test_callback_url_stored(self, batch_queue):
        """Le callback_url est conserve dans le job."""
        job_id = await batch_queue.enqueue(
            [{"nom": "Test", "code_postal": "75001"}],
            callback_url="https://example.com/callback",
        )
        job = await batch_queue.get_job(job_id)
        assert job.callback_url == "https://example.com/callback"


# ══════════════════════════════════════════════════════════════════════════════
# Integration tests (API endpoints)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestBatchAPI:
    async def test_create_batch_returns_202(self, api_client_with_key):
        """POST /api/v3/batch retourne 202 avec job_id."""
        resp = await api_client_with_key.post("/api/v3/batch", json={
            "prospects": [
                {"nom": "Google France", "code_postal": "75009"},
                {"nom": "Microsoft France", "code_postal": "92130"},
            ],
            "concurrency": 5,
        })
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "queued"
        assert data["total"] == 2
        assert "status_url" in data

    async def test_get_batch_status(self, api_client_with_key):
        """GET /api/v3/batch/{job_id} retourne le statut."""
        # Create job first
        resp = await api_client_with_key.post("/api/v3/batch", json={
            "prospects": [{"nom": "Test", "code_postal": "75001"}],
        })
        job_id = resp.json()["job_id"]

        # Check status
        resp = await api_client_with_key.get(f"/api/v3/batch/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job_id
        assert "progress" in data

    async def test_batch_processing(self, api_client_with_key):
        """Le worker traite les prospects (petit batch)."""
        resp = await api_client_with_key.post("/api/v3/batch", json={
            "prospects": [
                {"nom": "Google France", "code_postal": "75009"},
                {"nom": "Microsoft France", "code_postal": "92130"},
                {"nom": "Apple France", "code_postal": "75008"},
                {"nom": "Amazon France", "code_postal": "92100"},
                {"nom": "Meta France", "code_postal": "75002"},
            ],
            "concurrency": 5,
        })
        job_id = resp.json()["job_id"]

        # Wait for processing (max 30s)
        for _ in range(15):
            await asyncio.sleep(2)
            resp = await api_client_with_key.get(f"/api/v3/batch/{job_id}")
            data = resp.json()
            if data["status"] in ("completed", "failed"):
                break

        assert data["status"] == "completed"
        assert data["progress"]["processed"] == 5
        assert data["progress"]["total"] == 5

    async def test_batch_results(self, api_client_with_key):
        """Les resultats sont disponibles apres traitement."""
        resp = await api_client_with_key.post("/api/v3/batch", json={
            "prospects": [
                {"nom": "Google France", "code_postal": "75009"},
            ],
        })
        job_id = resp.json()["job_id"]

        # Wait for completion
        for _ in range(15):
            await asyncio.sleep(2)
            resp = await api_client_with_key.get(f"/api/v3/batch/{job_id}")
            if resp.json()["status"] == "completed":
                break

        # Get results
        resp = await api_client_with_key.get(f"/api/v3/batch/{job_id}/results")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert len(data["results"]) >= 1

    async def test_batch_csv_export(self, api_client_with_key):
        """Le CSV est bien forme."""
        resp = await api_client_with_key.post("/api/v3/batch", json={
            "prospects": [
                {"nom": "Google France", "code_postal": "75009"},
            ],
        })
        job_id = resp.json()["job_id"]

        # Wait for completion
        for _ in range(15):
            await asyncio.sleep(2)
            resp = await api_client_with_key.get(f"/api/v3/batch/{job_id}")
            if resp.json()["status"] == "completed":
                break

        # Download CSV
        resp = await api_client_with_key.get(f"/api/v3/batch/{job_id}/results.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        lines = resp.text.strip().split("\n")
        assert len(lines) >= 2  # header + at least 1 row
        assert "prospect_nom" in lines[0]
        assert "siret" in lines[0]

    async def test_unknown_job_404(self, api_client_with_key):
        """GET /api/v3/batch/{unknown} retourne 404."""
        resp = await api_client_with_key.get("/api/v3/batch/nonexistent-job-id")
        assert resp.status_code == 404

    async def test_batch_requires_auth(self, api_client):
        """POST /api/v3/batch requiert une API key."""
        resp = await api_client.post("/api/v3/batch", json={
            "prospects": [{"nom": "Test", "code_postal": "75001"}],
        })
        assert resp.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestSyncBatchLimit:
    async def test_sync_batch_over_50_rejected(self, api_client_with_key):
        """POST /match/batch avec > 50 prospects → 400."""
        prospects = [{"nom": f"P{i}", "code_postal": "75001"} for i in range(51)]
        resp = await api_client_with_key.post("/api/v3/match/batch", json={
            "prospects": prospects,
            "concurrency": 5,
        })
        assert resp.status_code == 400
        assert "50" in resp.json()["detail"]
        assert "/api/v3/batch" in resp.json()["detail"]
