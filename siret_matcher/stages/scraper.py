"""Étape 5 : Scraping des mentions légales pour extraire le SIRET."""
import asyncio
import logging
import re

import httpx
from bs4 import BeautifulSoup

from siret_matcher.db import SireneDB
from siret_matcher.models import Prospect, SireneResult
from siret_matcher.opco import format_effectif, get_opco

logger = logging.getLogger(__name__)

SIRET_PATTERNS = [
    re.compile(r"(?:SIRET|siret|Siret)\s*[:.\-]?\s*(\d{3}[\s.]?\d{3}[\s.]?\d{3}[\s.]?\d{5})"),
    re.compile(r"(?:SIREN|siren|Siren)\s*[:.\-]?\s*(\d{3}[\s.]?\d{3}[\s.]?\d{3})"),
    re.compile(r"\b(\d{14})\b"),  # 14 chiffres consécutifs
]

LEGAL_PATHS = [
    "/mentions-legales", "/mentions-legales/", "/mentions",
    "/legal", "/cgu", "/cgv",
    "/a-propos", "/qui-sommes-nous", "/about",
    "/contact", "/infos-legales",
]

SEMAPHORE = asyncio.Semaphore(10)  # Max 10 scrapes concurrents


def _validate_siret(siret: str) -> bool:
    """Validation basique d'un SIRET (14 chiffres, Luhn)."""
    s = re.sub(r"[\s.]", "", siret)
    if not re.match(r"^\d{14}$", s):
        return False
    # Algorithme de Luhn (de droite à gauche)
    total = 0
    for i, c in enumerate(reversed(s)):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _validate_siren(siren: str) -> bool:
    s = re.sub(r"[\s.]", "", siren)
    if not re.match(r"^\d{9}$", s):
        return False
    total = 0
    for i, c in enumerate(reversed(s)):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


async def _fetch_page(client: httpx.AsyncClient, url: str) -> str | None:
    async with SEMAPHORE:
        try:
            resp = await client.get(url, timeout=10, follow_redirects=True)
            if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                return resp.text
        except Exception:
            pass
    return None


def _extract_siret_from_html(html: str) -> str | None:
    """Extraire un SIRET valide depuis du HTML."""
    # D'abord chercher dans le texte brut
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ")

    for pattern in SIRET_PATTERNS:
        matches = pattern.findall(text)
        for match in matches:
            clean = re.sub(r"[\s.]", "", match)
            if len(clean) == 14 and _validate_siret(clean):
                return clean
            elif len(clean) == 9 and _validate_siren(clean):
                return clean  # On retourne le SIREN, le caller complétera
    return None


async def stage_scrape_siret(
    client: httpx.AsyncClient,
    db: SireneDB,
    prospect: Prospect,
) -> SireneResult | None:
    """Scraper le site web du prospect pour trouver son SIRET dans les mentions légales."""
    site = (prospect.site_web or "").strip()
    if not site:
        return None

    # Normaliser l'URL
    if not site.startswith("http"):
        site = "https://" + site
    site = site.rstrip("/")

    # Tester les pages de mentions légales
    found_siret = None
    for path in LEGAL_PATHS:
        url = site + path
        html = await _fetch_page(client, url)
        if html:
            siret = _extract_siret_from_html(html)
            if siret:
                found_siret = siret
                break

    # Aussi tester la page d'accueil (certains sites ont le SIRET en footer)
    if not found_siret:
        html = await _fetch_page(client, site)
        if html:
            found_siret = _extract_siret_from_html(html)

    if not found_siret:
        logger.debug(f"  ✗ Scrape: {prospect.nom} — pas de SIRET trouvé sur {site}")
        return None

    # Valider le SIRET trouvé contre la base locale
    clean_siret = re.sub(r"[\s.]", "", found_siret)

    if len(clean_siret) == 14:
        row = await db.validate_siret(clean_siret)
        if row:
            naf = row.get("naf", "")
            opco, source_opco = get_opco(naf, prospect.nom)
            logger.info(f"  ✓ Scrape: {prospect.nom} → SIRET={clean_siret} validé ({row['denomination']})")
            return SireneResult(
                siret=clean_siret,
                siren=clean_siret[:9],
                denomination=row.get("denomination", ""),
                enseigne=row.get("enseigne", ""),
                naf=naf,
                effectif=format_effectif(row.get("tranche_effectif", "")),
                tranche_effectif_code=row.get("tranche_effectif", ""),
                date_creation=str(row.get("date_creation", "")),
                code_postal=row.get("code_postal", ""),
                commune=row.get("commune", ""),
                score=80,
                methode="SCRAPE_MENTIONS_LEGALES",
                opco=opco,
                source_opco=source_opco,
            )
        else:
            # SIRET non trouvé en base locale (base pas à jour?) → quand même retourner
            logger.info(f"  ✓ Scrape: {prospect.nom} → SIRET={clean_siret} (non validé en base)")
            opco, source_opco = get_opco("", prospect.nom)
            return SireneResult(
                siret=clean_siret,
                siren=clean_siret[:9],
                score=65,
                methode="SCRAPE_NON_VALIDE",
                opco=opco,
                source_opco=source_opco,
            )

    logger.debug(f"  ✗ Scrape: {prospect.nom} — SIREN trouvé ({clean_siret}) mais pas SIRET complet")
    return None
