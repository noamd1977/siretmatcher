"""Cache Redis pour les lookups SIRET.

Stratégie :
- Cache les résultats de GET /api/dst/siret/{siret} (le plus appelé)
- TTL configurable (défaut : 24h — les données SIRENE ne changent pas souvent)
- Invalidation globale lors de l'import de nouvelles données
- Fallback gracieux : si Redis est down, on tape la BDD directement (pas d'erreur)
"""
import json
import logging
import os

import redis.asyncio as aioredis

from siret_matcher.logging_config import log_structured

logger = logging.getLogger(__name__)

# Préfixe pour les clés de cache SIRET
KEY_PREFIX = "siret:"

# Compteurs internes (aussi exposés via Prometheus dans metrics.py)
_hits = 0
_misses = 0

# Client Redis (initialisé par connect())
_redis: aioredis.Redis | None = None


async def connect() -> None:
    """Initialise la connexion Redis."""
    global _redis
    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", 6379))
    db = int(os.environ.get("REDIS_DB", 0))
    try:
        _redis = aioredis.Redis(host=host, port=port, db=db, decode_responses=True)
        await _redis.ping()
        logger.info(f"Redis connecté : {host}:{port}/{db}")
    except Exception as e:
        logger.warning(f"Redis indisponible ({e}) — cache désactivé")
        _redis = None


async def close() -> None:
    """Ferme la connexion Redis."""
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


def is_connected() -> bool:
    """Retourne True si Redis est connecté."""
    return _redis is not None


async def get_cached_siret(siret: str) -> dict | None:
    """Retourne le résultat caché ou None."""
    global _hits, _misses
    if not _redis:
        _misses += 1
        return None
    try:
        data = await _redis.get(f"{KEY_PREFIX}{siret}")
        if data is not None:
            _hits += 1
            return json.loads(data)
        _misses += 1
        return None
    except Exception as e:
        logger.warning(f"Redis get error: {e}")
        _misses += 1
        return None


async def set_cached_siret(siret: str, data: dict, ttl: int | None = None) -> None:
    """Met en cache un résultat de lookup SIRET."""
    if not _redis:
        return
    if ttl is None:
        ttl = int(os.environ.get("REDIS_TTL", 86400))
    try:
        await _redis.set(f"{KEY_PREFIX}{siret}", json.dumps(data, default=str), ex=ttl)
    except Exception as e:
        logger.warning(f"Redis set error: {e}")


async def invalidate_siret(siret: str) -> None:
    """Invalide le cache pour un SIRET spécifique."""
    if not _redis:
        return
    try:
        await _redis.delete(f"{KEY_PREFIX}{siret}")
    except Exception as e:
        logger.warning(f"Redis delete error: {e}")


async def invalidate_all() -> None:
    """Invalide tout le cache SIRET (après un import de données)."""
    if not _redis:
        return
    try:
        cursor = 0
        while True:
            cursor, keys = await _redis.scan(cursor, match=f"{KEY_PREFIX}*", count=1000)
            if keys:
                await _redis.delete(*keys)
            if cursor == 0:
                break
        logger.info("Cache SIRET invalidé (toutes les clés)")
    except Exception as e:
        logger.warning(f"Redis invalidate_all error: {e}")


async def get_cache_stats() -> dict:
    """Retourne les stats du cache."""
    stats = {"hits": _hits, "misses": _misses, "connected": is_connected()}
    if _redis:
        try:
            size = 0
            cursor = 0
            while True:
                cursor, keys = await _redis.scan(cursor, match=f"{KEY_PREFIX}*", count=1000)
                size += len(keys)
                if cursor == 0:
                    break
            stats["size"] = size
        except Exception:
            stats["size"] = -1
    else:
        stats["size"] = 0
    return stats
