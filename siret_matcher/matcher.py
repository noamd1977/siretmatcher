"""Pipeline orchestrateur : enchaîne les 5 étapes de matching."""
import asyncio
import logging
import httpx
from tqdm.asyncio import tqdm_asyncio
from siret_matcher.models import Prospect, SireneResult
from siret_matcher.normalizer import normalize_prospect
from siret_matcher.db import SireneDB
from siret_matcher.stages.api_recherche import stage_api_recherche
from siret_matcher.stages.address_match import stage_address_match
from siret_matcher.stages.trigram_match import stage_trigram_match
from siret_matcher.stages.scraper import stage_scrape_siret

logger = logging.getLogger(__name__)

# Concurrence par étape
SEM_API = asyncio.Semaphore(5)      # API externe : max 5 simultanés
SEM_DB = asyncio.Semaphore(20)      # DB locale : max 20 simultanés
SEM_SCRAPE = asyncio.Semaphore(10)  # Scraping : max 10 simultanés


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

    # ---- Étape 1-2 : API Recherche d'Entreprises ----
    async with SEM_API:
        result = await stage_api_recherche(client, prospect)
    if result:
        prospect.result = result
        return prospect

    if use_db:
        # ---- Étape 3 : Matching par adresse ----
        async with SEM_DB:
            result = await stage_address_match(client, db, prospect)
        if result:
            prospect.result = result
            return prospect

        # ---- Étape 4 : Trigrams PostgreSQL ----
        async with SEM_DB:
            result = await stage_trigram_match(db, prospect)
        if result:
            prospect.result = result
            return prospect

    # ---- Étape 5 : Scraping mentions légales ----
    if prospect.site_web:
        async with SEM_SCRAPE:
            result = await stage_scrape_siret(client, db, prospect)
        if result:
            prospect.result = result
            return prospect

    # Aucun match
    from siret_matcher.opco import get_opco
    opco, source_opco = get_opco("", prospect.nom)
    prospect.result = SireneResult(
        score=0,
        methode="NON_TROUVE",
        opco=opco,
        source_opco=source_opco,
    )
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
