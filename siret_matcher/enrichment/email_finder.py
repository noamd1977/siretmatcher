"""Détection d'email professionnel en 3 étapes.

A. Extraction depuis le site web (scraping des pages contact/mentions)
B. Déduction du domaine + vérification MX
C. Pattern depuis le nom du dirigeant
"""
import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import dns.resolver
import httpx

from siret_matcher import cache as siret_cache
from siret_matcher.normalizer import strip_accents

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "enrich:emails:"
_CACHE_TTL = 14 * 86400  # 14 jours

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_BLACKLIST_PREFIXES = frozenset({
    "noreply", "no-reply", "no_reply", "postmaster", "abuse",
    "webmaster", "mailer-daemon", "root", "hostmaster",
    "admin", "administrator", "support-technique",
    "exemple", "example", "test", "demo",
})

# Priorité : plus le score est bas, plus l'email est pertinent
_PRIORITY = {
    "contact": 1,
    "info": 2,
    "direction": 3,
    "accueil": 4,
    "commercial": 5,
    "rh": 6,
    "formation": 6,
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

# Pages à crawler pour trouver des emails
_EMAIL_PAGES = [
    "", "/contact", "/nous-contacter", "/contactez-nous",
    "/mentions-legales", "/mentions", "/legal",
    "/a-propos", "/qui-sommes-nous", "/about",
]


@dataclass
class EmailResult:
    email: str
    confidence: str  # "verified", "probable", "suggested"
    source: str      # "website", "domain_pattern", "dirigeant_pattern"
    domain_has_mx: bool = False


# ── Helpers ─────────────────────────────────────────────────────────────────


def extract_domain(site_web: str) -> str:
    """Extrait le domaine d'une URL."""
    site = site_web.strip()
    if not site:
        return ""
    if not site.startswith("http"):
        site = "https://" + site
    parsed = urlparse(site)
    hostname = parsed.hostname or ""
    # Enlever www
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def extract_emails_from_html(html: str) -> list[str]:
    """Extrait les emails d'un contenu HTML."""
    if not html:
        return []
    # Décoder les entités HTML basiques
    text = html.replace("&#64;", "@").replace("&#46;", ".").replace("[at]", "@").replace("[dot]", ".")
    emails = _EMAIL_RE.findall(text)
    # Déduplication en préservant l'ordre
    seen = set()
    unique = []
    for e in emails:
        low = e.lower()
        if low not in seen:
            seen.add(low)
            unique.append(low)
    return unique


def filter_emails(emails: list[str], site_domain: str = "") -> list[str]:
    """Filtre les emails indésirables et priorise."""
    filtered = []
    for email in emails:
        local = email.split("@")[0].lower()
        # Filtrer les blacklistés
        if local in _BLACKLIST_PREFIXES or any(local.startswith(p + ".") for p in _BLACKLIST_PREFIXES):
            continue
        # Filtrer les extensions de fichiers (images, etc.)
        if email.endswith((".png", ".jpg", ".gif", ".svg", ".css", ".js")):
            continue
        filtered.append(email)

    # Trier par priorité
    def _priority(email: str) -> int:
        local = email.split("@")[0].lower()
        for prefix, prio in _PRIORITY.items():
            if local == prefix or local.startswith(prefix + "."):
                return prio
        return 10  # autres

    filtered.sort(key=_priority)
    return filtered


def check_mx_record(domain: str) -> bool:
    """Vérifie si le domaine a un enregistrement MX."""
    if not domain:
        return False
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        return len(answers) > 0
    except Exception:
        return False


def generate_dirigeant_patterns(
    nom: str, prenom: str, domain: str
) -> list[str]:
    """Génère des patterns d'email à partir du nom du dirigeant."""
    if not domain or not nom:
        return []

    # Normaliser : minuscules, pas d'accents
    n = strip_accents(nom.lower().strip()).replace(" ", "-")
    p = strip_accents((prenom or "").lower().strip()).replace(" ", "-")

    # Prendre le premier prénom si composé
    p_first = p.split("-")[0] if p else ""
    p_initial = p_first[0] if p_first else ""

    patterns = []
    if p_first and n:
        patterns.append(f"{p_first}.{n}@{domain}")
        patterns.append(f"{p_first}{n}@{domain}")
    if p_initial and n:
        patterns.append(f"{p_initial}.{n}@{domain}")
    if p_first:
        patterns.append(f"{p_first}@{domain}")
    if n:
        patterns.append(f"{n}@{domain}")

    return patterns


# ── Fonction principale ─────────────────────────────────────────────────────


async def find_emails(
    client: httpx.AsyncClient,
    site_web: str = "",
    dirigeant_nom: str = "",
    dirigeant_prenom: str = "",
) -> list[EmailResult]:
    """Détecte les emails professionnels.

    Returns:
        Liste d'EmailResult triée par confiance (verified > probable > suggested).
    """
    domain = extract_domain(site_web)

    # Check cache
    cache_key = f"{_CACHE_PREFIX}{domain or 'none'}"
    if siret_cache.is_connected():
        try:
            cached = await siret_cache._redis.get(cache_key)
            if cached is not None:
                data = json.loads(cached)
                return [EmailResult(**e) for e in data]
        except Exception:
            pass

    results: list[EmailResult] = []
    seen_emails: set[str] = set()

    # ── Étape A : Scraping du site web ──────────────────────────────────────
    if site_web and domain:
        site = site_web.strip()
        if not site.startswith("http"):
            site = "https://" + site
        parsed = urlparse(site)
        base = f"https://{parsed.hostname}"

        for page in _EMAIL_PAGES:
            url = base + page
            try:
                resp = await client.get(
                    url, headers=_HEADERS, timeout=5, follow_redirects=True
                )
                if resp.status_code != 200:
                    continue
                ct = resp.headers.get("content-type", "")
                if "text/html" not in ct:
                    continue
                html = resp.text[:200_000]
                emails = extract_emails_from_html(html)
                emails = filter_emails(emails, domain)
                for email in emails:
                    if email not in seen_emails:
                        seen_emails.add(email)
                        results.append(EmailResult(
                            email=email,
                            confidence="verified",
                            source="website",
                            domain_has_mx=True,
                        ))
            except Exception:
                continue

            if len(results) >= 5:
                break

    # ── Étape B : Pattern domaine + MX ──────────────────────────────────────
    if domain and not results:
        has_mx = check_mx_record(domain)
        if has_mx:
            for prefix in ("contact", "info", "direction"):
                email = f"{prefix}@{domain}"
                if email not in seen_emails:
                    seen_emails.add(email)
                    results.append(EmailResult(
                        email=email,
                        confidence="probable",
                        source="domain_pattern",
                        domain_has_mx=True,
                    ))

    # ── Étape C : Pattern dirigeant ─────────────────────────────────────────
    if domain and dirigeant_nom:
        has_mx = check_mx_record(domain) if not any(r.domain_has_mx for r in results) else True
        if has_mx:
            patterns = generate_dirigeant_patterns(dirigeant_nom, dirigeant_prenom, domain)
            for email in patterns[:3]:
                if email not in seen_emails:
                    seen_emails.add(email)
                    results.append(EmailResult(
                        email=email,
                        confidence="suggested",
                        source="dirigeant_pattern",
                        domain_has_mx=True,
                    ))

    # Cache
    if siret_cache.is_connected():
        try:
            data = json.dumps([
                {"email": r.email, "confidence": r.confidence,
                 "source": r.source, "domain_has_mx": r.domain_has_mx}
                for r in results
            ])
            await siret_cache._redis.set(cache_key, data, ex=_CACHE_TTL)
        except Exception:
            pass

    return results
