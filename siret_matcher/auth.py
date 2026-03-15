"""Authentification par API Key."""
import hmac
import json
import logging
from pathlib import Path

from fastapi import HTTPException, Request

from siret_matcher.logging_config import log_structured

logger = logging.getLogger(__name__)

API_KEYS_PATH = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"

# {key_string: {"name": ..., "active": bool}}
_keys: dict[str, dict] = {}


def load_api_keys(path: Path = API_KEYS_PATH) -> None:
    """Charge les API keys depuis le fichier JSON."""
    global _keys
    _keys = {}
    if not path.exists():
        logger.warning(f"Fichier API keys introuvable : {path}")
        return
    try:
        data = json.loads(path.read_text())
        for entry in data.get("keys", []):
            _keys[entry["key"]] = {
                "name": entry["name"],
                "active": entry.get("active", True),
            }
        logger.info(f"API keys chargées : {len(_keys)} clés")
    except Exception as e:
        logger.error(f"Erreur chargement API keys : {e}")


def reload_api_keys() -> None:
    """Recharge les clés à chaud."""
    load_api_keys()


def get_active_keys() -> dict[str, dict]:
    """Retourne les clés chargées (pour les tests)."""
    return _keys


async def require_api_key(request: Request) -> str:
    """Dépendance FastAPI : vérifie le header X-API-Key.

    Retourne le nom associé à la clé.
    Lève 401 si absent, 403 si invalide/désactivé.
    """
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        log_structured(
            logger, logging.WARNING, "auth_failed",
            reason="missing_api_key",
            path=request.url.path,
        )
        raise HTTPException(status_code=401, detail="API key manquante. Ajoutez le header X-API-Key.")

    # Comparaison en temps constant pour chaque clé
    for stored_key, info in _keys.items():
        if hmac.compare_digest(api_key, stored_key):
            if not info["active"]:
                log_structured(
                    logger, logging.WARNING, "auth_failed",
                    reason="disabled_key", key_name=info["name"],
                    path=request.url.path,
                )
                raise HTTPException(status_code=403, detail="API key désactivée.")
            return info["name"]

    log_structured(
        logger, logging.WARNING, "auth_failed",
        reason="invalid_key",
        path=request.url.path,
    )
    raise HTTPException(status_code=403, detail="API key invalide.")


# Charger au premier import
load_api_keys()
