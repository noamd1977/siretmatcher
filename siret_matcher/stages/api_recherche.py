"""Étapes 1-2 : API Recherche d'Entreprises (gouv.fr)."""
import asyncio
import httpx
import logging
from siret_matcher.models import Prospect, SireneResult
from siret_matcher.scoring import score_name, score_geo, score_address
from siret_matcher.normalizer import strip_accents, clean_voie
from siret_matcher.opco import get_opco, format_effectif

logger = logging.getLogger(__name__)

API_BASE = "https://recherche-entreprises.api.gouv.fr/search"
RATE_LIMIT = asyncio.Semaphore(5)  # 5 requêtes concurrentes max


async def _search(client: httpx.AsyncClient, params: dict) -> list[dict]:
    """Appel API avec rate limiting."""
    async with RATE_LIMIT:
        try:
            resp = await client.get(API_BASE, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json().get("results", [])
            logger.warning(f"API Recherche status {resp.status_code}: {params.get('q')}")
        except Exception as e:
            logger.warning(f"API Recherche error: {e}")
        await asyncio.sleep(0.2)
    return []


def _score_result(prospect: Prospect, result: dict, nom_query: str) -> tuple[float, dict]:
    """Scorer un résultat API et retourner (score, best_etab_data)."""
    nom_api = (result.get("nom_complet") or "") + " " + (result.get("sigle") or "")
    siege = result.get("siege", {})
    etabs = result.get("matching_etablissements") or []

    # Rejeter les entreprises fermées/radiées
    etat = result.get("etat_administratif", "A")
    if etat != "A":
        return 0, siege

    # Trouver le meilleur établissement (CP local > siège, actif uniquement)
    best_etab = siege
    cp_match = str(siege.get("code_postal", "")) == prospect.code_postal
    for etab in etabs:
        if str(etab.get("code_postal", "")) == prospect.code_postal:
            # Vérifier que l'établissement est actif
            if etab.get("etat_administratif", "A") != "A":
                continue
            best_etab = etab
            cp_match = True
            break

    # Score nom (sur les variantes du prospect)
    s_nom = score_name(nom_query, nom_api)
    for variant in prospect.nom_variantes[:3]:
        s_variant = score_name(variant, nom_api)
        s_nom = max(s_nom, s_variant)

    # Score géo
    etab_cp = str(best_etab.get("code_postal", ""))
    s_geo = score_geo(prospect.code_postal, etab_cp, prospect.departement)

    # Score adresse
    s_addr = 0
    if prospect.adresse_numero and best_etab.get("adresse"):
        addr_sirene = strip_accents((best_etab.get("adresse") or "").upper())
        import re
        num_sirene = (re.match(r"(\d+)", addr_sirene) or type("", (), {"group": lambda s, x: ""})()).group(1)
        voie_sirene = clean_voie(addr_sirene)
        s_addr = score_address(
            prospect.adresse_numero, prospect.adresse_voie_clean,
            num_sirene, voie_sirene
        )

    total = s_nom + s_geo + s_addr
    return total, best_etab


def _build_result(prospect: Prospect, api_result: dict, best_etab: dict, score: float, methode: str) -> SireneResult:
    """Construire un SireneResult depuis un résultat API."""
    siege = api_result.get("siege", {})
    dirigeants = api_result.get("dirigeants") or []
    dirigeant = ""
    if dirigeants:
        d = dirigeants[0]
        dirigeant = f"{d.get('prenoms', '')} {d.get('nom', '')}".strip()

    siret = best_etab.get("siret") or siege.get("siret", "")
    naf = best_etab.get("activite_principale") or siege.get("activite_principale", "")
    tranche = best_etab.get("tranche_effectif_salarie") or siege.get("tranche_effectif_salarie", "")
    opco, source_opco = get_opco(naf, prospect.nom)

    return SireneResult(
        siret=siret,
        siren=api_result.get("siren", ""),
        denomination=api_result.get("nom_complet", ""),
        enseigne=best_etab.get("nom_enseigne", ""),
        naf=naf,
        effectif=format_effectif(tranche),
        tranche_effectif_code=tranche,
        date_creation=best_etab.get("date_creation") or siege.get("date_creation", ""),
        dirigeant=dirigeant,
        code_postal=str(best_etab.get("code_postal", "")),
        commune=best_etab.get("libelle_commune", ""),
        numero_voie=str(best_etab.get("numero_voie", "")),
        voie=best_etab.get("libelle_voie", ""),
        score=score,
        methode=methode,
        opco=opco,
        source_opco=source_opco,
    )


async def stage_api_recherche(
    client: httpx.AsyncClient,
    prospect: Prospect,
    seuil_exact: float = 65,
    seuil_probable: float = 40,
) -> SireneResult | None:
    """Étapes 1 et 2 : recherche par nom via API gouv.fr.
    
    Étape 1 : nom + code_postal
    Étape 2 : nom + département (si étape 1 échoue)
    Tente toutes les variantes de nom.
    """
    best_score = 0
    best_result = None
    best_etab = None
    best_query = ""

    # ---- Étape 1 : par code postal ----
    for variant in prospect.nom_variantes[:4]:
        params = {
            "q": variant,
            "code_postal": prospect.code_postal,
            "per_page": "5",
            "etat_administratif": "A",
        }
        results = await _search(client, params)
        for r in results:
            score, etab = _score_result(prospect, r, variant)
            if score > best_score:
                best_score = score
                best_result = r
                best_etab = etab
                best_query = variant

        if best_score >= seuil_exact:
            break

    # ---- Étape 2 : par département (si pas de match exact) ----
    if best_score < seuil_exact and prospect.departement:
        for variant in prospect.nom_variantes[:3]:
            params = {
                "q": variant,
                "departement": prospect.departement,
                "per_page": "10",
                "etat_administratif": "A",
            }
            results = await _search(client, params)
            for r in results:
                score, etab = _score_result(prospect, r, variant)
                if score > best_score:
                    best_score = score
                    best_result = r
                    best_etab = etab
                    best_query = variant

            if best_score >= seuil_exact:
                break

    # ---- Évaluer le meilleur résultat ----
    if best_result and best_score >= seuil_probable:
        if best_score >= seuil_exact:
            methode = "API_RECHERCHE_EXACT"
        else:
            methode = "API_RECHERCHE_PROBABLE"
        logger.info(f"  ✓ API Recherche: {prospect.nom} → {best_result.get('nom_complet')} (score={best_score}, q='{best_query}')")
        return _build_result(prospect, best_result, best_etab, best_score, methode)

    logger.debug(f"  ✗ API Recherche: {prospect.nom} (best_score={best_score})")
    return None
