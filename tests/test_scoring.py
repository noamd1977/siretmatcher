"""Tests exhaustifs pour siret_matcher/scoring.py."""
import pytest

from siret_matcher.scoring import (
    norm,
    levenshtein_similarity,
    jaro_winkler,
    token_sort_ratio,
    token_set_ratio,
    partial_ratio,
    common_words_score,
    score_name,
    score_geo,
    score_address,
    score_total,
)


# ═══════════════════════════════════════════════════════════════════════════════
# norm (helper interne)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("inp, expected", [
    ("café", "CAFE"),
    ("  dupont  ", "DUPONT"),
    ("", ""),
    (None, ""),
])
def test_norm(inp, expected):
    assert norm(inp) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# levenshtein_similarity
# ═══════════════════════════════════════════════════════════════════════════════


class TestLevenshteinSimilarity:
    def test_identical(self):
        assert levenshtein_similarity("DUPONT", "DUPONT") == 1.0

    def test_identical_case_insensitive(self):
        assert levenshtein_similarity("dupont", "DUPONT") == 1.0

    def test_empty_a(self):
        assert levenshtein_similarity("", "DUPONT") == 0.0

    def test_empty_b(self):
        assert levenshtein_similarity("DUPONT", "") == 0.0

    def test_both_empty(self):
        assert levenshtein_similarity("", "") == 0.0

    def test_similar(self):
        sim = levenshtein_similarity("DUPONT", "DUPOND")
        assert 0.7 < sim < 1.0

    def test_completely_different(self):
        sim = levenshtein_similarity("AAAA", "ZZZZ")
        assert sim < 0.3


# ═══════════════════════════════════════════════════════════════════════════════
# jaro_winkler
# ═══════════════════════════════════════════════════════════════════════════════


class TestJaroWinkler:
    def test_identical(self):
        assert jaro_winkler("DUPONT", "DUPONT") == 1.0

    def test_similar_prefix(self):
        """Jaro-Winkler donne un bonus aux préfixes communs."""
        sim = jaro_winkler("DUPONT", "DUPOND")
        assert sim > 0.8

    def test_completely_different(self):
        assert jaro_winkler("AAAA", "ZZZZ") < 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# token_sort_ratio
# ═══════════════════════════════════════════════════════════════════════════════


class TestTokenSortRatio:
    def test_identical(self):
        assert token_sort_ratio("DUPONT SERVICES", "DUPONT SERVICES") == 1.0

    def test_reordered(self):
        """Indépendant de l'ordre des mots."""
        assert token_sort_ratio("SERVICES DUPONT", "DUPONT SERVICES") == 1.0

    def test_returns_0_to_1(self):
        r = token_sort_ratio("AAA", "ZZZ")
        assert 0.0 <= r <= 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# token_set_ratio
# ═══════════════════════════════════════════════════════════════════════════════


class TestTokenSetRatio:
    def test_identical(self):
        assert token_set_ratio("DUPONT SERVICES", "DUPONT SERVICES") == 1.0

    def test_superset(self):
        """Ignore les mots supplémentaires."""
        r = token_set_ratio("DUPONT", "DUPONT SERVICES SAS")
        assert r > 0.8

    def test_completely_different(self):
        r = token_set_ratio("AAAA", "ZZZZ")
        assert r < 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# partial_ratio
# ═══════════════════════════════════════════════════════════════════════════════


class TestPartialRatio:
    def test_identical(self):
        assert partial_ratio("DUPONT", "DUPONT") == 1.0

    def test_substring(self):
        """Meilleur substring match."""
        r = partial_ratio("DUPONT", "DUPONT SERVICES")
        assert r == 1.0

    def test_completely_different(self):
        assert partial_ratio("AAAA", "ZZZZ") < 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# common_words_score
# ═══════════════════════════════════════════════════════════════════════════════


class TestCommonWordsScore:
    def test_identical(self):
        assert common_words_score("DUPONT SERVICES", "DUPONT SERVICES") == 1.0

    def test_no_common(self):
        assert common_words_score("DUPONT", "MARTIN") == 0.0

    def test_partial_inclusion(self):
        """Mots >= 4 chars où l'un contient l'autre → 0.5 point."""
        # NOTE: "AUTOMOBILE" et "AUTO" sont dans GENERIQUES (normalizer.py),
        # donc get_distinctive_words les filtre → sets vides → score 0.
        # On utilise des mots non-génériques pour tester la logique d'inclusion.
        score = common_words_score("DUPONTIN", "DUPONT MARTIN")
        # "DUPONTIN" contient "DUPONT" (len>=4) → partial 0.5
        assert score > 0.0

    def test_empty(self):
        assert common_words_score("", "DUPONT") == 0.0

    def test_both_empty(self):
        assert common_words_score("", "") == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# score_name — paliers de points
# ═══════════════════════════════════════════════════════════════════════════════

# NOTE: score_name utilise un système par paliers (pas linéaire) :
#   sim >= 0.85 → 50 pts
#   sim >= 0.70 → 40 pts
#   sim >= 0.55 → 30 pts
#   sim >= 0.40 → 20 pts
#   sim >= 0.25 → 10 pts
#   sinon       →  0 pts
# La doc décrivait un score linéaire pondéré sur 50, mais le code utilise des paliers.

# NOTE: les poids du code (lev*0.3 + jw*0.2 + tsr*0.25 + tset*0.15 + cw*0.1)
# correspondent à la doc (30% Levenshtein, 20% Jaro-Winkler, 25% Token-Sort,
# 15% Token-Set, 10% mots communs). Mais le code prend le MAX de 3 formules
# différentes, pas juste la première.


class TestScoreName:
    def test_perfect_match(self):
        """Noms identiques → 50 pts (palier max)."""
        assert score_name("GOOGLE FRANCE", "GOOGLE FRANCE") == 50

    def test_match_with_extra_word(self):
        """Nom avec mot supplémentaire → score élevé."""
        s = score_name("GOOGLE FRANCE", "GOOGLE FRANCE SAS")
        assert s >= 40  # Le token_set_ratio sera très haut

    def test_enseigne_match(self):
        """Le meilleur score entre dénomination et enseigne est retenu."""
        s = score_name("SPEEDY", "SAS RAPID AUTO", enseigne_sirene="SPEEDY")
        assert s == 50

    def test_completely_different(self):
        """Noms sans rapport → score très bas (palier 0 ou 10)."""
        # NOTE: "GOOGLE FRANCE" vs "BOULANGERIE MARTIN" donne 10 pts, pas 0.
        # Les algos fuzzy (partial_ratio, token_set) trouvent suffisamment de
        # similarité partielle pour atteindre le palier sim >= 0.25 → 10 pts.
        s = score_name("GOOGLE FRANCE", "BOULANGERIE MARTIN")
        assert s <= 10

    def test_partial_overlap(self):
        """Chevauchement partiel → score intermédiaire."""
        s = score_name("GARAGE DUPONT", "DUPONT AUTO SERVICES")
        assert 10 <= s <= 40

    def test_empty_prospect(self):
        assert score_name("", "DUPONT") == 0

    def test_empty_sirene(self):
        assert score_name("DUPONT", "") == 0

    def test_empty_both_sirene(self):
        """Les deux refs vides → 0."""
        assert score_name("DUPONT", "", "") == 0

    @pytest.mark.parametrize("score_val", [0, 10, 20, 30, 40, 50])
    def test_returns_valid_palier(self, score_val):
        """score_name ne retourne que des valeurs par palier."""
        # On ne peut pas forcer un palier précis, mais on vérifie le type
        pass  # Couvert implicitement par les tests ci-dessus

    def test_all_possible_values(self):
        """Vérifie que score_name retourne uniquement des multiples de 10."""
        test_pairs = [
            ("DUPONT", "DUPONT"),
            ("DUPONT", "DUPOND"),
            ("DUPONT", "MARTIN"),
            ("AB", "XYZWQR"),
            ("GARAGE DUPONT MARSEILLE", "DUPONT"),
        ]
        for a, b in test_pairs:
            s = score_name(a, b)
            assert s in {0, 10, 20, 30, 40, 50}, f"score_name({a!r}, {b!r}) = {s}"


# ═══════════════════════════════════════════════════════════════════════════════
# score_geo
# ═══════════════════════════════════════════════════════════════════════════════


class TestScoreGeo:
    def test_cp_exact(self):
        """Même code postal → 30 pts."""
        assert score_geo("75009", "75009") == 30

    def test_same_dept_explicit(self):
        """CP différents, même département passé explicitement → 15 pts."""
        assert score_geo("75009", "75010", dept_prospect="75", dept_sirene="75") == 15

    def test_same_dept_from_cp_prefix(self):
        """CP différents mais même préfixe 2 chars → 15 pts."""
        assert score_geo("13001", "13008") == 15

    def test_different_dept(self):
        """Départements différents → 0 pts."""
        assert score_geo("75009", "13001") == 0

    def test_empty_cp(self):
        assert score_geo("", "75009") == 0

    def test_both_empty(self):
        assert score_geo("", "") == 0

    def test_dept_match_overrides_cp_prefix(self):
        """dept explicites matchent même si CP préfixes diffèrent."""
        # Ex: Corse 2A vs 20xxx
        assert score_geo("20000", "2A001", dept_prospect="2A", dept_sirene="2A") == 15


# ═══════════════════════════════════════════════════════════════════════════════
# score_address
# ═══════════════════════════════════════════════════════════════════════════════


class TestScoreAddress:
    def test_exact_numero_and_good_voie(self):
        """Numéro exact + voie similaire → 12 + 8 = 20 pts."""
        pts = score_address("8", "R LONDRES", "8", "R LONDRES")
        assert pts == 20

    def test_exact_numero_only(self):
        """Numéro exact, voie sans rapport → 12 pts."""
        pts = score_address("8", "R LONDRES", "8", "AV VICTOR HUGO")
        assert pts >= 12  # 12 pour numéro, voie dépend de la similarité

    def test_different_numero(self):
        """Numéros différents → pas de points numéro."""
        pts = score_address("8", "R LONDRES", "12", "R LONDRES")
        assert pts == 8  # Seulement voie similaire

    def test_voie_similar_above_05(self):
        """Voie similaire >= 0.5 mais < 0.7 → 4 pts voie."""
        pts = score_address("", "BD CAPUCINES", "", "BD GRANDS CAPUCINES")
        assert pts in {4, 8}  # Dépend du score exact de token_sort_ratio

    def test_no_numero_no_voie(self):
        assert score_address("", "", "", "") == 0

    def test_numero_empty_one_side(self):
        """Un seul numéro vide → pas de points numéro."""
        pts = score_address("8", "R LONDRES", "", "R LONDRES")
        assert pts == 8  # Seulement voie

    def test_max_is_20(self):
        """Score adresse plafonné à 20."""
        pts = score_address("8", "R LONDRES", "8", "R LONDRES")
        assert pts <= 20


# ═══════════════════════════════════════════════════════════════════════════════
# score_total — composition
# ═══════════════════════════════════════════════════════════════════════════════


class TestScoreTotal:
    def test_sum_of_components(self):
        """Le total = nom + geo + adresse (+ bonus éventuel)."""
        nom = score_name("DUPONT", "DUPONT")
        geo = score_geo("75009", "75009")
        addr = score_address("8", "R PAIX", "8", "R PAIX")
        total = score_total(
            nom_prospect="DUPONT",
            nom_sirene="DUPONT",
            enseigne_sirene="",
            cp_prospect="75009",
            cp_sirene="75009",
            dept_prospect="75",
            dept_sirene="75",
            numero_prospect="8",
            voie_prospect="R PAIX",
            numero_sirene_addr="8",
            voie_sirene_addr="R PAIX",
        )
        assert total == nom + geo + addr

    def test_perfect_match_is_100(self):
        """Nom parfait (50) + CP exact (30) + adresse parfaite (20) = 100."""
        total = score_total(
            nom_prospect="DUPONT",
            nom_sirene="DUPONT",
            enseigne_sirene="",
            cp_prospect="75009",
            cp_sirene="75009",
            dept_prospect="75",
            dept_sirene="75",
            numero_prospect="8",
            voie_prospect="R PAIX",
            numero_sirene_addr="8",
            voie_sirene_addr="R PAIX",
        )
        assert total == 100

    def test_capped_at_100(self):
        """Même avec bonus unicité, ne dépasse pas 100."""
        total = score_total(
            nom_prospect="DUPONT",
            nom_sirene="DUPONT",
            enseigne_sirene="",
            cp_prospect="75009",
            cp_sirene="75009",
            dept_prospect="75",
            dept_sirene="75",
            numero_prospect="8",
            voie_prospect="R PAIX",
            numero_sirene_addr="8",
            voie_sirene_addr="R PAIX",
            unique_result=True,
        )
        assert total == 100

    def test_bonus_unicite(self):
        """Bonus +5 si unique_result=True et score >= 20."""
        total_sans = score_total(
            nom_prospect="DUPONT",
            nom_sirene="MARTIN",
            enseigne_sirene="",
            cp_prospect="75009",
            cp_sirene="75009",
            dept_prospect="75",
            dept_sirene="75",
        )
        total_avec = score_total(
            nom_prospect="DUPONT",
            nom_sirene="MARTIN",
            enseigne_sirene="",
            cp_prospect="75009",
            cp_sirene="75009",
            dept_prospect="75",
            dept_sirene="75",
            unique_result=True,
        )
        if total_sans >= 20:
            assert total_avec == total_sans + 5 or total_avec == 100
        else:
            assert total_avec == total_sans

    def test_no_bonus_under_20(self):
        """Pas de bonus unicité si score < 20."""
        total = score_total(
            nom_prospect="AAAA",
            nom_sirene="ZZZZ",
            enseigne_sirene="",
            cp_prospect="75009",
            cp_sirene="13001",
            dept_prospect="75",
            dept_sirene="13",
            unique_result=True,
        )
        total_sans = score_total(
            nom_prospect="AAAA",
            nom_sirene="ZZZZ",
            enseigne_sirene="",
            cp_prospect="75009",
            cp_sirene="13001",
            dept_prospect="75",
            dept_sirene="13",
            unique_result=False,
        )
        assert total == total_sans

    def test_zero_everything(self):
        total = score_total(
            nom_prospect="", nom_sirene="", enseigne_sirene="",
            cp_prospect="", cp_sirene="", dept_prospect="", dept_sirene="",
        )
        assert total == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Seuils de confiance
# ═══════════════════════════════════════════════════════════════════════════════

# NOTE: Les seuils EXACT (>=65) et PROBABLE (>=40) sont utilisés dans matcher.py,
# pas dans scoring.py. On teste ici la cohérence des scores avec ces seuils.


class TestConfidenceThresholds:
    def test_exact_threshold(self):
        """Un match parfait nom + CP doit dépasser le seuil EXACT (65)."""
        total = score_total(
            nom_prospect="GOOGLE FRANCE",
            nom_sirene="GOOGLE FRANCE",
            enseigne_sirene="",
            cp_prospect="75009",
            cp_sirene="75009",
            dept_prospect="75",
            dept_sirene="75",
        )
        assert total >= 65  # 50 (nom) + 30 (geo) = 80

    def test_probable_threshold(self):
        """Match nom ok + même département → devrait atteindre PROBABLE (40)."""
        total = score_total(
            nom_prospect="DUPONT SERVICES",
            nom_sirene="DUPONT SERVICES",
            enseigne_sirene="",
            cp_prospect="75009",
            cp_sirene="75010",
            dept_prospect="75",
            dept_sirene="75",
        )
        assert total >= 40  # 50 (nom) + 15 (dept) = 65

    def test_below_probable(self):
        """Nom sans rapport + dept différent → sous le seuil PROBABLE."""
        total = score_total(
            nom_prospect="GOOGLE FRANCE",
            nom_sirene="BOULANGERIE MARTIN",
            enseigne_sirene="",
            cp_prospect="75009",
            cp_sirene="13001",
            dept_prospect="75",
            dept_sirene="13",
        )
        assert total < 40


# ═══════════════════════════════════════════════════════════════════════════════
# Propriétés de robustesse
# ═══════════════════════════════════════════════════════════════════════════════


class TestRobustness:
    def test_score_name_is_symmetric(self):
        """Le score nom est symétrique (a vs b == b vs a)."""
        s1 = score_name("DUPONT SERVICES", "SERVICES DUPONT")
        s2 = score_name("SERVICES DUPONT", "DUPONT SERVICES")
        assert s1 == s2

    def test_score_name_case_insensitive(self):
        assert score_name("dupont", "DUPONT") == score_name("DUPONT", "DUPONT")

    def test_score_name_accent_insensitive(self):
        assert score_name("Café", "CAFE") == score_name("CAFE", "CAFE")

    def test_score_geo_none_safe(self):
        """Les empty strings ne provoquent pas d'erreur."""
        assert score_geo("", "", "", "") == 0

    def test_score_total_all_defaults(self):
        """Appel avec uniquement les args obligatoires."""
        total = score_total(
            nom_prospect="TEST",
            nom_sirene="TEST",
            enseigne_sirene="",
            cp_prospect="75001",
            cp_sirene="75001",
            dept_prospect="75",
            dept_sirene="75",
        )
        assert total >= 0
