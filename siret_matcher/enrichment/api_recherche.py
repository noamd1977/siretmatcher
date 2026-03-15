"""Enrichissement via l'API Recherche Entreprises (gouv.fr).

Appel léger pour récupérer dirigeant, catégorie, nature juridique, nb établissements.
Cache Redis TTL 7 jours.
"""
import json
import logging

import httpx

from siret_matcher import cache as siret_cache

logger = logging.getLogger(__name__)

_API_BASE = "https://recherche-entreprises.api.gouv.fr/search"
_CACHE_PREFIX = "enrich:api:"
_CACHE_TTL = 7 * 86400  # 7 jours


async def enrich_from_api(client: httpx.AsyncClient, siren: str) -> dict:
    """Appelle l'API Recherche Entreprises pour enrichir un SIREN.

    Returns:
        {
            "dirigeant_nom": "...", "dirigeant_prenom": "...", "dirigeant_fonction": "...",
            "categorie_entreprise": "ETI",
            "nature_juridique": "5499",
            "nombre_etablissements": 15,
            "effectif_unite_legale": "42",
        }
        ou {} si erreur.
    """
    # Check cache
    if siret_cache.is_connected():
        try:
            cached = await siret_cache._redis.get(f"{_CACHE_PREFIX}{siren}")
            if cached is not None:
                return json.loads(cached)
        except Exception:
            pass

    try:
        resp = await client.get(
            _API_BASE,
            params={"q": siren, "per_page": "1"},
            timeout=10,
        )
        if resp.status_code != 200:
            return {}

        results = resp.json().get("results") or []
        if not results:
            return {}

        r = results[0]
        # Vérifier que le SIREN correspond
        if r.get("siren") != siren:
            return {}

        # Dirigeant principal (personne physique)
        dirigeants = r.get("dirigeants") or []
        dir_nom, dir_prenom, dir_fonction = "", "", ""
        for d in dirigeants:
            if d.get("type_dirigeant") == "personne physique":
                dir_nom = d.get("nom", "")
                dir_prenom = d.get("prenoms", "")
                dir_fonction = d.get("qualite", "")
                break

        result = {
            "dirigeant_nom": dir_nom,
            "dirigeant_prenom": dir_prenom,
            "dirigeant_fonction": dir_fonction,
            "categorie_entreprise": r.get("categorie_entreprise") or "",
            "nature_juridique": r.get("nature_juridique") or "",
            "nombre_etablissements": r.get("nombre_etablissements_ouverts") or 0,
            "effectif_unite_legale": r.get("tranche_effectif_salarie") or "",
        }

    except Exception as e:
        logger.debug(f"API Recherche enrichment error for {siren}: {e}")
        return {}

    # Cache
    if siret_cache.is_connected():
        try:
            await siret_cache._redis.set(
                f"{_CACHE_PREFIX}{siren}", json.dumps(result), ex=_CACHE_TTL
            )
        except Exception:
            pass

    return result
