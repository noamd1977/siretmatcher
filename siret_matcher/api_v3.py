"""Router API v3 — endpoints structurés pour le frontend React.

Monté dans api.py via app.include_router(api_v3_router).
Les endpoints legacy restent inchangés (rétrocompatibilité).
"""
import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from siret_matcher.auth import require_api_key
from siret_matcher.logging_config import log_structured
from siret_matcher.lookups import (
    DEPT_TO_REGION,
    IDCC_TO_CONVENTION,
    NAF_LIBELLES,
    NAF_TO_OPCO,
    TRANCHE_MAP,
)
from siret_matcher.models_v3 import (
    AdresseInfo,
    AutocompleteResult,
    BatchRequest,
    BatchResponse,
    EffectifInfo,
    EtablissementResponse,
    IdccInfo,
    MatchDebug,
    MatchRequest,
    MatchResponse,
    NafInfo,
    OpcoInfo,
    OpcoReferentiel,
    SearchFacets,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    SortField,
    StageDebug,
)
from siret_matcher.search_models import REGION_DEPARTEMENTS, TAILLE_CODES

logger = logging.getLogger("siret_matcher.api_v3")

router = APIRouter(prefix="/api/v3", tags=["v3"])

# Cache Redis pour autocomplete (réutilise le même préfixe)
_AUTOCOMPLETE_PREFIX = "v3:autocomplete:"
_AUTOCOMPLETE_TTL = 300  # 5 min


# ── Helpers ─────────────────────────────────────────────────────────────────


def _dept_from_cp(code_postal: str) -> str:
    if not code_postal:
        return ""
    return code_postal[:3] if code_postal.startswith("97") else code_postal[:2]


def _confidence(score: float) -> str:
    if score >= 65:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _build_etablissement(row, opco_name: str = "", opco_source: str = "",
                         idcc_code: str = "", idcc_libelle: str = "") -> EtablissementResponse:
    """Construit un EtablissementResponse à partir d'une row asyncpg."""
    naf_code = row["naf"] or ""
    tranche = row["tranche_effectif"] or ""
    cp = row["code_postal"] or ""
    dept = _dept_from_cp(cp)

    # Voie : reconstitution
    parts = [p for p in [row.get("numero_voie"), row.get("type_voie"), row.get("voie")] if p]
    voie = " ".join(parts) if parts else None

    return EtablissementResponse(
        siret=row["siret"],
        siren=row["siren"],
        denomination=row.get("denomination"),
        enseigne=row.get("enseigne") or row.get("denomination_usuelle"),
        naf=NafInfo(code=naf_code, libelle=NAF_LIBELLES.get(naf_code, "")),
        effectif=EffectifInfo(code=tranche, libelle=TRANCHE_MAP.get(tranche, "")),
        adresse=AdresseInfo(
            numero=row.get("numero_voie"),
            voie=voie,
            code_postal=cp,
            commune=row.get("commune"),
            departement=dept,
            region=DEPT_TO_REGION.get(dept, ""),
        ),
        opco=OpcoInfo(nom=opco_name or None, source=opco_source or None),
        idcc=IdccInfo(code=idcc_code or None, libelle=idcc_libelle or None),
        date_creation=str(row["date_creation"]) if row.get("date_creation") else None,
        etat_administratif=row.get("etat_administratif"),
    )


async def _has_search_vector(pool) -> bool:
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name = 'etablissements' AND column_name = 'search_vector'"
        )
    return val > 0


# ── GET /api/v3/etablissements/{siret} ──────────────────────────────────────


@router.get(
    "/etablissements/{siret}",
    response_model=EtablissementResponse,
    tags=["referentiel"],
    summary="Lookup enrichi d'un établissement",
)
async def get_etablissement(request: Request, siret: str):
    """Retourne la fiche complète d'un établissement par SIRET."""
    import re
    if not re.match(r"^\d{14}$", siret):
        raise HTTPException(status_code=400, detail="Format SIRET invalide (14 chiffres attendus)")

    pool = request.app.state.pool
    async with pool.acquire() as conn:
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
        raise HTTPException(status_code=404, detail="SIRET non trouvé")

    opco = row["opco_proprietaire"] or row["opco_gestion"] or ""
    source_opco = "FRANCE_COMPETENCES" if opco else ""

    # Fallback OPCO par NAF
    if not opco:
        naf_prefix = (row["naf"] or "").replace(".", "")[:2]
        if naf_prefix in NAF_TO_OPCO:
            opco = NAF_TO_OPCO[naf_prefix]
            source_opco = "NAF"

    return _build_etablissement(
        row,
        opco_name=opco,
        opco_source=source_opco,
        idcc_code=row["idcc"] or "",
        idcc_libelle=row["convention_libelle"] or "",
    )


# ── POST /api/v3/match ─────────────────────────────────────────────────────


@router.post(
    "/match",
    response_model=MatchResponse,
    tags=["match"],
    summary="Matching intelligent d'un prospect",
)
async def match_v3(
    req: MatchRequest,
    request: Request,
    api_key_name: str = Depends(require_api_key),
):
    """Matching intelligent : même pipeline 5 étapes, réponse structurée v3."""
    from siret_matcher.matcher import match_one
    from siret_matcher.models import Prospect

    want_debug = request.headers.get("X-Debug", "").lower() == "true"

    prospect = Prospect(
        nom=req.nom,
        adresse=req.adresse,
        code_postal=req.code_postal,
        ville=req.ville,
        telephone=req.telephone,
        site_web=req.site_web,
        email=req.email,
    )

    t0 = time.perf_counter()
    http_client = request.app.state.http_client
    db_obj = request.app.state.db

    try:
        result = await match_one(http_client, db_obj, prospect, use_db=True)
    except Exception as e:
        logger.error(f"Erreur matching v3: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    r = result.result
    stages_tried = getattr(result, "_stages_tried", 0)

    if r and r.siret and r.methode != "NON_TROUVE":
        # Vérifier actif
        pool = request.app.state.pool
        async with pool.acquire() as conn:
            check = await conn.fetchval(
                "SELECT siret FROM etablissements WHERE siret = $1 AND etat_administratif = 'A'",
                r.siret,
            )
        if not check:
            # Radié — on traite comme non trouvé
            r = None

    if r and r.siret and r.methode != "NON_TROUVE":
        # Enrichir OPCO
        pool = request.app.state.pool
        async with pool.acquire() as conn:
            opco_row = await conn.fetchrow(
                """SELECT o.opco_proprietaire, o.opco_gestion, o.idcc,
                          il.libelle AS convention_libelle
                   FROM siret_opco o
                   LEFT JOIN idcc_libelles il ON o.idcc = il.idcc
                   WHERE o.siret = $1""",
                r.siret,
            )
            etab_row = await conn.fetchrow(
                """SELECT siret, siren, denomination, denomination_usuelle, enseigne,
                          naf, numero_voie, type_voie, voie, code_postal, commune,
                          tranche_effectif, date_creation, etat_administratif
                   FROM etablissements WHERE siret = $1""",
                r.siret,
            )

        opco_name = ""
        opco_source = ""
        idcc_code = ""
        idcc_libelle = ""
        if opco_row:
            opco_name = opco_row["opco_proprietaire"] or opco_row["opco_gestion"] or ""
            opco_source = "FRANCE_COMPETENCES" if opco_name else ""
            idcc_code = opco_row["idcc"] or ""
            idcc_libelle = opco_row["convention_libelle"] or ""

        if not opco_name and etab_row:
            naf_prefix = (etab_row["naf"] or "").replace(".", "")[:2]
            if naf_prefix in NAF_TO_OPCO:
                opco_name = NAF_TO_OPCO[naf_prefix]
                opco_source = "NAF"

        etab = _build_etablissement(
            etab_row, opco_name, opco_source, idcc_code, idcc_libelle
        ) if etab_row else None

        score = r.score
        debug_info = None
        if want_debug:
            debug_info = MatchDebug(
                stages_tried=stages_tried,
                duration_ms=duration_ms,
                stages=[StageDebug(
                    name=r.methode, found=True, score=r.score, duration_ms=duration_ms
                )],
            )

        log_structured(
            logger, logging.INFO, "match_v3",
            api_key=api_key_name, prospect_name=req.nom,
            matched=True, siret=r.siret, score=score,
            duration_ms=duration_ms,
        )

        return MatchResponse(
            matched=True,
            confidence=_confidence(score),
            score=score,
            methode=r.methode,
            etablissement=etab,
            debug=debug_info,
        )
    else:
        debug_info = None
        if want_debug:
            debug_info = MatchDebug(
                stages_tried=stages_tried,
                duration_ms=duration_ms,
                stages=[],
            )

        log_structured(
            logger, logging.INFO, "match_v3",
            api_key=api_key_name, prospect_name=req.nom,
            matched=False, duration_ms=duration_ms,
        )

        return MatchResponse(
            matched=False,
            confidence="low",
            score=0.0,
            methode="NON_TROUVE",
            etablissement=None,
            debug=debug_info,
        )


# ── POST /api/v3/match/batch ───────────────────────────────────────────────


@router.post(
    "/match/batch",
    response_model=BatchResponse,
    tags=["match"],
    summary="Matching en lot",
)
async def match_batch_v3(
    req: BatchRequest,
    request: Request,
    api_key_name: str = Depends(require_api_key),
):
    """Matching en lot avec parallélisme contrôlé."""
    from siret_matcher.matcher import match_one
    from siret_matcher.models import Prospect

    prospects = [
        Prospect(
            nom=p.nom, adresse=p.adresse, code_postal=p.code_postal,
            ville=p.ville, telephone=p.telephone, site_web=p.site_web,
            email=p.email,
        )
        for p in req.prospects
    ]

    t0 = time.perf_counter()
    http_client = request.app.state.http_client
    db_obj = request.app.state.db
    sem = asyncio.Semaphore(req.concurrency)

    async def _match_one(p: Prospect) -> MatchResponse:
        async with sem:
            try:
                result = await match_one(http_client, db_obj, p, use_db=True)
            except Exception:
                return MatchResponse(matched=False, confidence="low", score=0.0,
                                     methode="ERREUR")

        r = result.result
        if r and r.siret and r.methode != "NON_TROUVE":
            # Enrichir
            pool = request.app.state.pool
            async with pool.acquire() as conn:
                etab_row = await conn.fetchrow(
                    """SELECT siret, siren, denomination, denomination_usuelle, enseigne,
                              naf, numero_voie, type_voie, voie, code_postal, commune,
                              tranche_effectif, date_creation, etat_administratif
                       FROM etablissements WHERE siret = $1""",
                    r.siret,
                )
                opco_row = await conn.fetchrow(
                    """SELECT o.opco_proprietaire, o.opco_gestion, o.idcc,
                              il.libelle AS convention_libelle
                       FROM siret_opco o
                       LEFT JOIN idcc_libelles il ON o.idcc = il.idcc
                       WHERE o.siret = $1""",
                    r.siret,
                )

            opco_name, opco_source, idcc_code, idcc_libelle = "", "", "", ""
            if opco_row:
                opco_name = opco_row["opco_proprietaire"] or opco_row["opco_gestion"] or ""
                opco_source = "FRANCE_COMPETENCES" if opco_name else ""
                idcc_code = opco_row["idcc"] or ""
                idcc_libelle = opco_row["convention_libelle"] or ""
            if not opco_name and etab_row:
                naf_prefix = (etab_row["naf"] or "").replace(".", "")[:2]
                if naf_prefix in NAF_TO_OPCO:
                    opco_name = NAF_TO_OPCO[naf_prefix]
                    opco_source = "NAF"

            etab = _build_etablissement(
                etab_row, opco_name, opco_source, idcc_code, idcc_libelle
            ) if etab_row else None

            return MatchResponse(
                matched=True,
                confidence=_confidence(r.score),
                score=r.score,
                methode=r.methode,
                etablissement=etab,
            )
        else:
            return MatchResponse(matched=False, confidence="low", score=0.0,
                                 methode="NON_TROUVE")

    results = await asyncio.gather(*[_match_one(p) for p in prospects])
    duration_ms = round((time.perf_counter() - t0) * 1000, 1)

    matched_count = sum(1 for r in results if r.matched)
    total = len(results)

    log_structured(
        logger, logging.INFO, "match_batch_v3",
        api_key=api_key_name, total=total, matched=matched_count,
        duration_ms=duration_ms,
    )

    return BatchResponse(
        total=total,
        matched=matched_count,
        not_found=total - matched_count,
        taux_matching=round(matched_count / total, 4) if total else 0.0,
        duration_ms=duration_ms,
        results=list(results),
    )


# ── POST /api/v3/search ────────────────────────────────────────────────────


@router.post(
    "/search",
    response_model=SearchResponse,
    tags=["search"],
    summary="Recherche unifiée avec facets",
)
async def search_v3(req: SearchRequest, request: Request):
    """Recherche avancée avec full-text, filtres et facets pour le frontend."""
    t0 = time.perf_counter()
    pool = request.app.state.pool
    f = req.filters

    # Au moins un critère requis
    if not req.q and not f.idcc and not f.naf_prefix and not f.departements:
        raise HTTPException(
            status_code=422,
            detail="Au moins un critère requis : q, departements, idcc ou naf_prefix"
        )

    has_sv = await _has_search_vector(pool) if req.q else False

    # Build parameterized query
    conditions = [f"e.etat_administratif = '{f.etat}'"]
    params: list = []
    idx = 0
    join_type = "LEFT JOIN"
    order_by = "e.commune, e.denomination"

    if f.departements:
        idx += 1
        conditions.append(f"e.departement = ANY(${idx})")
        params.append(f.departements)

    if f.taille and f.taille.value != "TOUTES":
        idx += 1
        conditions.append(f"e.tranche_effectif = ANY(${idx})")
        params.append(TAILLE_CODES[f.taille.value])

    if req.q and req.q.strip():
        q_clean = req.q.strip()
        if has_sv:
            idx += 1
            conditions.append(f"e.search_vector @@ plainto_tsquery('french', ${idx})")
            params.append(q_clean)
            if req.sort == SortField.RELEVANCE:
                order_by = f"ts_rank(e.search_vector, plainto_tsquery('french', ${idx})) DESC, e.denomination"
        else:
            idx += 1
            conditions.append(
                f"(e.denomination_clean % ${idx} OR e.enseigne_clean % ${idx})"
            )
            params.append(q_clean)
            if req.sort == SortField.RELEVANCE:
                order_by = (
                    f"GREATEST(similarity(e.denomination_clean, ${idx}), "
                    f"similarity(e.enseigne_clean, ${idx})) DESC, e.denomination"
                )

    if f.idcc:
        idx += 1
        conditions.append(f"o.idcc = ${idx}")
        params.append(f.idcc)
        join_type = "INNER JOIN"
    elif f.naf_prefix:
        idx += 1
        conditions.append(f"e.naf LIKE ${idx}")
        params.append(f"{f.naf_prefix}%")

    if req.sort == SortField.DENOMINATION:
        order_by = "e.denomination"
    elif req.sort == SortField.CODE_POSTAL:
        order_by = "e.code_postal, e.denomination"

    where = " AND ".join(conditions)

    # Count query
    if join_type == "LEFT JOIN":
        count_sql = f"SELECT COUNT(*) FROM etablissements e WHERE {where}"
    else:
        count_sql = (
            f"SELECT COUNT(*) FROM etablissements e "
            f"{join_type} siret_opco o ON e.siret = o.siret WHERE {where}"
        )

    # Data query
    limit_idx = idx + 1
    offset_idx = idx + 2
    data_params = list(params) + [req.limit, req.offset]

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
        WHERE {where}
        ORDER BY {order_by}
        LIMIT ${limit_idx} OFFSET ${offset_idx}
    """

    # Facet queries — départements, tailles, top NAF
    # Only compute facets when we have enough filters to make them meaningful
    base_where = where
    base_join = join_type

    facet_dept_sql = f"""
        SELECT e.departement, COUNT(*) AS cnt
        FROM etablissements e
        {base_join} siret_opco o ON e.siret = o.siret
        WHERE {base_where}
        GROUP BY e.departement
        ORDER BY cnt DESC LIMIT 20
    """ if join_type == "INNER JOIN" else f"""
        SELECT e.departement, COUNT(*) AS cnt
        FROM etablissements e
        WHERE {base_where}
        GROUP BY e.departement
        ORDER BY cnt DESC LIMIT 20
    """

    facet_taille_sql = f"""
        SELECT
            CASE
                WHEN e.tranche_effectif IN ('NN','00','01','02','03') THEN 'MOINS_11'
                WHEN e.tranche_effectif IN ('11','12') THEN 'DE_11_A_49'
                WHEN e.tranche_effectif IN ('21','22','31','32','41','42','51','52','53') THEN 'PLUS_DE_50'
                ELSE 'AUTRE'
            END AS taille,
            COUNT(*) AS cnt
        FROM etablissements e
        {"" if join_type == "LEFT JOIN" else f"{join_type} siret_opco o ON e.siret = o.siret"}
        WHERE {base_where}
        GROUP BY taille
    """

    facet_naf_sql = f"""
        SELECT e.naf, COUNT(*) AS cnt
        FROM etablissements e
        {"" if join_type == "LEFT JOIN" else f"{join_type} siret_opco o ON e.siret = o.siret"}
        WHERE {base_where}
        GROUP BY e.naf
        ORDER BY cnt DESC LIMIT 10
    """

    try:
        async def _count():
            async with pool.acquire() as conn:
                await conn.execute("SET LOCAL jit = off")
                return await conn.fetchval(count_sql, *params)

        async def _data():
            async with pool.acquire() as conn:
                await conn.execute("SET LOCAL jit = off")
                return await conn.fetch(data_sql, *data_params)

        async def _facet_dept():
            async with pool.acquire() as conn:
                await conn.execute("SET LOCAL jit = off")
                return await conn.fetch(facet_dept_sql, *params)

        async def _facet_taille():
            async with pool.acquire() as conn:
                await conn.execute("SET LOCAL jit = off")
                return await conn.fetch(facet_taille_sql, *params)

        async def _facet_naf():
            async with pool.acquire() as conn:
                await conn.execute("SET LOCAL jit = off")
                return await conn.fetch(facet_naf_sql, *params)

        total, rows, dept_rows, taille_rows, naf_rows = await asyncio.gather(
            _count(), _data(), _facet_dept(), _facet_taille(), _facet_naf()
        )
    except Exception as e:
        logger.error(f"Erreur SQL search v3: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur base de données: {str(e)}")

    results = []
    for r in rows:
        naf_code = r["naf"] or ""
        tranche = r["tranche_effectif"] or ""
        cp = r["code_postal"] or ""
        dept = _dept_from_cp(cp)
        parts = [p for p in [r["numero_voie"], r["type_voie"], r["voie"]] if p]

        results.append(SearchResultItem(
            siret=r["siret"],
            siren=r["siren"],
            denomination=r["denomination"],
            enseigne=r["enseigne"] or r["denomination_usuelle"],
            naf=NafInfo(code=naf_code, libelle=NAF_LIBELLES.get(naf_code, "")),
            effectif=EffectifInfo(code=tranche, libelle=TRANCHE_MAP.get(tranche, "")),
            adresse=AdresseInfo(
                numero=r["numero_voie"],
                voie=" ".join(parts) if parts else None,
                code_postal=cp,
                commune=r["commune"],
                departement=dept,
                region=DEPT_TO_REGION.get(dept, ""),
            ),
            opco=r["opco"],
            idcc=IdccInfo(code=r["idcc"], libelle=r["convention_collective"]),
            date_creation=str(r["date_creation"]) if r["date_creation"] else None,
        ))

    # Build facets
    facets = SearchFacets(
        departements={row["departement"]: row["cnt"] for row in dept_rows if row["departement"]},
        tailles={row["taille"]: row["cnt"] for row in taille_rows if row["taille"] != "AUTRE"},
        top_naf=[
            {"code": row["naf"], "libelle": NAF_LIBELLES.get(row["naf"] or "", ""), "count": row["cnt"]}
            for row in naf_rows if row["naf"]
        ],
    )

    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    log_structured(
        logger, logging.INFO, "search_v3",
        q=req.q or "", total=total or 0, duration_ms=duration_ms,
    )

    return SearchResponse(total=total or 0, results=results, facets=facets)


# ── GET /api/v3/autocomplete ────────────────────────────────────────────────


@router.get(
    "/autocomplete",
    response_model=list[AutocompleteResult],
    tags=["search"],
    summary="Autocomplétion rapide",
)
async def autocomplete_v3(
    request: Request,
    q: str = Query(..., description="Terme de recherche (min 2 caractères)"),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Autocomplétion sur les établissements actifs (full-text + trigrammes)."""
    q = q.strip()
    if len(q) < 2:
        return []

    pool = request.app.state.pool

    # Cache Redis
    from siret_matcher import cache as siret_cache
    cache_key = f"{_AUTOCOMPLETE_PREFIX}{q.lower()}:{limit}"
    if siret_cache.is_connected():
        try:
            cached = await siret_cache._redis.get(cache_key)
            if cached is not None:
                return json.loads(cached)
        except Exception:
            pass

    has_sv = await _has_search_vector(pool)

    if has_sv:
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
        logger.error(f"Erreur SQL autocomplete v3: {e}")
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

    # Cache
    if siret_cache.is_connected():
        try:
            data = json.dumps([r.model_dump() for r in results])
            await siret_cache._redis.set(cache_key, data, ex=_AUTOCOMPLETE_TTL)
        except Exception:
            pass

    return results


# ── GET /api/v3/referentiel/regions ─────────────────────────────────────────


@router.get(
    "/referentiel/regions",
    tags=["referentiel"],
    summary="Liste des régions et départements",
)
async def list_regions_v3():
    """Régions et leurs départements pour alimenter les selects."""
    return REGION_DEPARTEMENTS


# ── GET /api/v3/referentiel/idcc ────────────────────────────────────────────


@router.get(
    "/referentiel/idcc",
    tags=["referentiel"],
    summary="Liste des IDCC avec libellé et nombre d'établissements",
)
async def list_idcc_v3(request: Request):
    """Liste les conventions collectives avec leur nombre d'établissements."""
    pool = request.app.state.pool
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT il.idcc, il.libelle, COUNT(o.siret) AS nb
                FROM idcc_libelles il
                JOIN siret_opco o ON il.idcc = o.idcc
                GROUP BY il.idcc, il.libelle
                HAVING COUNT(o.siret) > 0
                ORDER BY COUNT(o.siret) DESC
            """)
    except Exception as e:
        logger.error(f"Erreur SQL referentiel/idcc v3: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur base de données: {str(e)}")

    return [{"idcc": r["idcc"], "libelle": r["libelle"], "count": r["nb"]} for r in rows]


# ── GET /api/v3/referentiel/opco ────────────────────────────────────────────


# Référentiel OPCO statique (11 OPCO depuis la réforme 2019)
_OPCO_REFERENTIEL = [
    OpcoReferentiel(nom="AFDAS", secteurs="Culture, médias, loisirs, sport"),
    OpcoReferentiel(nom="AKTO", secteurs="Travail temporaire, propreté, sécurité"),
    OpcoReferentiel(nom="ATLAS", secteurs="Numérique, ingénierie, conseil, études"),
    OpcoReferentiel(nom="CONSTRUCTYS", secteurs="Bâtiment, travaux publics"),
    OpcoReferentiel(nom="OCAPIAT", secteurs="Agriculture, pêche, agroalimentaire"),
    OpcoReferentiel(nom="OPCO 2I", secteurs="Interindustriel (chimie, pharma, métallurgie…)"),
    OpcoReferentiel(nom="OPCO COMMERCE", secteurs="Commerce, distribution"),
    OpcoReferentiel(nom="OPCO EP", secteurs="Entreprises de proximité (artisanat, libéral…)"),
    OpcoReferentiel(nom="OPCO MOBILITES", secteurs="Transports, logistique"),
    OpcoReferentiel(nom="OPCO SANTE", secteurs="Santé, médico-social"),
    OpcoReferentiel(nom="UNIFORMATION", secteurs="Cohésion sociale (ESS, habitat, insertion…)"),
]


@router.get(
    "/referentiel/opco",
    response_model=list[OpcoReferentiel],
    tags=["referentiel"],
    summary="Liste des OPCO",
)
async def list_opco_v3():
    """Liste les 11 OPCO avec leurs secteurs d'activité."""
    return _OPCO_REFERENTIEL


# ── GET /api/v3/stats ──────────────────────────────────────────────────────


@router.get(
    "/stats",
    tags=["system"],
    summary="Métriques de matching et système",
)
async def get_stats(request: Request):
    """Retourne les métriques Prometheus en JSON pour le dashboard."""
    from siret_matcher.metrics import (
        MATCH_TOTAL, MATCH_SCORE, MATCH_METHOD, MATCH_STAGES_TRIED,
        DST_LOOKUP_TOTAL, CACHE_HITS, CACHE_MISSES,
        DB_POOL_SIZE, ETABLISSEMENTS_COUNT,
    )
    from siret_matcher import cache as siret_cache
    import psutil

    # Matching metrics
    matched = MATCH_TOTAL.labels(result="matched")._value.get()
    not_found = MATCH_TOTAL.labels(result="not_found")._value.get()
    total_match = matched + not_found

    # Score avg from histogram — extract sum and count from collect()
    score_sum = 0.0
    score_count = 0.0
    for metric in MATCH_SCORE.collect():
        for sample in metric.samples:
            if sample.name.endswith("_sum"):
                score_sum = sample.value
            elif sample.name.endswith("_count"):
                score_count = sample.value
    avg_score = round(score_sum / score_count, 1) if score_count > 0 else 0

    # Methods
    by_method = {}
    for method_name in [
        "API_RECHERCHE_EXACT", "API_RECHERCHE_PROBABLE", "API_RECHERCHE_CP",
        "ADDRESS_UNIQUE", "ADDRESS_BEST",
        "TRIGRAM_FUZZY", "TRIGRAM_BEST",
        "SCRAPE_MENTIONS_LEGALES", "NON_TROUVE",
    ]:
        val = MATCH_METHOD.labels(method=method_name)._value.get()
        if val > 0:
            by_method[method_name] = int(val)

    # DST lookups
    dst_found = DST_LOOKUP_TOTAL.labels(found="true")._value.get()
    dst_not_found = DST_LOOKUP_TOTAL.labels(found="false")._value.get()
    dst_total = dst_found + dst_not_found

    cache_hits = CACHE_HITS._value.get()
    cache_misses = CACHE_MISSES._value.get()
    cache_total = cache_hits + cache_misses
    cache_hit_rate = round(cache_hits / cache_total, 3) if cache_total > 0 else 0

    # System
    pool = request.app.state.pool
    etab_count = ETABLISSEMENTS_COUNT._value.get()
    pool_size = DB_POOL_SIZE._value.get()

    opco_count = 0
    try:
        async with pool.acquire() as conn:
            opco_count = await conn.fetchval("SELECT COUNT(*) FROM siret_opco")
    except Exception:
        pass

    redis_ok = siret_cache.is_connected()

    # Uptime
    uptime = 0
    try:
        proc = psutil.Process()
        uptime = int(time.time() - proc.create_time())
    except Exception:
        pass

    return {
        "matching": {
            "total": int(total_match),
            "matched": int(matched),
            "not_found": int(not_found),
            "taux": round(matched / total_match, 3) if total_match > 0 else 0,
            "avg_score": avg_score,
            "by_method": by_method,
        },
        "dst_lookups": {
            "total": int(dst_total),
            "found": int(dst_found),
            "not_found": int(dst_not_found),
            "cache_hit_rate": cache_hit_rate,
        },
        "system": {
            "etablissements_actifs": int(etab_count),
            "siret_opco_count": int(opco_count),
            "db_pool_size": int(pool_size),
            "redis_connected": redis_ok,
            "uptime_seconds": uptime,
        },
    }
