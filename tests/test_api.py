"""Tests d'intégration pour les endpoints de l'API SIRET Matcher."""
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


# ── GET /health ──────────────────────────────────────────────────────────────


async def test_health_status(api_client):
    resp = await api_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("ok", "degraded")
    assert "etablissements_actifs" in data
    assert data["etablissements_actifs"] > 0
    assert "db" in data
    assert "redis" in data
    assert "cache_size" in data


# ── GET /api/dst/siret/{siret} ───────────────────────────────────────────────


async def test_siret_lookup_valid_existing(api_client):
    resp = await api_client.get("/api/dst/siret/44306184100047")
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    for key in ("siret", "siren", "denomination", "code_naf", "opco",
                "adresse", "code_postal", "ville"):
        assert key in data, f"Clé manquante : {key}"
    assert data["opco"] != ""


async def test_siret_lookup_valid_not_found(api_client):
    resp = await api_client.get("/api/dst/siret/00000000000000")
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False


async def test_siret_lookup_invalid_short(api_client):
    resp = await api_client.get("/api/dst/siret/12345")
    assert resp.status_code == 400
    data = resp.json()
    assert "error" in data


async def test_siret_lookup_invalid_letters(api_client):
    resp = await api_client.get("/api/dst/siret/ABCDEFGHIJKLMN")
    assert resp.status_code == 400


# ── POST /match ──────────────────────────────────────────────────────────────


async def test_match_positive(api_client_with_key):
    payload = {"nom": "Google France", "code_postal": "75009", "ville": "Paris"}
    resp = await api_client_with_key.post("/match", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched"] is True
    assert data["siret"]
    assert data["score"] > 0


async def test_match_negative(api_client_with_key):
    payload = {
        "nom": "XYZQWERTY INTROUVABLE SARL",
        "code_postal": "99999",
        "ville": "Nulle Part",
    }
    resp = await api_client_with_key.post("/match", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched"] is False


async def test_match_empty_name(api_client_with_key):
    payload = {"nom": ""}
    resp = await api_client_with_key.post("/match", json=payload)
    # code_postal est requis par le modèle Pydantic → 422
    assert resp.status_code == 422


# ── POST /match/batch ────────────────────────────────────────────────────────


async def test_match_batch(api_client_with_key):
    payload = {
        "prospects": [
            {"nom": "Google France", "code_postal": "75009", "ville": "Paris"},
            {"nom": "XYZQWERTY INTROUVABLE SARL", "code_postal": "99999", "ville": "Nulle Part"},
            {"nom": "Total Energies", "code_postal": "92400", "ville": "Courbevoie"},
        ]
    }
    resp = await api_client_with_key.post("/match/batch", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["matched"] <= data["total"]
    assert len(data["results"]) == 3


# ── POST /search/prospects ───────────────────────────────────────────────────


async def test_search_prospects(api_client):
    payload = {
        "departements": ["75"],
        "taille": "PLUS_DE_50",
        "naf": "62",
        "limit": 10,
        "offset": 0,
    }
    resp = await api_client.post("/search/prospects", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] > 0
    assert len(data["results"]) > 0
    for r in data["results"]:
        assert "siret" in r


# ── GET /search/regions ──────────────────────────────────────────────────────


async def test_search_regions(api_client):
    resp = await api_client.get("/search/regions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    # Chaque région a une liste de départements
    for region, depts in data.items():
        assert isinstance(depts, list)
        assert len(depts) > 0


# ── GET /search/idcc ─────────────────────────────────────────────────────────


async def test_search_idcc(api_client):
    resp = await api_client.get("/search/idcc")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    for item in data[:5]:
        assert "idcc" in item
        assert "libelle" in item


# ── Rate limiting — IP réelle via X-Forwarded-For ─────────────────────────


def test_get_real_client_ip_without_header():
    """Sans X-Forwarded-For, utilise l'IP du socket."""
    from unittest.mock import MagicMock

    from api import get_real_client_ip

    request = MagicMock()
    request.headers = {}
    request.client.host = "127.0.0.1"
    assert get_real_client_ip(request) == "127.0.0.1"


def test_get_real_client_ip_with_forwarded_for():
    """Avec X-Forwarded-For, prend la première IP (client réel)."""
    from unittest.mock import MagicMock

    from api import get_real_client_ip

    request = MagicMock()
    request.headers = {"X-Forwarded-For": "1.2.3.4, 172.18.0.2"}
    request.client.host = "172.18.0.2"
    assert get_real_client_ip(request) == "1.2.3.4"


def test_get_real_client_ip_single_ip():
    """X-Forwarded-For avec une seule IP (pas de proxy chain)."""
    from unittest.mock import MagicMock

    from api import get_real_client_ip

    request = MagicMock()
    request.headers = {"X-Forwarded-For": "203.0.113.50"}
    request.client.host = "172.18.0.2"
    assert get_real_client_ip(request) == "203.0.113.50"
