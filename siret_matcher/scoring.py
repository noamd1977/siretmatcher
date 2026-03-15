"""Algorithmes de scoring pour le matching SIRET."""
from rapidfuzz import distance, fuzz

from siret_matcher.normalizer import get_distinctive_words, strip_accents


def norm(s: str) -> str:
    return strip_accents((s or "").upper()).strip()


def levenshtein_similarity(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    d = distance.Levenshtein.distance(a, b)
    return 1 - d / max(len(a), len(b))


def jaro_winkler(a: str, b: str) -> float:
    return distance.JaroWinkler.similarity(norm(a), norm(b))


def token_sort_ratio(a: str, b: str) -> float:
    """Similarité indépendante de l'ordre des mots."""
    return fuzz.token_sort_ratio(norm(a), norm(b)) / 100


def token_set_ratio(a: str, b: str) -> float:
    """Similarité basée sur les mots en commun (ignore les mots supplémentaires)."""
    return fuzz.token_set_ratio(norm(a), norm(b)) / 100


def partial_ratio(a: str, b: str) -> float:
    """Meilleur substring match."""
    return fuzz.partial_ratio(norm(a), norm(b)) / 100


def common_words_score(nom1: str, nom2: str) -> float:
    """Score basé sur les mots distinctifs en commun."""
    w1 = set(get_distinctive_words(norm(nom1)))
    w2 = set(get_distinctive_words(norm(nom2)))
    if not w1 or not w2:
        return 0.0
    common = w1 & w2
    # Aussi compter les inclusions partielles (un mot contient l'autre)
    partial = 0
    for a in w1 - common:
        for b in w2 - common:
            if len(a) >= 4 and len(b) >= 4:
                if a in b or b in a:
                    partial += 0.5
                    break
    total = len(common) + partial
    return min(total / max(len(w1), 1), 1.0)


def score_name(nom_prospect: str, nom_sirene: str, enseigne_sirene: str = "") -> float:
    """Score composite du nom (0-50 points).
    
    Compare le nom prospect avec la dénomination ET l'enseigne Sirene,
    prend le meilleur score.
    """
    best = 0.0
    for nom_ref in [nom_sirene, enseigne_sirene]:
        if not nom_ref:
            continue
        # Plusieurs métriques, pondérées
        lev = levenshtein_similarity(nom_prospect, nom_ref)
        jw = jaro_winkler(nom_prospect, nom_ref)
        tsr = token_sort_ratio(nom_prospect, nom_ref)
        tset = token_set_ratio(nom_prospect, nom_ref)
        pr = partial_ratio(nom_prospect, nom_ref)
        cw = common_words_score(nom_prospect, nom_ref)

        # Score combiné
        sim = max(
            lev * 0.3 + jw * 0.2 + tsr * 0.25 + tset * 0.15 + cw * 0.1,
            tset * 0.5 + pr * 0.3 + cw * 0.2,  # Biais token_set pour franchises
            pr * 0.4 + tsr * 0.3 + cw * 0.3,    # Biais partial pour noms courts
        )
        best = max(best, sim)

    # Convertir en points (max 50)
    if best >= 0.85:
        return 50
    elif best >= 0.70:
        return 40
    elif best >= 0.55:
        return 30
    elif best >= 0.40:
        return 20
    elif best >= 0.25:
        return 10
    return 0


def score_geo(cp_prospect: str, cp_sirene: str, dept_prospect: str = "", dept_sirene: str = "") -> float:
    """Score géographique (0-30 points)."""
    if cp_prospect and cp_sirene and cp_prospect == cp_sirene:
        return 30
    if dept_prospect and dept_sirene and dept_prospect == dept_sirene:
        return 15
    if cp_prospect and cp_sirene and cp_prospect[:2] == cp_sirene[:2]:
        return 15
    return 0


def score_address(
    numero_prospect: str, voie_prospect: str,
    numero_sirene: str, voie_sirene: str
) -> float:
    """Score d'adresse fine (0-20 points)."""
    pts = 0
    # Numéro exact
    if numero_prospect and numero_sirene:
        if numero_prospect == numero_sirene:
            pts += 12
    # Similarité voie
    if voie_prospect and voie_sirene:
        sim = token_sort_ratio(voie_prospect, voie_sirene)
        if sim >= 0.7:
            pts += 8
        elif sim >= 0.5:
            pts += 4
    return pts


def score_total(
    nom_prospect: str,
    nom_sirene: str,
    enseigne_sirene: str,
    cp_prospect: str,
    cp_sirene: str,
    dept_prospect: str,
    dept_sirene: str,
    numero_prospect: str = "",
    voie_prospect: str = "",
    numero_sirene_addr: str = "",
    voie_sirene_addr: str = "",
    unique_result: bool = False,
) -> float:
    """Score total composite (0-100)."""
    s = 0.0
    s += score_name(nom_prospect, nom_sirene, enseigne_sirene)
    s += score_geo(cp_prospect, cp_sirene, dept_prospect, dept_sirene)
    s += score_address(numero_prospect, voie_prospect, numero_sirene_addr, voie_sirene_addr)
    if unique_result and s >= 20:
        s += 5
    return min(s, 100)
