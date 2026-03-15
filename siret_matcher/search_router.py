"""
Router FastAPI pour les endpoints /search/*
Fichier isolé — rattaché à l'app via un seul app.include_router() dans api.py.
Rollback = commenter 2 lignes dans api.py + restart.
"""
import asyncio
import json
import logging
import time

from fastapi import APIRouter, HTTPException, Query, Request

from .logging_config import log_structured

from .search_models import (
    AutocompleteResult,
    REGION_DEPARTEMENTS,
    TAILLE_CODES,
    ProspectResult,
    SearchProspectsRequest,
    SearchProspectsResponse,
    TailleEntreprise,
)

logger = logging.getLogger("siret_matcher.search")

router = APIRouter(prefix="/search", tags=["search"])

# Préfixe Redis pour le cache autocomplete
_AUTOCOMPLETE_PREFIX = "autocomplete:"
_AUTOCOMPLETE_TTL = 300  # 5 minutes


def _build_address(numero_voie, type_voie, voie) -> str | None:
    """Reconstitue l'adresse en une seule chaîne."""
    parts = [p for p in [numero_voie, type_voie, voie] if p]
    return " ".join(parts) if parts else None


async def _has_search_vector(pool) -> bool:
    """Vérifie si la colonne search_vector existe (migration faite)."""
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name = 'etablissements' AND column_name = 'search_vector'"
        )
    return val > 0


class _QueryBuilder:
    """Construit des requêtes SQL paramétrées pour la recherche de prospects."""

    def __init__(self):
        self.conditions: list[str] = ["e.etat_administratif = 'A'"]
        self.params: list = []
        self.idx: int = 0
        self.join_type: str = "LEFT JOIN"
        self.order_by: str = "e.commune, e.denomination"
        self.has_fulltext: bool = False

    def add_departements(self, departements: list[str]):
        self.idx += 1
        self.conditions.append(f"e.departement = ANY(${self.idx})")
        self.params.append(departements)

    def add_taille(self, taille: TailleEntreprise):
        if taille != TailleEntreprise.TOUTES:
            self.idx += 1
            self.conditions.append(f"e.tranche_effectif = ANY(${self.idx})")
            self.params.append(TAILLE_CODES[taille.value])

    def add_idcc(self, idcc: str):
        self.idx += 1
        self.conditions.append(f"o.idcc = ${self.idx}")
        self.params.append(idcc)
        self.join_type = "INNER JOIN"

    def add_naf(self, naf: str):
        self.idx += 1
        self.conditions.append(f"e.naf LIKE ${self.idx}")
        self.params.append(f"{naf}%")

    def add_fulltext(self, q: str):
        self.idx += 1
        self.conditions.append(
            f"e.search_vector @@ plainto_tsquery('french', ${self.idx})"
        )
        self.params.append(q)
        self.has_fulltext = True
        # Trier par pertinence full-text
        self.order_by = f"ts_rank(e.search_vector, plainto_tsquery('french', ${self.idx})) DESC, e.denomination"

    def add_fulltext_trigram_fallback(self, q: str):
        """Fallback trigramme si search_vector n'existe pas."""
        self.idx += 1
        self.conditions.append(
            f"(e.denomination_clean % ${self.idx} OR e.enseigne_clean % ${self.idx})"
        )
        self.params.append(q)
        self.order_by = (
            f"GREATEST(similarity(e.denomination_clean, ${self.idx}), "
            f"similarity(e.enseigne_clean, ${self.idx})) DESC, e.denomination"
        )

    def where_clause(self) -> str:
        return " AND ".join(self.conditions)

    def build_count_sql(self) -> tuple[str, list]:
        where = self.where_clause()
        if self.join_type == "LEFT JOIN":
            sql = f"SELECT COUNT(*) FROM etablissements e WHERE {where}"
        else:
            sql = (
                f"SELECT COUNT(*) FROM etablissements e "
                f"{self.join_type} siret_opco o ON e.siret = o.siret "
                f"WHERE {where}"
            )
        return sql, list(self.params)

    def build_data_sql(self, limit: int, offset: int) -> tuple[str, list]:
        where = self.where_clause()
        limit_idx = self.idx + 1
        offset_idx = self.idx + 2
        params = list(self.params) + [limit, offset]

        sql = f"""
            SELECT e.siret, e.siren, e.denomination, e.denomination_usuelle,
                   e.enseigne, e.naf, e.code_postal, e.commune, e.departement,
                   e.tranche_effectif, e.date_creation,
                   e.numero_voie, e.type_voie, e.voie,
                   COALESCE(o.opco_proprietaire, o.opco_gestion) AS opco,
                   o.idcc,
                   il.libelle AS convention_collective
            FROM etablissements e
            {self.join_type} siret_opco o ON e.siret = o.siret
            LEFT JOIN idcc_libelles il ON o.idcc = il.idcc
            WHERE {where}
            ORDER BY {self.order_by}
            LIMIT ${limit_idx} OFFSET ${offset_idx}
        """
        return sql, params


def _build_query(req: SearchProspectsRequest, has_sv: bool) -> _QueryBuilder:
    """Construit le QueryBuilder à partir de la requête."""
    qb = _QueryBuilder()

    # Départements (obligatoire)
    qb.add_departements(req.departements)

    # Taille
    qb.add_taille(req.taille)

    # Full-text search
    if req.q and req.q.strip():
        q_clean = req.q.strip()
        if has_sv:
            qb.add_fulltext(q_clean)
        else:
            qb.add_fulltext_trigram_fallback(q_clean)

    # Critère métier : IDCC > NAF > Section NAF
    if req.idcc:
        qb.add_idcc(req.idcc)
    elif req.naf:
        qb.add_naf(req.naf)
    elif req.section_naf:
        raise HTTPException(
            status_code=501,
            detail="Filtre section_naf non supporté. Utilisez le filtre naf avec "
                   "un préfixe à 2 chiffres (ex: naf='62' au lieu de section_naf='J')."
        )

    return qb


# ─────────────────────────────────────────────
# ENDPOINT : GET /search/autocomplete
# ─────────────────────────────────────────────
@router.get("/autocomplete", response_model=list[AutocompleteResult])
async def autocomplete(
    request: Request,
    q: str = Query(..., description="Terme de recherche (min 2 caractères)"),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Autocomplétion rapide sur les établissements actifs.

    Combine full-text search (tsvector) et trigrammes pour le fuzzy matching.
    Résultats mis en cache Redis 5 min.
    """
    t0 = time.perf_counter()
    q = q.strip()
    if len(q) < 2:
        return []

    pool = request.app.state.pool

    # Vérifier le cache Redis
    from siret_matcher import cache as siret_cache
    cache_key = f"{_AUTOCOMPLETE_PREFIX}{q.lower()}:{limit}"
    if siret_cache.is_connected():
        try:
            cached = await siret_cache._redis.get(cache_key)
            if cached is not None:
                duration_ms = round((time.perf_counter() - t0) * 1000, 1)
                log_structured(
                    logger, logging.DEBUG, "autocomplete",
                    q=q, results=len(json.loads(cached)), cache="hit",
                    duration_ms=duration_ms,
                )
                return json.loads(cached)
        except Exception:
            pass

    # Déterminer si search_vector est disponible
    has_sv = await _has_search_vector(pool)

    if has_sv:
        # Full-text + trigrammes combinés, triés par pertinence
        sql = """
            SELECT siret, denomination, commune, code_postal, naf
            FROM etablissements
            WHERE etat_administratif = 'A'
              AND (
                  search_vector @@ plainto_tsquery('french', $1)
                  OR denomination_clean % $1
                  OR enseigne_clean % $1
              )
            ORDER BY (
                ts_rank(search_vector, plainto_tsquery('french', $1)) * 2
                + GREATEST(similarity(denomination_clean, $1), similarity(enseigne_clean, $1))
            ) DESC
            LIMIT $2
        """
    else:
        # Fallback trigrammes seuls
        sql = """
            SELECT siret, denomination, commune, code_postal, naf
            FROM etablissements
            WHERE etat_administratif = 'A'
              AND (denomination_clean % $1 OR enseigne_clean % $1)
            ORDER BY GREATEST(
                similarity(denomination_clean, $1),
                similarity(enseigne_clean, $1)
            ) DESC
            LIMIT $2
        """

    try:
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL jit = off")
            rows = await conn.fetch(sql, q, limit)
    except Exception as e:
        logger.error(f"Erreur SQL autocomplete: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur base de données: {str(e)}")

    results = [
        AutocompleteResult(
            siret=r["siret"],
            denomination=r["denomination"],
            commune=r["commune"],
            code_postal=r["code_postal"],
            naf=r["naf"],
        )
        for r in rows
    ]

    # Mettre en cache
    if siret_cache.is_connected():
        try:
            data = json.dumps([r.model_dump() for r in results])
            await siret_cache._redis.set(cache_key, data, ex=_AUTOCOMPLETE_TTL)
        except Exception:
            pass

    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    log_structured(
        logger, logging.INFO, "autocomplete",
        q=q, results=len(results), cache="miss",
        duration_ms=duration_ms,
    )

    return results


# ─────────────────────────────────────────────
# ENDPOINT PRINCIPAL : POST /search/prospects
# ─────────────────────────────────────────────
@router.post("/prospects", response_model=SearchProspectsResponse)
async def search_prospects(req: SearchProspectsRequest, request: Request):
    """
    Recherche de prospects dans la base SIRENE locale.
    Croise etablissements × siret_opco × idcc_libelles.
    Filtre par département, taille, IDCC et/ou NAF.
    Supporte la recherche full-text via le champ q.
    """
    t0 = time.perf_counter()

    # Au moins un critère métier requis (sauf si q est renseigné)
    if not req.q and not req.idcc and not req.naf and not req.section_naf:
        raise HTTPException(
            status_code=422,
            detail="Au moins un critère métier requis : q, idcc, naf ou section_naf"
        )

    pool = request.app.state.pool
    has_sv = await _has_search_vector(pool) if req.q else False

    qb = _build_query(req, has_sv)

    count_sql, count_params = qb.build_count_sql()
    data_sql, data_params = qb.build_data_sql(req.limit, req.offset)

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

    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    log_structured(
        logger, logging.INFO, "search",
        q=req.q or "",
        departements=req.departements,
        taille=req.taille.value if req.taille else None,
        results_count=total or 0,
        duration_ms=duration_ms,
    )

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
    if not req.q and not req.idcc and not req.naf and not req.section_naf:
        raise HTTPException(status_code=422, detail="Au moins un critère métier requis")

    pool = request.app.state.pool
    has_sv = await _has_search_vector(pool) if req.q else False

    qb = _build_query(req, has_sv)
    count_sql, count_params = qb.build_count_sql()

    try:
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL jit = off")
            total = await conn.fetchval(count_sql, *count_params)
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
