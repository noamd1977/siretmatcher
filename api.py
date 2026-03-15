"""API HTTP pour SIRET Matcher — appelable par n8n."""
import asyncio
import logging
import os
import re
import sys
import time

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional
import uvicorn
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

# Ajouter le projet au path
sys.path.insert(0, "/opt/siret-matcher")
os.chdir("/opt/siret-matcher")

from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from siret_matcher.auth import require_api_key
from siret_matcher import cache as siret_cache
from siret_matcher.logging_config import setup_logging, log_structured
from siret_matcher.metrics import (
    REQUEST_COUNT, REQUEST_DURATION, MATCH_TOTAL, MATCH_SCORE,
    MATCH_METHOD, MATCH_STAGES_TRIED, DST_LOOKUP_TOTAL,
    DB_POOL_SIZE, ETABLISSEMENTS_COUNT, CACHE_HITS, CACHE_MISSES,
)
from siret_matcher.search_router import router as search_router
from siret_matcher.api_v3 import router as api_v3_router
from siret_matcher.models import Prospect, SireneResult
from siret_matcher.matcher import match_one, match_batch
from siret_matcher.db import SireneDB
from siret_matcher.lookups import NAF_TO_OPCO, DEPT_TO_REGION, NAF_LIBELLES

setup_logging()
logger = logging.getLogger(__name__)


def get_real_client_ip(request: Request) -> str:
    """Extrait l'IP réelle du client depuis X-Forwarded-For (Traefik) ou le socket."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For: client, proxy1, proxy2 → on prend le client
        return forwarded_for.split(",")[0].strip()
    return request.client.host


class TimingMiddleware(BaseHTTPMiddleware):
    """Mesure la durée de chaque requête et logge en JSON structuré."""

    SKIP_PATHS = {"/health", "/metrics"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in self.SKIP_PATHS:
            return await call_next(request)
        start = time.perf_counter()
        response = await call_next(request)
        duration_s = time.perf_counter() - start
        duration_ms = round(duration_s * 1000, 1)

        # Prometheus
        REQUEST_COUNT.labels(
            endpoint=path, method=request.method, status=str(response.status_code)
        ).inc()
        REQUEST_DURATION.labels(endpoint=path).observe(duration_s)

        log_structured(
            logger, logging.INFO, "request",
            method=request.method,
            path=path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        return response


limiter = Limiter(key_func=get_real_client_ip)
app = FastAPI(
    title="SIRET Matcher API",
    version="3.0.0",
    description="API d'enrichissement de prospects à partir des données SIRENE, France Compétences et BAN.",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "match", "description": "Matching intelligent de prospects"},
        {"name": "search", "description": "Recherche avancée d'établissements"},
        {"name": "referentiel", "description": "Données de référence (régions, IDCC, OPCO)"},
        {"name": "dst", "description": "Endpoints DST Campus (legacy)"},
        {"name": "system", "description": "Health, metrics"},
        {"name": "v3", "description": "API v3 — endpoints structurés pour le frontend"},
    ],
)
app.state.limiter = limiter
app.include_router(search_router)
app.include_router(api_v3_router)

app.add_middleware(TimingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"error": "Trop de requêtes. Limite : 30 par minute."},
    )


SIRET_RE = re.compile(r"^\d{14}$")

# Connexions persistantes
db = SireneDB()
http_client = None


class ProspectInput(BaseModel):
    nom: str
    adresse: str = ""
    code_postal: str
    ville: str = ""
    telephone: str = ""
    site_web: str = ""
    email: str = ""
    secteur_recherche: str = ""
    place_id: str = ""
    rating: str = ""
    avis: str = ""


class MatchResult(BaseModel):
    nom: str
    siret: Optional[str] = ""
    siren: Optional[str] = ""
    denomination: Optional[str] = ""
    enseigne: Optional[str] = ""
    naf: Optional[str] = ""
    effectif: Optional[str] = ""
    date_creation: Optional[str] = ""
    dirigeant: Optional[str] = ""
    score: float = 0.0
    methode: Optional[str] = ""
    opco: Optional[str] = ""
    convention_collective: Optional[str] = ""
    matched: bool = False


class BatchRequest(BaseModel):
    prospects: list[ProspectInput]
    concurrency: int = Field(default=5, ge=1, le=20)


class BatchResponse(BaseModel):
    total: int
    matched: int
    taux: str
    results: list[MatchResult]


def prospect_from_input(p: ProspectInput) -> Prospect:
    return Prospect(
        nom=p.nom,
        adresse=p.adresse,
        code_postal=p.code_postal,
        ville=p.ville,
        telephone=p.telephone,
        site_web=p.site_web,
        email=p.email,
        secteur_recherche=p.secteur_recherche,
        place_id=p.place_id,
        rating=p.rating,
        avis=p.avis,
    )


def result_from_prospect(p: Prospect) -> MatchResult:
    r = p.result
    if r and r.siret:
        return MatchResult(
            nom=p.nom,
            siret=r.siret,
            siren=r.siren,
            denomination=r.denomination,
            enseigne=r.enseigne,
            naf=r.naf,
            effectif=r.effectif,
            date_creation=r.date_creation,
            dirigeant=r.dirigeant,
            score=r.score,
            methode=r.methode,
            opco=r.opco,
            convention_collective=r.convention_collective,
            matched=True,
        )
    return MatchResult(nom=p.nom, matched=False)


@app.on_event("startup")
async def startup():
    global http_client
    import httpx
    http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        headers={"User-Agent": "SIRETMatcher/2.0 (contact@akol-formation.fr)"},
        follow_redirects=True,
    )
    try:
        await db.connect()
        app.state.pool = db.pool
        app.state.db = db
        stats = await db.get_stats()
        logger.info(f"Base Sirene connectee: {stats['active']:,} etablissements actifs")
        ETABLISSEMENTS_COUNT.set(stats["active"])
        DB_POOL_SIZE.set(db.pool.get_size())
    except Exception as e:
        logger.error(f"Erreur connexion DB: {e}")
    app.state.http_client = http_client
    await siret_cache.connect()


@app.on_event("shutdown")
async def shutdown():
    global http_client
    if http_client:
        await http_client.aclose()
    await db.close()
    await siret_cache.close()


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health():
    try:
        stats = await db.get_stats()
        cache_stats = await siret_cache.get_cache_stats()
        redis_ok = cache_stats["connected"]
        return {
            "status": "ok" if redis_ok else "degraded",
            "db": "connected",
            "redis": "connected" if redis_ok else "disconnected",
            "cache_size": cache_stats["size"],
            "etablissements_actifs": stats["active"],
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.post("/match", response_model=MatchResult)
async def match_single(p: ProspectInput, api_key_name: str = Depends(require_api_key)):
    """Matcher un seul prospect."""
    prospect = prospect_from_input(p)
    t0 = time.perf_counter()
    try:
        result = await match_one(http_client, db, prospect, use_db=True)
        # Vérifier que l'établissement est actif (existe dans notre base 16.7M actifs)
        if result.result and result.result.siret:
            check = await db.pool.fetchval(
                "SELECT siret FROM etablissements WHERE siret = $1 AND etat_administratif = 'A'",
                result.result.siret
            )
            if not check:
                logger.warning(f"SIRET {result.result.siret} non trouvé dans base actifs, rejet")
                result.result = None
                result.score = 0
                result.methode = "RADIE"

        # Enrichir OPCO via table France Compétences (3.49M SIRET)
        if result.result and result.result.siret:
            opco_data = await db.get_opco(result.result.siret)
            if opco_data.get("opco"):
                result.result.opco = opco_data["opco"]
                result.result.source_opco = "FRANCE_COMPETENCES"
                if opco_data.get("idcc"):
                    result.result.idcc = opco_data["idcc"]
        mr = result_from_prospect(result)
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)

        # Prometheus
        MATCH_TOTAL.labels(result="matched" if mr.matched else "not_found").inc()
        MATCH_SCORE.observe(mr.score)
        if mr.methode:
            MATCH_METHOD.labels(method=mr.methode).inc()
        stages = getattr(result, "_stages_tried", 0)
        if stages:
            MATCH_STAGES_TRIED.observe(stages)

        log_structured(
            logger, logging.INFO, "match_single",
            api_key=api_key_name,
            prospect_name=p.nom,
            prospect_cp=p.code_postal,
            matched=mr.matched,
            siret=mr.siret or "",
            score=mr.score,
            methode=mr.methode or "",
            stages_tried=stages,
            duration_ms=duration_ms,
        )
        return mr
    except Exception as e:
        logger.error(f"Erreur matching {p.nom}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/match/batch", response_model=BatchResponse)
async def match_multi(req: BatchRequest, api_key_name: str = Depends(require_api_key)):
    """Matcher un batch de prospects."""
    prospects = [prospect_from_input(p) for p in req.prospects]
    t0 = time.perf_counter()
    try:
        results = await match_batch(prospects, use_db=True, concurrency=req.concurrency)
        match_results = [result_from_prospect(p) for p in results]
        matched = sum(1 for r in match_results if r.matched)
        total = len(match_results)
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        scores = [r.score for r in match_results if r.matched]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0
        log_structured(
            logger, logging.INFO, "match_batch",
            api_key=api_key_name,
            total=total,
            matched=matched,
            taux=round(matched / total, 2) if total else 0,
            avg_score=avg_score,
            duration_ms=duration_ms,
        )
        return BatchResponse(
            total=total,
            matched=matched,
            taux=f"{matched/total*100:.0f}%",
            results=match_results,
        )
    except Exception as e:
        logger.error(f"Erreur batch matching: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/dst/siret/{siret}")
@limiter.limit("30/minute")
async def dst_siret_lookup(request: Request, siret: str):
    """Lookup SIRET pour le simulateur dstcampus.fr."""
    t0 = time.perf_counter()
    # Validation format
    if not SIRET_RE.match(siret):
        return JSONResponse(
            status_code=400,
            content={"error": "Format SIRET invalide. Le SIRET doit contenir exactement 14 chiffres."},
        )

    # 1. Cherche dans le cache
    cached = await siret_cache.get_cached_siret(siret)
    if cached is not None:
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        DST_LOOKUP_TOTAL.labels(found="true").inc()
        CACHE_HITS.inc()
        log_structured(
            logger, logging.INFO, "dst_lookup",
            siret=siret, found=True, cache="hit", duration_ms=duration_ms,
        )
        return cached

    CACHE_MISSES.inc()

    # 2. Cache miss → requête BDD
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT e.siret, e.siren, e.denomination, e.denomination_usuelle, e.enseigne,
                      e.naf, e.numero_voie, e.type_voie, e.voie, e.code_postal, e.commune,
                      e.tranche_effectif, e.date_creation, e.etat_administratif,
                      o.opco_proprietaire, o.opco_gestion, o.idcc,
                      il.libelle AS convention_libelle
               FROM etablissements e
               LEFT JOIN siret_opco o ON e.siret = o.siret
               LEFT JOIN idcc_libelles il ON o.idcc = il.idcc
               WHERE e.siret = $1""",
            siret,
        )

    if not row:
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        DST_LOOKUP_TOTAL.labels(found="false").inc()
        log_structured(
            logger, logging.INFO, "dst_lookup",
            siret=siret, found=False, cache="miss", duration_ms=duration_ms,
        )
        return {"found": False, "siret": siret, "message": "SIRET non trouvé dans la base SIRENE"}

    # Construire adresse
    parts = [p for p in [row["numero_voie"], row["type_voie"], row["voie"]] if p]
    adresse = " ".join(parts)

    # OPCO + IDCC depuis la jointure
    opco = row["opco_proprietaire"] or row["opco_gestion"] or ""
    source_opco = "FRANCE_COMPETENCES" if opco else ""
    idcc = row["idcc"] or ""

    # Fallback OPCO via NAF si pas dans France Compétences
    if not opco:
        naf_prefix = (row["naf"] or "").replace(".", "")[:2]
        if naf_prefix in NAF_TO_OPCO:
            opco = NAF_TO_OPCO[naf_prefix]
            source_opco = "NAF"

    # Convention collective depuis la jointure
    convention = row["convention_libelle"] or ""

    # Libellé NAF
    naf_code = row["naf"] or ""
    libelle_naf = NAF_LIBELLES.get(naf_code, "")

    # Région
    cp = row["code_postal"] or ""
    dept = cp[:3] if cp.startswith("97") else cp[:2]
    region = DEPT_TO_REGION.get(dept, "")

    # Enseigne : prendre enseigne ou denomination_usuelle
    enseigne = row["enseigne"] or row["denomination_usuelle"] or ""

    result = {
        "found": True,
        "siret": row["siret"],
        "siren": row["siren"],
        "denomination": row["denomination"] or "",
        "enseigne": enseigne,
        "code_naf": naf_code,
        "libelle_naf": libelle_naf,
        "effectif_code": row["tranche_effectif"] or "",
        "date_creation": row["date_creation"] or "",
        "opco": opco,
        "source_opco": source_opco,
        "idcc": idcc,
        "convention_collective": convention,
        "adresse": adresse,
        "code_postal": cp,
        "ville": row["commune"] or "",
        "region": region,
    }

    # 3. Met en cache (seulement si found)
    await siret_cache.set_cached_siret(siret, result)

    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    DST_LOOKUP_TOTAL.labels(found="true").inc()
    log_structured(
        logger, logging.INFO, "dst_lookup",
        siret=siret, found=True, cache="miss", opco=opco, source_opco=source_opco,
        duration_ms=duration_ms,
    )

    return result


# Servir le frontend React (après tous les routes API)
_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8042)
