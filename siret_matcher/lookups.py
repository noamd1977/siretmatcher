"""Point d'entrée unique pour tous les dictionnaires de référence.

Les données sont stockées en fichiers JSON dans config/data/ et chargées
une seule fois au niveau module. Appeler reload_lookups() pour recharger
à chaud sans redémarrer le processus.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "config" / "data"

# Registre : nom de variable → nom du fichier JSON (sans extension)
_REGISTRY: dict[str, str] = {
    "DEPT_TO_REGION": "dept_to_region",
    "IDCC_TO_CONVENTION": "idcc_to_convention",
    "NAF_LIBELLES": "naf_libelles",
    "NAF_TO_OPCO": "naf_to_opco",
    "ENSEIGNE_TO_OPCO": "enseigne_to_opco",
    "TRANCHE_MAP": "tranche_map",
    "NATURE_JURIDIQUE": "nature_juridique",
}


def _load(filename: str) -> dict:
    """Charge un fichier JSON depuis config/data/. Retourne {} si absent."""
    path = _DATA_DIR / f"{filename}.json"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("Fichier lookup manquant : %s — dictionnaire vide utilisé", path)
        return {}


def reload_lookups() -> None:
    """Recharge tous les dictionnaires depuis les fichiers JSON."""
    g = globals()
    for var_name, filename in _REGISTRY.items():
        g[var_name] = _load(filename)


# --- Chargement initial ---
DEPT_TO_REGION: dict[str, str] = {}
IDCC_TO_CONVENTION: dict[str, str] = {}
NAF_LIBELLES: dict[str, str] = {}
NAF_TO_OPCO: dict[str, str] = {}
ENSEIGNE_TO_OPCO: dict[str, str] = {}
TRANCHE_MAP: dict[str, str] = {}
NATURE_JURIDIQUE: dict[str, str] = {}

reload_lookups()
