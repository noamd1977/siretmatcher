"""Tests du cache Redis pour les lookups SIRET."""
import asyncio

import pytest

from siret_matcher import cache as siret_cache

# Détecter si Redis est disponible
_redis_available = None


async def _check_redis():
    global _redis_available
    if _redis_available is None:
        try:
            import redis.asyncio as aioredis
            r = aioredis.Redis(host="localhost", port=6379, db=0)
            await r.ping()
            await r.aclose()
            _redis_available = True
        except Exception:
            _redis_available = False
    return _redis_available


@pytest.fixture(autouse=True)
async def ensure_redis_connected():
    """Connecte le module cache à Redis pour chaque test."""
    available = await _check_redis()
    if not available:
        pytest.skip("Redis non disponible")
    await siret_cache.connect()
    yield
    # Nettoyer les clés de test
    if siret_cache._redis:
        cursor = 0
        while True:
            cursor, keys = await siret_cache._redis.scan(cursor, match="siret:TEST_*", count=100)
            if keys:
                await siret_cache._redis.delete(*keys)
            if cursor == 0:
                break
    await siret_cache.close()


pytestmark = pytest.mark.asyncio(loop_scope="session")

# Données de test
_SAMPLE = {
    "found": True,
    "siret": "TEST_44306184100047",
    "siren": "443061841",
    "denomination": "TEST CORP",
    "opco": "ATLAS",
}

_SIRET = "TEST_44306184100047"


# ── Tests unitaires du cache ─────────────────────────────────────────────────


async def test_cache_miss():
    """get un SIRET non caché retourne None."""
    result = await siret_cache.get_cached_siret("TEST_00000000000000")
    assert result is None


async def test_cache_hit():
    """set puis get retourne les données."""
    await siret_cache.set_cached_siret(_SIRET, _SAMPLE)
    result = await siret_cache.get_cached_siret(_SIRET)
    assert result is not None
    assert result["siret"] == _SIRET
    assert result["denomination"] == "TEST CORP"


async def test_cache_ttl():
    """set avec TTL court → après expiration, get retourne None."""
    await siret_cache.set_cached_siret("TEST_TTL_SIRET", _SAMPLE, ttl=1)
    # Vérifier que c'est bien là
    result = await siret_cache.get_cached_siret("TEST_TTL_SIRET")
    assert result is not None
    # Attendre l'expiration
    await asyncio.sleep(1.5)
    result = await siret_cache.get_cached_siret("TEST_TTL_SIRET")
    assert result is None


async def test_invalidate_single():
    """set → invalidate → get retourne None."""
    await siret_cache.set_cached_siret(_SIRET, _SAMPLE)
    await siret_cache.invalidate_siret(_SIRET)
    result = await siret_cache.get_cached_siret(_SIRET)
    assert result is None


async def test_invalidate_all():
    """set plusieurs → invalidate_all → tous retournent None."""
    for i in range(3):
        await siret_cache.set_cached_siret(f"TEST_BULK_{i}", _SAMPLE)
    await siret_cache.invalidate_all()
    for i in range(3):
        result = await siret_cache.get_cached_siret(f"TEST_BULK_{i}")
        assert result is None


async def test_cache_stats():
    """get_cache_stats retourne un dict avec les bonnes clés."""
    stats = await siret_cache.get_cache_stats()
    assert "hits" in stats
    assert "misses" in stats
    assert "size" in stats
    assert "connected" in stats
    assert stats["connected"] is True


# ── Test fallback Redis down ─────────────────────────────────────────────────


async def test_fallback_redis_down():
    """Si Redis n'est pas connecté, les fonctions passent silencieusement."""
    # Sauvegarder et déconnecter
    saved_redis = siret_cache._redis
    siret_cache._redis = None

    # Aucune exception
    result = await siret_cache.get_cached_siret(_SIRET)
    assert result is None
    await siret_cache.set_cached_siret(_SIRET, _SAMPLE)
    await siret_cache.invalidate_siret(_SIRET)
    await siret_cache.invalidate_all()

    stats = await siret_cache.get_cache_stats()
    assert stats["connected"] is False
    assert stats["size"] == 0

    # Restaurer
    siret_cache._redis = saved_redis


# ── Test intégration endpoint DST ────────────────────────────────────────────


async def test_dst_endpoint_cache_hit(api_client):
    """Appeler 2 fois le même SIRET → la 2ème utilise le cache."""
    # Premier appel (cache miss → popule le cache)
    resp1 = await api_client.get("/api/dst/siret/44306184100047")
    assert resp1.status_code == 200

    # Lire les métriques avant le 2ème appel
    metrics_before = await api_client.get("/metrics")
    before_text = metrics_before.text

    def _extract_hits(text):
        for line in text.splitlines():
            if line.startswith("siret_matcher_cache_hits_total "):
                return float(line.split()[-1])
        return 0.0

    hits_before = _extract_hits(before_text)

    # Deuxième appel (devrait être un cache hit)
    resp2 = await api_client.get("/api/dst/siret/44306184100047")
    assert resp2.status_code == 200
    assert resp2.json() == resp1.json()

    metrics_after = await api_client.get("/metrics")
    hits_after = _extract_hits(metrics_after.text)
    assert hits_after > hits_before, "Le compteur cache_hits devrait avoir augmenté"
