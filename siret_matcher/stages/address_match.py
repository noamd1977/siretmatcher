"""Étape 3 : Matching par adresse physique (BAN + base Sirene locale).

C'est le game-changer pour les franchises :
"Point S" au "32 Avenue Noël Franchini, 20000 Ajaccio"
→ chercher TOUS les établissements actifs à cette adresse
→ souvent il n'y en a qu'un seul = match direct
"""
import httpx
import logging
import re
from siret_matcher.models import Prospect, SireneResult
from siret_matcher.scoring import score_name, score_address
from siret_matcher.normalizer import strip_accents, clean_voie, normalize_address
from siret_matcher.opco import get_opco, format_effectif
from siret_matcher.db import SireneDB

logger = logging.getLogger(__name__)

BAN_API = "https://api-adresse.data.gouv.fr/search/"


async def geocode_ban(client: httpx.AsyncClient, adresse: str, code_postal: str) -> dict | None:
    """Géocoder une adresse via l'API BAN (Base Adresse Nationale).
    
    Retourne les composants structurés de l'adresse.
    """
    try:
        resp = await client.get(BAN_API, params={
            "q": adresse,
            "postcode": code_postal,
            "limit": "1",
        }, timeout=10)
        if resp.status_code != 200:
            return None
        features = resp.json().get("features", [])
        if not features:
            return None
        props = features[0]["properties"]
        return {
            "housenumber": props.get("housenumber", ""),
            "street": props.get("street", ""),
            "postcode": props.get("postcode", ""),
            "city": props.get("city", ""),
            "score": props.get("score", 0),
        }
    except Exception as e:
        logger.debug(f"BAN geocode error: {e}")
        return None


def _row_to_result(row: dict, score: float, methode: str, prospect: Prospect) -> SireneResult:
    """Convertir une ligne PostgreSQL en SireneResult."""
    naf = row.get("naf", "")
    opco, source_opco = get_opco(naf, prospect.nom)
    return SireneResult(
        siret=row.get("siret", ""),
        siren=row.get("siren", ""),
        denomination=row.get("denomination", ""),
        enseigne=row.get("enseigne", ""),
        naf=naf,
        effectif=format_effectif(row.get("tranche_effectif", "")),
        tranche_effectif_code=row.get("tranche_effectif", ""),
        date_creation=str(row.get("date_creation", "")),
        code_postal=row.get("code_postal", ""),
        commune=row.get("commune", ""),
        numero_voie=str(row.get("numero_voie", "")),
        voie=row.get("voie", ""),
        score=score,
        methode=methode,
        opco=opco,
        source_opco=source_opco,
    )


async def stage_address_match(
    client: httpx.AsyncClient,
    db: SireneDB,
    prospect: Prospect,
    seuil: float = 50,
) -> SireneResult | None:
    """Matching par adresse : chercher les établissements à la même adresse physique.
    
    Pipeline :
    1. Extraire numéro + voie de l'adresse Google Maps
    2. Optionnel : enrichir via API BAN pour normaliser
    3. Chercher dans la base locale par numéro + voie + CP
    4. Si 1 résultat → match haute confiance
    5. Si plusieurs → scorer par similarité de nom
    """
    # ---- Composants d'adresse du prospect ----
    numero = prospect.adresse_numero
    voie_clean = prospect.adresse_voie_clean

    # Si pas de numéro extrait, tenter via BAN
    if not numero and prospect.adresse:
        ban = await geocode_ban(client, prospect.adresse, prospect.code_postal)
        if ban and ban.get("housenumber"):
            numero = ban["housenumber"]
            voie_clean = clean_voie(strip_accents(ban.get("street", "").upper()))

    if not numero or not voie_clean:
        logger.debug(f"  ✗ Address: {prospect.nom} — pas d'adresse exploitable")
        return None

    # ---- Recherche dans la base locale ----
    rows = await db.search_by_address(numero, voie_clean, prospect.code_postal)

    # Si rien avec le CP exact, tenter les CP proches (cas Corse 20000/20090)
    if not rows and prospect.departement in ("2A", "2B", "97"):
        # Tenter sans filtrer par CP, juste département implicite via les CP voisins
        alt_cps = _nearby_postcodes(prospect.code_postal)
        for alt_cp in alt_cps:
            rows = await db.search_by_address(numero, voie_clean, alt_cp)
            if rows:
                break

    if not rows:
        logger.debug(f"  ✗ Address: {prospect.nom} — 0 résultat pour {numero} {voie_clean} {prospect.code_postal}")
        return None

    # ---- Scoring ----
    if len(rows) == 1:
        # Un seul établissement à cette adresse = haute confiance
        row = rows[0]
        # Quand même vérifier un minimum de cohérence
        s_addr = score_address(numero, voie_clean, str(row.get("numero_voie", "")),
                               clean_voie(row.get("voie", "")))
        score = 30 + s_addr + 20 + 5  # geo(30) + addr + unicité(5) + base_address(20)
        score = min(score, 95)
        logger.info(f"  ✓ Address UNIQUE: {prospect.nom} → {row['denomination']} SIRET={row['siret']} (score={score})")
        return _row_to_result(row, score, "ADDRESS_UNIQUE", prospect)

    # Plusieurs résultats : scorer par nom
    best_row = None
    best_score = 0
    for row in rows:
        s_name = score_name(
            prospect.nom_clean,
            strip_accents((row.get("denomination") or "").upper()),
            strip_accents((row.get("enseigne") or "").upper()),
        )
        s_total = s_name + 30 + 15  # geo(30) + addr_base(15)
        if s_total > best_score:
            best_score = s_total
            best_row = row

    if best_row and best_score >= seuil:
        logger.info(f"  ✓ Address MULTI: {prospect.nom} → {best_row['denomination']} (score={best_score}, {len(rows)} candidats)")
        return _row_to_result(best_row, best_score, "ADDRESS_MULTI", prospect)

    logger.debug(f"  ✗ Address: {prospect.nom} — {len(rows)} résultats mais score insuffisant ({best_score})")
    return None


def _nearby_postcodes(cp: str) -> list[str]:
    """Retourner les CP voisins pour la Corse et DOM-TOM."""
    prefix = cp[:3]
    corse_cps = {
        "200": ["20000", "20090", "20100", "20167", "20166", "20190"],
        "201": ["20100", "20200", "20137", "20169"],
    }
    if prefix in corse_cps:
        return [c for c in corse_cps[prefix] if c != cp]
    return []
