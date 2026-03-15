"""Étape 5 : Scraping des mentions légales pour extraire le SIRET."""
import asyncio
import logging
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from siret_matcher.db import SireneDB
from siret_matcher.metrics import SCRAPE_TOTAL, SCRAPE_SUCCESS, SCRAPE_PAGES_CRAWLED, SCRAPE_ERRORS
from siret_matcher.models import Prospect, SireneResult
from siret_matcher.opco import format_effectif, get_opco
from siret_matcher.scoring import score_name, score_geo

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

SCRAPE_TIMEOUT = 5  # secondes par requête
MAX_RESPONSE_SIZE = 500_000  # 500KB max par page
MAX_REDIRECTS = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SIRETMatcher/3.0; +https://dstcampus.fr)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

SEMAPHORE = asyncio.Semaphore(10)  # Max 10 scrapes concurrents

PAGES_TO_CRAWL = [
    "/mentions-legales",
    "/mentions_legales",
    "/legal",
    "/cgu",
    "/cgv",
    "/cgu-cgv",
    "/a-propos",
    "/about",
    "/about-us",
    "/qui-sommes-nous",
    "/contact",
    "/informations-legales",
    "/info-legales",
    "/conditions-generales",
    "/politique-de-confidentialite",
    "/privacy",
    "/footer",
    "/plan-du-site",
    "/sitemap",
    "",  # Homepage
]

SIRET_PATTERNS = [
    # SIRET explicite (14 chiffres avec ou sans espaces/points/tirets)
    re.compile(r"(?:SIRET|siret|Siret)\s*[:;]?\s*(\d[\d\s.\-]{12,17}\d)"),
    # SIREN explicite (9 chiffres)
    re.compile(r"(?:SIREN|siren|Siren)\s*[:;]?\s*(\d[\d\s.\-]{7,11}\d)"),
    # RCS suivi du SIREN
    re.compile(r"(?:RCS|R\.C\.S\.)\s+\w+\s+(\d{3}\s?\d{3}\s?\d{3})"),
    # N° TVA intracommunautaire (FR + 2 chiffres + SIREN)
    re.compile(r"(?:TVA|tva)\s*[:;]?\s*FR\s*\d{2}\s*(\d{3}\s?\d{3}\s?\d{3})"),
    # SIRET brut 14 chiffres dans le texte (dernier recours)
    re.compile(r"\b(\d{3}\s?\d{3}\s?\d{3}\s?\d{5})\b"),
]


# ── Validation Luhn ──────────────────────────────────────────────────────────

def _luhn_check(digits: str) -> bool:
    """Algorithme de Luhn sur une chaîne de chiffres."""
    total = 0
    for i, c in enumerate(reversed(digits)):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _validate_siret(siret: str) -> bool:
    """Validation d'un SIRET (14 chiffres, Luhn)."""
    s = re.sub(r"[\s.\-]", "", siret)
    if not re.match(r"^\d{14}$", s):
        return False
    return _luhn_check(s)


def _validate_siren(siren: str) -> bool:
    """Validation d'un SIREN (9 chiffres, Luhn)."""
    s = re.sub(r"[\s.\-]", "", siren)
    if not re.match(r"^\d{9}$", s):
        return False
    return _luhn_check(s)


def _clean_number(raw: str) -> str:
    """Supprimer espaces, points, tirets d'un numéro."""
    return re.sub(r"[\s.\-]", "", raw)


# ── Construction d'URLs ──────────────────────────────────────────────────────

def build_urls_to_crawl(site_web: str) -> list[str]:
    """
    Construit la liste d'URLs à crawler à partir du site web du prospect.

    Gère :
    - URLs avec ou sans protocole (ajout https:// si manquant)
    - URLs avec ou sans www
    - URLs avec un path existant (ex: entreprise.com/fr/) → ajouter les sous-pages
    - Nettoyage des trailing slashes
    - Domaines avec sous-domaine (ex: shop.entreprise.com → tester aussi entreprise.com)
    """
    site = site_web.strip()
    if not site:
        return []

    # Ajouter le protocole si manquant
    if not site.startswith("http"):
        site = "https://" + site

    parsed = urlparse(site)
    hostname = parsed.hostname or ""
    base_path = parsed.path.rstrip("/")

    # Bases à tester : URL d'origine + variantes
    bases = []

    # Base principale
    base_url = f"https://{hostname}"
    bases.append(base_url)

    # Si le site a un sous-domaine (pas www), tester aussi le domaine parent
    parts = hostname.split(".")
    if len(parts) > 2 and parts[0] != "www":
        parent = ".".join(parts[1:])
        bases.append(f"https://{parent}")
        bases.append(f"https://www.{parent}")

    # Si pas de www, tester aussi avec www (et vice versa)
    if hostname.startswith("www."):
        bases.append(f"https://{hostname[4:]}")
    else:
        bases.append(f"https://www.{hostname}")

    # Dédoublonner les bases
    seen_bases = []
    for b in bases:
        if b not in seen_bases:
            seen_bases.append(b)

    # Construire toutes les URLs
    urls = []
    seen = set()

    for base in seen_bases:
        for page in PAGES_TO_CRAWL:
            # Si le site original a un path, ajouter les pages en sous-chemin aussi
            if base_path and page:
                url = f"{base}{base_path}{page}"
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
            url = f"{base}{page}"
            if url not in seen:
                seen.add(url)
                urls.append(url)

    return urls


# ── Fetch d'une page ─────────────────────────────────────────────────────────

async def _fetch_page(client: httpx.AsyncClient, url: str) -> str | None:
    """Télécharge une page HTML avec gestion robuste des erreurs."""
    async with SEMAPHORE:
        try:
            resp = await client.get(
                url,
                timeout=SCRAPE_TIMEOUT,
                follow_redirects=True,
                headers=HEADERS,
            )

            # Vérifier que c'est du HTML
            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return None

            if resp.status_code != 200:
                return None

            # Limiter la taille
            content = resp.content
            if len(content) > MAX_RESPONSE_SIZE:
                content = content[:MAX_RESPONSE_SIZE]

            # Détecter l'encoding via BeautifulSoup
            return content.decode(resp.encoding or "utf-8", errors="replace")

        except httpx.ConnectError:
            SCRAPE_ERRORS.labels(error_type="connection").inc()
        except httpx.TimeoutException:
            SCRAPE_ERRORS.labels(error_type="timeout").inc()
        except httpx.TooManyRedirects:
            SCRAPE_ERRORS.labels(error_type="too_many_redirects").inc()
        except Exception as e:
            error_type = "ssl" if "ssl" in str(e).lower() else "other"
            SCRAPE_ERRORS.labels(error_type=error_type).inc()
            logger.debug(f"Scrape error on {url}: {e}")
    return None


async def _fetch_page_with_http_fallback(
    client: httpx.AsyncClient, url: str
) -> str | None:
    """Tente HTTPS puis HTTP en cas d'erreur SSL."""
    result = await _fetch_page(client, url)
    if result is not None:
        return result

    # Fallback HTTP si c'était du HTTPS
    if url.startswith("https://"):
        http_url = "http://" + url[8:]
        logger.debug(f"SSL fallback: {url} → {http_url}")
        return await _fetch_page(client, http_url)

    return None


# ── Extraction depuis HTML ───────────────────────────────────────────────────

def _extract_candidates_from_html(html: str) -> list[dict]:
    """
    Extraire tous les candidats SIRET/SIREN depuis du HTML.

    Retourne une liste de dicts: {"value": "...", "type": "siret"|"siren", "source": "pattern_name"}
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ")

    candidates = []
    seen = set()

    for pattern in SIRET_PATTERNS:
        matches = pattern.findall(text)
        for match in matches:
            clean = _clean_number(match)
            if clean in seen:
                continue
            seen.add(clean)

            if len(clean) == 14 and _validate_siret(clean):
                candidates.append({"value": clean, "type": "siret"})
            elif len(clean) == 9 and _validate_siren(clean):
                candidates.append({"value": clean, "type": "siren"})

    return candidates


# Legacy function kept for backwards compatibility with existing callers
def _extract_siret_from_html(html: str) -> str | None:
    """Extraire un SIRET/SIREN valide depuis du HTML."""
    candidates = _extract_candidates_from_html(html)
    if candidates:
        return candidates[0]["value"]
    return None


# ── Scoring & sélection du meilleur candidat ─────────────────────────────────

def _score_candidate(row: dict, prospect: Prospect) -> float:
    """Score un candidat SIRET par rapport au prospect."""
    s = 0.0
    # Score géographique
    s += score_geo(
        prospect.code_postal,
        row.get("code_postal", ""),
        prospect.departement,
        (row.get("code_postal", "") or "")[:2],
    )
    # Score nom
    s += score_name(
        prospect.nom,
        row.get("denomination", ""),
        row.get("enseigne", ""),
    )
    return s


async def _resolve_candidates(
    db: SireneDB,
    candidates: list[dict],
    prospect: Prospect,
) -> list[tuple[dict, float]]:
    """
    Résoudre les candidats SIRET/SIREN contre la BDD et les scorer.

    Retourne une liste de (row, score) triée par score décroissant.
    """
    scored = []

    for cand in candidates:
        if cand["type"] == "siret":
            row = await db.validate_siret(cand["value"])
            if row:
                s = _score_candidate(row, prospect)
                scored.append((row, s))
        elif cand["type"] == "siren":
            rows = await db.search_by_siren(cand["value"])
            for row in rows:
                s = _score_candidate(row, prospect)
                scored.append((row, s))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


# ── Stage principal ──────────────────────────────────────────────────────────

async def stage_scrape_siret(
    client: httpx.AsyncClient,
    db: SireneDB,
    prospect: Prospect,
) -> SireneResult | None:
    """Scraper le site web du prospect pour trouver son SIRET dans les mentions légales."""
    site = (prospect.site_web or "").strip()
    if not site:
        return None

    SCRAPE_TOTAL.inc()

    urls = build_urls_to_crawl(site)
    if not urls:
        return None

    # Crawler les pages et collecter tous les candidats
    all_candidates = []
    pages_crawled = 0

    for url in urls:
        html = await _fetch_page_with_http_fallback(client, url)
        if html:
            pages_crawled += 1
            candidates = _extract_candidates_from_html(html)
            for c in candidates:
                if c not in all_candidates:
                    all_candidates.append(c)

            # Si on a déjà des candidats SIRET (pas juste SIREN), on peut arrêter
            if any(c["type"] == "siret" for c in all_candidates):
                break

    SCRAPE_PAGES_CRAWLED.observe(pages_crawled)

    if not all_candidates:
        logger.debug(f"  \u2717 Scrape: {prospect.nom} \u2014 pas de SIRET trouv\u00e9 sur {site}")
        return None

    # Résoudre et scorer les candidats contre la BDD
    scored = await _resolve_candidates(db, all_candidates, prospect)

    if scored:
        best_row, best_score = scored[0]
        siret = best_row["siret"]
        naf = best_row.get("naf", "")
        opco, source_opco = get_opco(naf, prospect.nom)

        # Confiance selon le nombre de candidats
        if len(scored) == 1:
            confidence = 80
        else:
            confidence = 70

        logger.info(
            f"  \u2713 Scrape: {prospect.nom} \u2192 SIRET={siret} "
            f"({best_row.get('denomination', '')}, score={best_score:.0f}, "
            f"{len(scored)} candidat(s))"
        )
        SCRAPE_SUCCESS.inc()
        return SireneResult(
            siret=siret,
            siren=siret[:9],
            denomination=best_row.get("denomination", ""),
            enseigne=best_row.get("enseigne", ""),
            naf=naf,
            effectif=format_effectif(best_row.get("tranche_effectif", "")),
            tranche_effectif_code=best_row.get("tranche_effectif", ""),
            date_creation=str(best_row.get("date_creation", "")),
            code_postal=best_row.get("code_postal", ""),
            commune=best_row.get("commune", ""),
            score=confidence,
            methode="SCRAPE_MENTIONS_LEGALES",
            opco=opco,
            source_opco=source_opco,
        )

    # Candidats trouvés mais non validés en BDD → retourner le premier SIRET quand même
    for cand in all_candidates:
        if cand["type"] == "siret":
            siret = cand["value"]
            logger.info(f"  \u2713 Scrape: {prospect.nom} \u2192 SIRET={siret} (non valid\u00e9 en base)")
            opco, source_opco = get_opco("", prospect.nom)
            SCRAPE_SUCCESS.inc()
            return SireneResult(
                siret=siret,
                siren=siret[:9],
                score=65,
                methode="SCRAPE_NON_VALIDE",
                opco=opco,
                source_opco=source_opco,
            )

    logger.debug(
        f"  \u2717 Scrape: {prospect.nom} \u2014 SIREN trouv\u00e9(s) "
        f"mais pas de SIRET complet en BDD"
    )
    return None
