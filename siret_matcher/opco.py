# DEPRECATED: les dictionnaires sont dans lookups.py — ce module ne garde que les fonctions.
"""Fonctions OPCO et effectif. Les dicts de référence sont dans lookups.py."""

from siret_matcher.lookups import ENSEIGNE_TO_OPCO, NAF_TO_OPCO, TRANCHE_MAP


def get_opco(naf: str = "", nom: str = "") -> tuple[str, str]:
    """Retourne (opco, source). Source = 'NAF', 'ENSEIGNE', ou ''."""
    naf_prefix = (naf or "").replace(".", "")[:2]
    if naf_prefix in NAF_TO_OPCO:
        return NAF_TO_OPCO[naf_prefix], "NAF"
    nom_lower = (nom or "").lower()
    for enseigne, opco in ENSEIGNE_TO_OPCO.items():
        if enseigne in nom_lower:
            return opco, "ENSEIGNE"
    return "À déterminer", ""


def format_effectif(tranche_code: str) -> str:
    return TRANCHE_MAP.get(tranche_code or "", tranche_code or "")
