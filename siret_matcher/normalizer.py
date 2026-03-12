"""Nettoyage et normalisation des noms d'entreprise et adresses."""
import re
import unicodedata

FRANCHISES = [
    "EUROTYRE", "SPEEDY", "NORAUTO", "MIDAS", "POINT S", "EUROMASTER",
    "FEU VERT", "SILIGOM", "EUROREPAR", "AUTOSUR", "AUTOVISION", "DEKRA",
    "SECURITEST", "MOTRIO", "AD EXPERT", "AD ", "ROADY", "FIRST STOP",
    "VULCO", "CARGLASS", "CARTER CASH", "OSCARO",
]

MARQUES_AUTO = [
    "RENAULT", "PEUGEOT", "CITROEN", "CITROËN", "DACIA", "TOYOTA", "FORD",
    "FIAT", "BMW", "MERCEDES", "AUDI", "VOLKSWAGEN", "OPEL", "NISSAN",
    "HYUNDAI", "KIA", "HONDA", "SUZUKI", "VOLVO", "MAZDA", "SEAT", "SKODA",
    "MITSUBISHI", "JEEP", "SUBARU", "ISUZU", "IVECO",
]

FORMES_JURIDIQUES = [
    "SARL", "SAS", "SASU", "SA", "EURL", "SCI", "EI", "SNC", "ETS",
    "ETABLISSEMENTS", "ETABLISSEMENT", "SOCIETE", "SOC", "GIE", "SELARL",
    "SELAS", "SCP", "SCOP", "ASSOCIATION", "ASSOC",
]

ARTICLES = [
    "ET", "FILS", "CIE", "CHEZ", "LE", "LA", "LES", "DU", "DES", "DE",
    "AU", "AUX", "A", "EN", "PAR", "POUR", "L",
]

GENERIQUES = [
    "GARAGE", "AUTO", "AUTOMOBILE", "AUTOMOBILES", "CARROSSERIE", "MOTO",
    "MOTOS", "PNEUS", "PNEU", "SERVICE", "SERVICES", "MECANIQUE",
    "REPARATION", "REPARATIONS", "CONTROLE", "TECHNIQUE", "CENTRE",
    "ATELIER", "DEPANNAGE", "PIECES", "AUTOS", "CAR", "CARS",
]

ALL_BRANDS = set(FRANCHISES + MARQUES_AUTO)


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def normalize_base(s: str) -> str:
    """Normalisation de base : majuscules, accents, caractères spéciaux."""
    s = strip_accents(s.upper())
    # Apostrophes typographiques → espace
    s = re.sub(r"[''`'\u2019\u2018]", " ", s)
    # Tirets entre mots → espace (mais garder les séparateurs " - ")
    s = re.sub(r"(?<=[A-Z])-(?=[A-Z])", " ", s)
    # Slash et tirets longs
    s = s.replace("/", " ").replace("–", "-").replace("—", "-")
    return s


def extract_parentheses(nom: str) -> tuple[str, str]:
    """Extraire le contenu des parenthèses et retourner (nom_sans_paren, contenu_paren)."""
    m = re.search(r"\(([^)]+)\)", nom)
    if not m:
        return nom, ""
    contenu = m.group(1).strip()
    nom_sans = re.sub(r"\([^)]*\)", " ", nom).strip()
    return nom_sans, contenu


def split_franchise(nom: str) -> str:
    """Si 'Franchise - Nom Local', retourner la partie non-franchise."""
    if " - " not in nom:
        return nom
    parts = [p.strip() for p in nom.split(" - ")]
    non_brand = [
        p for p in parts
        if not any(b in p.upper() for b in ALL_BRANDS)
    ]
    return non_brand[0] if non_brand else parts[-1]


def remove_words(s: str, words: list[str]) -> str:
    for w in words:
        s = re.sub(r"\b" + re.escape(w) + r"\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_name(nom: str, ville: str = "") -> str:
    """Nettoyage complet d'un nom d'entreprise pour recherche."""
    n = normalize_base(nom)
    n = split_franchise(n)
    n = re.sub(r"\([^)]*\)", " ", n)  # Retirer parenthèses
    n = remove_words(n, [strip_accents(b.upper()) for b in ALL_BRANDS])
    n = remove_words(n, FORMES_JURIDIQUES)
    n = remove_words(n, ARTICLES)
    # Retirer la ville
    if ville:
        ville_words = strip_accents(ville.upper()).replace("-", " ").split()
        n = remove_words(n, [w for w in ville_words if len(w) >= 3])
    n = re.sub(r"[^A-Z0-9\s]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    if len(n) < 3:
        n = re.sub(r"[^A-Z0-9\s]", " ", normalize_base(nom))
        n = re.sub(r"\s+", " ", n).strip()
    return n


def get_distinctive_words(nom_clean: str) -> list[str]:
    """Extraire les mots distinctifs d'un nom nettoyé."""
    words = [w for w in nom_clean.split() if len(w) >= 2]
    distinctive = [w for w in words if w not in GENERIQUES and w not in ARTICLES and len(w) >= 3]
    if not distinctive:
        distinctive = [w for w in words if w not in ARTICLES]
    return sorted(distinctive, key=len, reverse=True)


def generate_variants(prospect) -> list[str]:
    """Générer toutes les variantes de recherche pour un prospect."""
    variants = []
    nom_base = normalize_base(prospect.nom)

    # Variante 1 : nom nettoyé complet
    clean = clean_name(prospect.nom, prospect.ville)
    if clean and len(clean) >= 3:
        variants.append(clean)

    # Variante 2 : contenu des parenthèses
    _, paren = extract_parentheses(nom_base)
    if paren:
        paren_clean = clean_name(paren, prospect.ville)
        if paren_clean and len(paren_clean) >= 3 and paren_clean not in variants:
            variants.insert(0, paren_clean)  # Priorité

    # Variante 3 : partie non-franchise du tiret
    if " - " in nom_base:
        franchise_clean = clean_name(split_franchise(nom_base), prospect.ville)
        if franchise_clean and franchise_clean not in variants:
            variants.append(franchise_clean)

    # Variante 4 : mots distinctifs seulement
    for v in list(variants):
        words = get_distinctive_words(v)
        if words and " ".join(words) != v:
            joined = " ".join(words[:3])
            if joined not in variants:
                variants.append(joined)

    return variants


def normalize_address(adresse: str) -> tuple[str, str, str]:
    """Extraire numéro, voie, et voie nettoyée d'une adresse.
    
    Returns: (numero, voie_brute, voie_clean)
    """
    addr = strip_accents(adresse.upper()).strip()

    # Extraire le numéro
    m = re.match(r"^(\d+)\s*[,\s]\s*(.*)", addr)
    if not m:
        m = re.match(r"^(\d+\s*(?:BIS|TER)?)\s+(.+)", addr, re.IGNORECASE)
    if not m:
        return "", addr, clean_voie(addr)

    numero = m.group(1).strip()
    voie = m.group(2).strip()
    # Retirer le CP et la ville en fin d'adresse
    voie = re.sub(r",?\s*\d{5}\s+.*$", "", voie).strip()

    return numero, voie, clean_voie(voie)


def clean_voie(voie: str) -> str:
    """Nettoyer le nom de voie pour comparaison."""
    v = strip_accents(voie.upper())
    # Abréviations standard
    v = re.sub(r"\bRUE\b", "R", v)
    v = re.sub(r"\bAVENUE\b", "AV", v)
    v = re.sub(r"\bBOULEVARD\b", "BD", v)
    v = re.sub(r"\bIMPASSE\b", "IMP", v)
    v = re.sub(r"\bCHEMIN\b", "CH", v)
    v = re.sub(r"\bROUTE\b", "RTE", v)
    v = re.sub(r"\bPLACE\b", "PL", v)
    v = re.sub(r"\bALLEE\b", "ALL", v)
    # Retirer articles
    v = re.sub(r"\b(DU|DE LA|DE L|DES|DE|D|LE|LA|LES|L)\b", "", v)
    v = re.sub(r"[^A-Z0-9\s]", " ", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v


def normalize_prospect(prospect) -> None:
    """Enrichir un Prospect avec tous les champs normalisés. Modifie en place."""
    prospect.nom_clean = clean_name(prospect.nom, prospect.ville)
    _, prospect.nom_parentheses = extract_parentheses(normalize_base(prospect.nom))
    if prospect.nom_parentheses:
        prospect.nom_parentheses = clean_name(prospect.nom_parentheses, prospect.ville)
    prospect.nom_variantes = generate_variants(prospect)
    numero, voie, voie_clean = normalize_address(prospect.adresse)
    prospect.adresse_numero = numero
    prospect.adresse_voie = voie
    prospect.adresse_voie_clean = voie_clean
