"""Enrichissement financier via scraping Pappers.fr.

Scrape la page publique d'une entreprise pour extraire CA, résultat net
et date des derniers comptes publiés. Cache Redis TTL 30 jours.
"""
import json
import logging
import re

import httpx

from siret_matcher import cache as siret_cache

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "enrich:pappers:"
_CACHE_TTL = 30 * 86400  # 30 jours

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def _extract_financial(html: str) -> dict:
    """Extrait CA, résultat net et date des comptes depuis le HTML Pappers."""
    result = {}

    # Chiffre d'affaires — pattern: "Chiffre d'affaires" suivi d'un montant
    ca_patterns = [
        r"Chiffre\s+d['']affaires\s*(?:</[^>]+>\s*)*(?:<[^>]+>\s*)*([0-9\s.,]+)\s*€",
        r"(?:CA|chiffre.d.affaires)\s*:\s*([0-9\s.,]+)\s*€",
    ]
    for pat in ca_patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            result["chiffre_affaires"] = m.group(1).strip() + " €"
            break

    # Résultat net
    rn_patterns = [
        r"R[ée]sultat\s+net\s*(?:</[^>]+>\s*)*(?:<[^>]+>\s*)*([-−]?\s*[0-9\s.,]+)\s*€",
        r"(?:résultat.net)\s*:\s*([-−]?\s*[0-9\s.,]+)\s*€",
    ]
    for pat in rn_patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            result["resultat_net"] = m.group(1).strip().replace("−", "-") + " €"
            break

    # Date des comptes — "Comptes annuels 2024" ou "clôture au 31/12/2024"
    date_patterns = [
        r"cl[ôo]ture\s+(?:au\s+)?(\d{2}/\d{2}/\d{4})",
        r"Comptes\s+annuels?\s+(\d{4})",
        r"Bilan\s+(\d{4})",
    ]
    for pat in date_patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            val = m.group(1)
            if len(val) == 4:
                result["date_comptes"] = f"{val}-12-31"
            else:
                # DD/MM/YYYY → YYYY-MM-DD
                parts = val.split("/")
                if len(parts) == 3:
                    result["date_comptes"] = f"{parts[2]}-{parts[1]}-{parts[0]}"
            break

    return result


async def enrich_from_pappers(client: httpx.AsyncClient, siren: str) -> dict:
    """Scrape les données financières depuis Pappers.fr.

    Returns:
        {"chiffre_affaires": "...", "resultat_net": "...", "date_comptes": "..."}
        ou {} si non trouvé / erreur.
    """
    # Check cache
    if siret_cache.is_connected():
        try:
            cached = await siret_cache._redis.get(f"{_CACHE_PREFIX}{siren}")
            if cached is not None:
                return json.loads(cached)
        except Exception:
            pass

    url = f"https://www.pappers.fr/entreprise/{siren}"
    try:
        resp = await client.get(url, headers=_HEADERS, timeout=5, follow_redirects=True)
        if resp.status_code == 429:
            logger.warning("Pappers rate limited (429)")
            return {}
        if resp.status_code != 200:
            return {}

        ct = resp.headers.get("content-type", "")
        if "text/html" not in ct:
            return {}

        html = resp.text[:200_000]  # Limiter la taille
        result = _extract_financial(html)

    except httpx.TimeoutException:
        logger.debug(f"Pappers timeout for {siren}")
        return {}
    except Exception as e:
        logger.debug(f"Pappers error for {siren}: {e}")
        return {}

    # Cache result (even empty — to avoid repeated failures)
    if siret_cache.is_connected():
        try:
            await siret_cache._redis.set(
                f"{_CACHE_PREFIX}{siren}", json.dumps(result), ex=_CACHE_TTL
            )
        except Exception:
            pass

    return result
