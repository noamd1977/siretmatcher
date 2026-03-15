"""
Router FastAPI pour les endpoints /search/*
Fichier isolé — rattaché à l'app via un seul app.include_router() dans api.py.
Rollback = commenter 2 lignes dans api.py + restart.
"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request

from .search_models import (
    REGION_DEPARTEMENTS,
    TAILLE_CODES,
    ProspectResult,
    SearchProspectsRequest,
    SearchProspectsResponse,
    TailleEntreprise,
)

logger = logging.getLogger("siret_matcher.search")

router = APIRouter(prefix="/search", tags=["search"])


def _build_address(numero_voie, type_voie, voie) -> str | None:
    """Reconstitue l'adresse en une seule chaîne."""
    parts = [p for p in [numero_voie, type_voie, voie] if p]
    return " ".join(parts) if parts else None


def _build_query(req: SearchProspectsRequest):
    """
    Construit dynamiquement le WHERE clause et les params asyncpg.
    Retourne (conditions_sql: str, params: list, join_type: str)
    """
    conditions = ["e.etat_administratif = 'A'"]
    params = []
    idx = 0

    # --- Départements (obligatoire) ---
    idx += 1
    conditions.append(f"e.departement = ANY(${idx})")
    params.append(req.departements)

    # --- Taille (sauf TOUTES) ---
    if req.taille != TailleEntreprise.TOUTES:
        idx += 1
        conditions.append(f"e.tranche_effectif = ANY(${idx})")
        params.append(TAILLE_CODES[req.taille.value])

    # --- Critère métier : IDCC > NAF > Section NAF ---
    join_type = "LEFT JOIN"

    if req.idcc:
        idx += 1
        conditions.append(f"o.idcc = ${idx}")
        params.append(req.idcc)
        join_type = "INNER JOIN"
    elif req.naf:
        idx += 1
        conditions.append(f"e.naf LIKE ${idx}")
        params.append(f"{req.naf}%")
    elif req.section_naf:
        raise HTTPException(
            status_code=501,
            detail="Filtre section_naf non supporté. Utilisez le filtre naf avec "
                   "un préfixe à 2 chiffres (ex: naf='62' au lieu de section_naf='J')."
        )

    return " AND ".join(conditions), params, join_type


# ─────────────────────────────────────────────
# ENDPOINT PRINCIPAL : POST /search/prospects
# ─────────────────────────────────────────────
@router.post("/prospects", response_model=SearchProspectsResponse)
async def search_prospects(req: SearchProspectsRequest, request: Request):
    """
    Recherche de prospects dans la base SIRENE locale.
    Croise etablissements × siret_opco × idcc_libelles.
    Filtre par département, taille, IDCC et/ou NAF.
    """
    if not req.idcc and not req.naf and not req.section_naf:
        raise HTTPException(
            status_code=422,
            detail="Au moins un critère métier requis : idcc, naf ou section_naf"
        )

    where_clause, params, join_type = _build_query(req)

    pool = request.app.state.pool

    # ---- COUNT (optimisé : skip JOIN si pas de filtre sur siret_opco) ----
    if join_type == "LEFT JOIN":
        # NAF: pas de filtre sur o.*, le JOIN est inutile pour le count
        count_sql = f"SELECT COUNT(*) FROM etablissements e WHERE {where_clause}"
        count_params = params
    else:
        count_sql = f"""
            SELECT COUNT(*) FROM etablissements e
            {join_type} siret_opco o ON e.siret = o.siret
            WHERE {where_clause}
        """
        count_params = params

    # ---- DATA (avec pagination) ----
    limit_idx = len(params) + 1
    offset_idx = len(params) + 2
    data_params = params + [req.limit, req.offset]

    data_sql = f"""
        SELECT e.siret, e.siren, e.denomination, e.denomination_usuelle,
               e.enseigne, e.naf, e.code_postal, e.commune, e.departement,
               e.tranche_effectif, e.date_creation,
               e.numero_voie, e.type_voie, e.voie,
               COALESCE(o.opco_proprietaire, o.opco_gestion) AS opco,
               o.idcc,
               il.libelle AS convention_collective
        FROM etablissements e
        {join_type} siret_opco o ON e.siret = o.siret
        LEFT JOIN idcc_libelles il ON o.idcc = il.idcc
        WHERE {where_clause}
        ORDER BY e.commune, e.denomination
        LIMIT ${limit_idx} OFFSET ${offset_idx}
    """

    async def _run_count():
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL jit = off")
            return await conn.fetchval(count_sql, *count_params)

    async def _run_data():
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL jit = off")
            return await conn.fetch(data_sql, *data_params)

    try:
        total, rows = await asyncio.gather(_run_count(), _run_data())
    except Exception as e:
        logger.error(f"Erreur SQL search/prospects: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur base de données: {str(e)}")

    results = [
        ProspectResult(
            siret=r["siret"],
            siren=r["siren"],
            denomination=r["denomination"],
            denomination_usuelle=r["denomination_usuelle"],
            enseigne=r["enseigne"],
            naf=r["naf"],
            code_postal=r["code_postal"],
            commune=r["commune"],
            departement=r["departement"],
            tranche_effectif=r["tranche_effectif"],
            date_creation=str(r["date_creation"]) if r["date_creation"] else None,
            adresse=_build_address(r["numero_voie"], r["type_voie"], r["voie"]),
            opco=r["opco"],
            idcc=r["idcc"],
            convention_collective=r["convention_collective"],
        )
        for r in rows
    ]

    logger.info(f"search/prospects: {total} résultats, retourné {len(results)} (offset={req.offset})")

    return SearchProspectsResponse(
        total=total or 0,
        limit=req.limit,
        offset=req.offset,
        results=results,
    )


# ─────────────────────────────────────────────
# ENDPOINT COUNT : POST /search/prospects/count
# ─────────────────────────────────────────────
@router.post("/prospects/count")
async def search_prospects_count(req: SearchProspectsRequest, request: Request):
    """Retourne uniquement le count, sans les données. Rapide pour preview formulaire."""
    if not req.idcc and not req.naf and not req.section_naf:
        raise HTTPException(status_code=422, detail="Au moins un critère métier requis")

    where_clause, params, join_type = _build_query(req)

    pool = request.app.state.pool

    if join_type == "LEFT JOIN":
        count_sql = f"SELECT COUNT(*) FROM etablissements e WHERE {where_clause}"
    else:
        count_sql = f"""
            SELECT COUNT(*) FROM etablissements e
            {join_type} siret_opco o ON e.siret = o.siret
            WHERE {where_clause}
        """

    try:
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL jit = off")
            total = await conn.fetchval(count_sql, *params)
    except Exception as e:
        logger.error(f"Erreur SQL search/prospects/count: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur base de données: {str(e)}")

    return {"total": total or 0}


# ─────────────────────────────────────────────
# ENDPOINT UTILITAIRE : GET /search/regions
# ─────────────────────────────────────────────
@router.get("/regions")
async def list_regions():
    """Liste les régions et leurs départements pour le formulaire n8n."""
    return REGION_DEPARTEMENTS


# ─────────────────────────────────────────────
# ENDPOINT UTILITAIRE : GET /search/idcc
# ─────────────────────────────────────────────
@router.get("/idcc")
async def list_idcc(request: Request):
    """Liste les IDCC avec libellé et nombre d'établissements."""
    pool = request.app.state.pool

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT il.idcc, il.libelle, COUNT(o.siret) as nb_etablissements
                FROM idcc_libelles il
                JOIN siret_opco o ON il.idcc = o.idcc
                GROUP BY il.idcc, il.libelle
                HAVING COUNT(o.siret) > 0
                ORDER BY COUNT(o.siret) DESC
            """)
    except Exception as e:
        logger.error(f"Erreur SQL search/idcc: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur base de données: {str(e)}")

    return [
        {"idcc": r["idcc"], "libelle": r["libelle"], "count": r["nb_etablissements"]}
        for r in rows
    ]
