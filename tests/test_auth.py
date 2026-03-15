"""Tests d'authentification par API Key."""
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


# ── POST /match — authentification ───────────────────────────────────────────


async def test_match_without_api_key_returns_401(api_client):
    """POST /match sans header X-API-Key retourne 401."""
    payload = {"nom": "Google France", "code_postal": "75009", "ville": "Paris"}
    resp = await api_client.post("/match", json=payload)
    assert resp.status_code == 401
    data = resp.json()
    assert "detail" in data


async def test_match_with_invalid_api_key_returns_403(api_client):
    """POST /match avec une API key invalide retourne 403."""
    payload = {"nom": "Google France", "code_postal": "75009", "ville": "Paris"}
    resp = await api_client.post(
        "/match", json=payload,
        headers={"X-API-Key": "cette-cle-nexiste-pas-du-tout"},
    )
    assert resp.status_code == 403
    data = resp.json()
    assert "detail" in data


async def test_match_with_valid_api_key_returns_200(api_client_with_key):
    """POST /match avec API key valide fonctionne."""
    payload = {"nom": "Google France", "code_postal": "75009", "ville": "Paris"}
    resp = await api_client_with_key.post("/match", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "matched" in data


# ── POST /match/batch — authentification ─────────────────────────────────────


async def test_batch_without_api_key_returns_401(api_client):
    """POST /match/batch sans API key retourne 401."""
    payload = {
        "prospects": [
            {"nom": "Google France", "code_postal": "75009", "ville": "Paris"},
        ]
    }
    resp = await api_client.post("/match/batch", json=payload)
    assert resp.status_code == 401


async def test_batch_with_valid_api_key_returns_200(api_client_with_key):
    """POST /match/batch avec API key valide fonctionne."""
    payload = {
        "prospects": [
            {"nom": "Google France", "code_postal": "75009", "ville": "Paris"},
        ]
    }
    resp = await api_client_with_key.post("/match/batch", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1


# ── Endpoints publics — pas d'API key requise ────────────────────────────────


async def test_dst_siret_public_no_key(api_client):
    """GET /api/dst/siret reste public, pas besoin d'API key."""
    resp = await api_client.get("/api/dst/siret/44306184100047")
    assert resp.status_code == 200


async def test_health_public_no_key(api_client):
    """GET /health reste public."""
    resp = await api_client.get("/health")
    assert resp.status_code == 200


async def test_metrics_public_no_key(api_client):
    """GET /metrics reste public."""
    resp = await api_client.get("/metrics")
    assert resp.status_code == 200


async def test_search_prospects_public_no_key(api_client):
    """POST /search/prospects reste public."""
    payload = {
        "departements": ["75"],
        "taille": "PLUS_DE_50",
        "naf": "62",
        "limit": 5,
        "offset": 0,
    }
    resp = await api_client.post("/search/prospects", json=payload)
    assert resp.status_code == 200
