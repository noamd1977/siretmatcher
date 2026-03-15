"""Pipeline orchestrateur : enchaîne les 5 étapes de matching."""
import asyncio
import logging
import time

import httpx
from tqdm.asyncio import tqdm_asyncio

from siret_matcher.db import SireneDB
from siret_matcher.logging_config import log_structured
from siret_matcher.metrics import MATCH_TOTAL, MATCH_SCORE, MATCH_METHOD, MATCH_STAGES_TRIED
from siret_matcher.models import Prospect, SireneResult
from siret_matcher.normalizer import normalize_prospect
from siret_matcher.stages.address_match import stage_address_match
from siret_matcher.stages.api_recherche import stage_api_recherche
from siret_matcher.stages.scraper import stage_scrape_siret
from siret_matcher.stages.trigram_match import stage_trigram_match

logger = logging.getLogger(__name__)

# Concurrence par étape
SEM_API = asyncio.Semaphore(5)      # API externe : max 5 simultanés
SEM_DB = asyncio.Semaphore(20)      # DB locale : max 20 simultanés
SEM_SCRAPE = asyncio.Semaphore(10)  # Scraping : max 10 simultanés


async def _run_stage(name: str, coro):
    """Exécute une étape et logge le résultat avec sa durée."""
    t0 = time.perf_counter()
    result = await coro
    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    extra = {"stage": name, "found": result is not None, "duration_ms": duration_ms}
    if result is not None:
        extra["score"] = result.score
    log_structured(logger, logging.DEBUG, "stage_result", **extra)
    return result


def _record_match_metrics(prospect: Prospect) -> None:
    """Enregistre les métriques Prometheus pour un matching terminé."""
    r = prospect.result
    if not r:
        return
    matched = r.siret and r.methode != "NON_TROUVE"
    MATCH_TOTAL.labels(result="matched" if matched else "not_found").inc()
    MATCH_SCORE.observe(r.score)
    if r.methode:
        MATCH_METHOD.labels(method=r.methode).inc()
    stages = getattr(prospect, "_stages_tried", 0)
    if stages:
        MATCH_STAGES_TRIED.observe(stages)


async def match_one(
    client: httpx.AsyncClient,
    db: SireneDB,
    prospect: Prospect,
    use_db: bool = True,
) -> Prospect:
    """Matcher un seul prospect à travers toutes les étapes."""
    # Normaliser
    normalize_prospect(prospect)

    logger.info(f"[{prospect.nom}] variantes={prospect.nom_variantes[:3]} addr={prospect.adresse_numero} {prospect.adresse_voie_clean}")

    stages_tried = 0

    # ---- Étape 1-2 : API Recherche d'Entreprises ----
    stages_tried += 1
    async with SEM_API:
        result = await _run_stage("api_recherche", stage_api_recherche(client, prospect))
    if result:
        prospect.result = result
        prospect._stages_tried = stages_tried
        _record_match_metrics(prospect)
        return prospect

    if use_db:
        # ---- Étape 3 : Matching par adresse ----
        stages_tried += 1
        async with SEM_DB:
            result = await _run_stage("address_match", stage_address_match(client, db, prospect))
        if result:
            prospect.result = result
            prospect._stages_tried = stages_tried
            _record_match_metrics(prospect)
            return prospect

        # ---- Étape 4 : Trigrams PostgreSQL ----
        stages_tried += 1
        async with SEM_DB:
            result = await _run_stage("trigram_fuzzy", stage_trigram_match(db, prospect))
        if result:
            prospect.result = result
            prospect._stages_tried = stages_tried
            _record_match_metrics(prospect)
            return prospect

    # ---- Étape 5 : Scraping mentions légales ----
    if prospect.site_web:
        stages_tried += 1
        async with SEM_SCRAPE:
            result = await _run_stage("scrape_mentions", stage_scrape_siret(client, db, prospect))
        if result:
            prospect.result = result
            prospect._stages_tried = stages_tried
            _record_match_metrics(prospect)
            return prospect

    # Aucun match
    stages_tried = 5
    from siret_matcher.opco import get_opco
    opco, source_opco = get_opco("", prospect.nom)
    prospect.result = SireneResult(
        score=0,
        methode="NON_TROUVE",
        opco=opco,
        source_opco=source_opco,
    )
    prospect._stages_tried = stages_tried
    _record_match_metrics(prospect)
    logger.info(f"  ✗ AUCUN MATCH: {prospect.nom}")
    return prospect


async def match_batch(
    prospects: list[Prospect],
    use_db: bool = True,
    concurrency: int = 5,
) -> list[Prospect]:
    """Matcher un batch de prospects avec parallélisme contrôlé."""
    db = SireneDB()
    if use_db:
        try:
            await db.connect()
            stats = await db.get_stats()
            logger.info(f"Base Sirene : {stats['active']:,} établissements actifs / {stats['total']:,} total")
        except Exception as e:
            logger.warning(f"Base Sirene indisponible ({e}) — étapes 3-4 désactivées")
            use_db = False

    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    async with httpx.AsyncClient(
        limits=limits,
        headers={"User-Agent": "SIRETMatcher/2.0 (contact@akol-formation.fr)"},
        follow_redirects=True,
    ) as client:
        sem = asyncio.Semaphore(concurrency)

        async def _match_with_sem(p):
            async with sem:
                return await match_one(client, db, p, use_db=use_db)

        tasks = [_match_with_sem(p) for p in prospects]
        results = []
        for coro in tqdm_asyncio.as_completed(tasks, total=len(tasks), desc="Matching"):
            result = await coro
            results.append(result)

    if use_db:
        await db.close()

    return results


def prospects_to_dicts(prospects: list[Prospect]) -> list[dict]:
    """Convertir les prospects matchés en dictionnaires pour export."""
    rows = []
    for p in prospects:
        r = p.result or SireneResult()
        rows.append({
            "nom": p.nom,
            "adresse": p.adresse,
            "code_postal": p.code_postal,
            "ville": p.ville,
            "departement": p.departement,
            "telephone": p.telephone,
            "site_web": p.site_web,
            "email": p.email,
            "secteur_recherche": p.secteur_recherche,
            "place_id": p.place_id,
            "rating": p.rating,
            "avis": p.avis,
            # Résultat matching
            "siret": r.siret,
            "siren": r.siren,
            "denomination_sirene": r.denomination,
            "enseigne_sirene": r.enseigne,
            "naf": r.naf,
            "effectif": r.effectif,
            "tranche_effectif_code": r.tranche_effectif_code,
            "date_creation": r.date_creation,
            "dirigeant": r.dirigeant,
            "opco": r.opco,
            "source_opco": r.source_opco,
            "idcc": r.idcc,
            "convention_collective": r.convention_collective,
            "score_confiance": r.score,
            "methode_matching": r.methode,
            "statut_prospection": "À contacter" if r.siret else "Non enrichi",
        })
    return rows
