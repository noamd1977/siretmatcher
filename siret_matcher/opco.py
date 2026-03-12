"""Mapping NAF → OPCO et détection enseignes."""

NAF_TO_OPCO = {
    "01": "OCAPIAT", "02": "OCAPIAT", "03": "OCAPIAT", "10": "OCAPIAT", "11": "OCAPIAT", "75": "OCAPIAT",
    "24": "OPCO 2i", "25": "OPCO 2i", "26": "OPCO 2i", "27": "OPCO 2i", "28": "OPCO 2i",
    "29": "OPCO 2i", "30": "OPCO 2i", "33": "OPCO 2i",
    "41": "CONSTRUCTYS", "42": "CONSTRUCTYS", "43": "CONSTRUCTYS",
    "45": "OPCO Mobilités",
    "46": "OPCOMMERCE", "47": "OPCOMMERCE",
    "49": "OPCO Mobilités", "50": "OPCO Mobilités", "51": "OPCO Mobilités",
    "52": "OPCO Mobilités", "53": "OPCO Mobilités",
    "55": "AKTO", "56": "AKTO",
    "58": "AFDAS", "59": "AFDAS", "60": "AFDAS", "61": "AFDAS",
    "62": "ATLAS", "63": "ATLAS",
    "64": "ATLAS", "65": "ATLAS", "66": "ATLAS",
    "68": "OPCO EP",
    "69": "ATLAS", "70": "ATLAS", "71": "ATLAS", "73": "ATLAS", "74": "ATLAS", "78": "ATLAS",
    "81": "AKTO", "85": "AKTO",
    "86": "OPCO Santé", "87": "OPCO Santé", "88": "OPCO Santé",
    "90": "AFDAS", "91": "AFDAS", "92": "AFDAS", "93": "AFDAS",
    "96": "OPCO EP",
}

ENSEIGNE_TO_OPCO = {
    "speedy": "OPCO Mobilités", "norauto": "OPCO Mobilités", "midas": "OPCO Mobilités",
    "feu vert": "OPCO Mobilités", "euromaster": "OPCO Mobilités", "point s": "OPCO Mobilités",
    "eurotyre": "OPCO Mobilités", "siligom": "OPCO Mobilités", "eurorepar": "OPCO Mobilités",
    "autosur": "OPCO Mobilités", "autovision": "OPCO Mobilités", "dekra": "OPCO Mobilités",
    "securitest": "OPCO Mobilités", "motrio": "OPCO Mobilités", "roady": "OPCO Mobilités",
    "vulco": "OPCO Mobilités", "first stop": "OPCO Mobilités", "carglass": "OPCO Mobilités",
    "renault": "OPCO Mobilités", "peugeot": "OPCO Mobilités", "citroen": "OPCO Mobilités",
    "toyota": "OPCO Mobilités", "ford": "OPCO Mobilités", "bmw": "OPCO Mobilités",
    "mercedes": "OPCO Mobilités", "audi": "OPCO Mobilités", "volkswagen": "OPCO Mobilités",
}


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


TRANCHE_MAP = {
    "NN": "0", "00": "0", "01": "1-2", "02": "3-5", "03": "6-9",
    "11": "10-19", "12": "20-49", "21": "50-99", "22": "100-199",
    "31": "200-249", "32": "250-499", "41": "500-999", "42": "1000-1999",
    "51": "2000-4999", "52": "5000-9999", "53": "10000+",
}


def format_effectif(tranche_code: str) -> str:
    return TRANCHE_MAP.get(tranche_code or "", tranche_code or "")
