"""Étape 4 : Fuzzy matching par trigrams PostgreSQL (base Sirene locale).

pg_trgm permet de matcher :
  "CORS AUTO" → "CORSE AUTOMOBILE" (similarity ~0.45)
  "EQUIP AUTO" → "EQUIP AUTO CORSE" (similarity ~0.65)
  "PACHA" → "LE PACHA" (similarity ~0.55)
"""
import logging
from siret_matcher.models import Prospect, SireneResult
from siret_matcher.scoring import score_name, score_geo, score_address
from siret_matcher.normalizer import strip_accents, clean_voie
from siret_matcher.opco import get_opco, format_effectif
from siret_matcher.db import SireneDB

logger = logging.getLogger(__name__)


def _row_to_result(row: dict, score: float, methode: str, prospect: Prospect) -> SireneResult:
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


async def stage_trigram_match(
    db: SireneDB,
    prospect: Prospect,
    seuil: float = 45,
) -> SireneResult | None:
    """Recherche fuzzy par trigrams dans la base Sirene locale.
    
    Teste toutes les variantes de nom du prospect contre les colonnes
    denomination_clean et enseigne_clean avec l'opérateur % de pg_trgm.
    """
    # Chercher avec toutes les variantes
    rows = await db.search_trigram_all_variants(
        prospect.nom_variantes,
        prospect.code_postal,
        prospect.departement,
    )

    if not rows:
        logger.debug(f"  ✗ Trigram: {prospect.nom} — 0 résultat")
        return None

    # Scorer chaque résultat avec notre scoring Python (plus fin que pg_trgm seul)
    best_row = None
    best_score = 0

    for row in rows:
        denom = strip_accents((row.get("denomination") or "").upper())
        enseigne = strip_accents((row.get("enseigne") or "").upper())

        # Score nom : tester toutes les variantes du prospect
        s_name = 0
        for variant in prospect.nom_variantes[:4]:
            s = score_name(variant, denom, enseigne)
            s_name = max(s_name, s)

        # Score géo
        row_cp = str(row.get("code_postal", ""))
        s_geo = score_geo(prospect.code_postal, row_cp, prospect.departement)

        # Score adresse
        s_addr = 0
        if prospect.adresse_numero:
            row_num = str(row.get("numero_voie", ""))
            row_voie = clean_voie(row.get("voie", ""))
            s_addr = score_address(
                prospect.adresse_numero, prospect.adresse_voie_clean,
                row_num, row_voie
            )

        # Bonus unicité
        s_unique = 5 if len(rows) == 1 else 0

        total = s_name + s_geo + s_addr + s_unique

        if total > best_score:
            best_score = total
            best_row = row

    if best_row and best_score >= seuil:
        logger.info(
            f"  ✓ Trigram: {prospect.nom} → {best_row['denomination']}"
            f" (enseigne={best_row.get('enseigne','')}) score={best_score}"
        )
        return _row_to_result(best_row, best_score, "TRIGRAM_FUZZY", prospect)

    logger.debug(f"  ✗ Trigram: {prospect.nom} — best_score={best_score} < seuil={seuil}")
    return None
