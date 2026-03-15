"""API HTTP pour SIRET Matcher — appelable par n8n."""
import asyncio
import logging
import os
import re
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional
import uvicorn
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

# Ajouter le projet au path
sys.path.insert(0, "/opt/siret-matcher")
os.chdir("/opt/siret-matcher")

from siret_matcher.search_router import router as search_router
from siret_matcher.models import Prospect, SireneResult
from siret_matcher.matcher import match_one, match_batch
from siret_matcher.db import SireneDB
from siret_matcher.lookups import NAF_TO_OPCO, DEPT_TO_REGION, NAF_LIBELLES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
logger = logging.getLogger(__name__)


def get_real_client_ip(request: Request) -> str:
    """Extrait l'IP réelle du client depuis X-Forwarded-For (Traefik) ou le socket."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For: client, proxy1, proxy2 → on prend le client
        return forwarded_for.split(",")[0].strip()
    return request.client.host


limiter = Limiter(key_func=get_real_client_ip)
app = FastAPI(title="SIRET Matcher API", version="2.0")
app.state.limiter = limiter
app.include_router(search_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
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
        stats = await db.get_stats()
        logger.info(f"Base Sirene connectee: {stats['active']:,} etablissements actifs")
    except Exception as e:
        logger.error(f"Erreur connexion DB: {e}")


@app.on_event("shutdown")
async def shutdown():
    global http_client
    if http_client:
        await http_client.aclose()
    await db.close()


@app.get("/health")
async def health():
    try:
        stats = await db.get_stats()
        return {"status": "ok", "etablissements_actifs": stats["active"]}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.post("/match", response_model=MatchResult)
async def match_single(p: ProspectInput):
    """Matcher un seul prospect."""
    prospect = prospect_from_input(p)
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
        return result_from_prospect(result)
    except Exception as e:
        logger.error(f"Erreur matching {p.nom}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/match/batch", response_model=BatchResponse)
async def match_multi(req: BatchRequest):
    """Matcher un batch de prospects."""
    prospects = [prospect_from_input(p) for p in req.prospects]
    try:
        results = await match_batch(prospects, use_db=True, concurrency=req.concurrency)
        match_results = [result_from_prospect(p) for p in results]
        matched = sum(1 for r in match_results if r.matched)
        return BatchResponse(
            total=len(match_results),
            matched=matched,
            taux=f"{matched/len(match_results)*100:.0f}%",
            results=match_results,
        )
    except Exception as e:
        logger.error(f"Erreur batch matching: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/dst/siret/{siret}")
@limiter.limit("30/minute")
async def dst_siret_lookup(request: Request, siret: str):
    """Lookup SIRET pour le simulateur dstcampus.fr."""
    # Validation format
    if not SIRET_RE.match(siret):
        return JSONResponse(
            status_code=400,
            content={"error": "Format SIRET invalide. Le SIRET doit contenir exactement 14 chiffres."},
        )

    # Requête SQL principale avec jointures OPCO + IDCC
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

    return {
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8042)
