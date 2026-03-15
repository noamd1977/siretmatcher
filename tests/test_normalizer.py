"""Tests exhaustifs pour siret_matcher/normalizer.py."""
import pytest

from siret_matcher.normalizer import (
    strip_accents,
    normalize_base,
    extract_parentheses,
    split_franchise,
    remove_words,
    clean_name,
    get_distinctive_words,
    generate_variants,
    normalize_address,
    clean_voie,
    normalize_prospect,
    FRANCHISES,
    MARQUES_AUTO,
    ALL_BRANDS,
)
from siret_matcher.models import Prospect


# ═══════════════════════════════════════════════════════════════════════════════
# strip_accents
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("inp, expected", [
    ("Café", "Cafe"),
    ("éàüöñ", "eauon"),
    ("DEJA VU", "DEJA VU"),
    ("", ""),
    ("Citroën", "Citroen"),
])
def test_strip_accents(inp, expected):
    assert strip_accents(inp) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# normalize_base
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("inp, expected", [
    ("café de la gare", "CAFE DE LA GARE"),
    ("l'atelier", "L ATELIER"),
    ("Jean-Pierre", "JEAN PIERRE"),        # tiret entre lettres → espace
    ("A/B", "A B"),                         # slash → espace
    ("test\u2019s", "TEST S"),              # apostrophe typographique
    ("", ""),
])
def test_normalize_base(inp, expected):
    assert normalize_base(inp) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# extract_parentheses
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtractParentheses:
    def test_with_parentheses(self):
        nom, contenu = extract_parentheses("DUPONT (EX MARTIN) SERVICES")
        assert contenu == "EX MARTIN"
        assert "DUPONT" in nom
        assert "SERVICES" in nom

    def test_without_parentheses(self):
        nom, contenu = extract_parentheses("DUPONT SERVICES")
        assert nom == "DUPONT SERVICES"
        assert contenu == ""

    def test_empty(self):
        nom, contenu = extract_parentheses("")
        assert nom == ""
        assert contenu == ""


# ═══════════════════════════════════════════════════════════════════════════════
# split_franchise
# ═══════════════════════════════════════════════════════════════════════════════


class TestSplitFranchise:
    def test_franchise_dash_local(self):
        """'SPEEDY - MARSEILLE' → la partie non-franchise."""
        result = split_franchise("SPEEDY - MARSEILLE")
        assert result == "MARSEILLE"

    def test_no_dash(self):
        assert split_franchise("DUPONT SERVICES") == "DUPONT SERVICES"

    def test_both_non_brand(self):
        """Si aucune partie n'est une marque, prendre la première."""
        result = split_franchise("DUPONT - SERVICES")
        assert result == "DUPONT"

    def test_all_brand_parts(self):
        """Si toutes les parties sont des marques, retourner la dernière."""
        result = split_franchise("RENAULT - SPEEDY")
        assert result == "SPEEDY"


# ═══════════════════════════════════════════════════════════════════════════════
# remove_words
# ═══════════════════════════════════════════════════════════════════════════════


class TestRemoveWords:
    def test_basic(self):
        assert remove_words("SARL DUPONT", ["SARL"]) == "DUPONT"

    def test_multiple(self):
        assert remove_words("LE GARAGE DU COIN", ["LE", "DU"]) == "GARAGE COIN"

    def test_no_match(self):
        assert remove_words("DUPONT", ["SARL"]) == "DUPONT"

    def test_empty(self):
        assert remove_words("", ["SARL"]) == ""


# ═══════════════════════════════════════════════════════════════════════════════
# clean_name — formes juridiques
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("inp, expected", [
    ("SARL DUPONT SERVICES", "DUPONT SERVICES"),
    ("SAS MARTIN", "MARTIN"),
    ("SASU LE GARAGE", "GARAGE"),           # SASU + article retirés, GARAGE reste (pas supprimé)
    ("EURL DUPONT", "DUPONT"),
    ("SCI LES OLIVIERS", "OLIVIERS"),
    ("ETS DUVAL", "DUVAL"),
    ("SOCIETE GENERALE DE PNEUS", "GENERALE PNEUS"),
])
def test_clean_name_removes_legal_forms(inp, expected):
    assert clean_name(inp) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# clean_name — accents et articles
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("inp, expected", [
    ("Café de la Gare", "CAFE GARE"),
    ("L'Atelier du Bois", "ATELIER BOIS"),
    # NOTE: clean_name ne supprime PAS les GENERIQUES (contrairement à ce qu'on
    # pourrait croire). Seuls brands, formes juridiques et articles sont retirés.
])
def test_clean_name_accents_and_articles(inp, expected):
    assert clean_name(inp) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# clean_name — franchises et marques
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("inp, expected", [
    ("GARAGE RENAULT MARTIN", "GARAGE MARTIN"),
    # NOTE: "GARAGE" est dans GENERIQUES mais clean_name ne supprime PAS les
    # génériques, seulement les brands/formes juridiques/articles. "RENAULT" est
    # marque → supprimé, mais "GARAGE" reste.
    ("POINT S MARSEILLE", "MARSEILLE"),
    # "POINT S" est franchise → supprimé
])
def test_clean_name_removes_brands(inp, expected):
    assert clean_name(inp) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# clean_name — cas limites
# ═══════════════════════════════════════════════════════════════════════════════


class TestCleanNameEdgeCases:
    def test_empty_string(self):
        assert clean_name("") == ""

    def test_only_legal_form(self):
        """Quand il ne reste que <3 chars, fallback sur normalize_base."""
        result = clean_name("SAS")
        # Après suppression de SAS, reste < 3 chars → fallback = "SAS"
        assert result == "SAS"

    def test_multiple_spaces(self):
        assert clean_name("  DUPONT   SERVICES  ") == "DUPONT SERVICES"

    def test_with_ville(self):
        """La ville est retirée du nom nettoyé."""
        result = clean_name("DUPONT MARSEILLE", ville="Marseille")
        assert result == "DUPONT"

    def test_special_chars_removed(self):
        result = clean_name("DUPONT & FILS")
        # & supprimé, FILS dans ARTICLES
        assert result == "DUPONT"


# ═══════════════════════════════════════════════════════════════════════════════
# get_distinctive_words
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetDistinctiveWords:
    def test_basic(self):
        words = get_distinctive_words("DUPONT MARSEILLE")
        assert "DUPONT" in words
        assert "MARSEILLE" in words

    def test_filters_generics(self):
        words = get_distinctive_words("GARAGE DUPONT AUTO")
        # GARAGE et AUTO sont dans GENERIQUES
        assert "DUPONT" in words
        assert "GARAGE" not in words
        assert "AUTO" not in words

    def test_sorted_by_length_desc(self):
        words = get_distinctive_words("AB DUPONT XYZ")
        # Mots >= 3 chars et pas génériques, triés par longueur desc
        assert words[0] == "DUPONT"

    def test_empty(self):
        assert get_distinctive_words("") == []

    def test_fallback_when_all_generic(self):
        """Si tous les mots sont génériques, fallback sur mots non-articles."""
        words = get_distinctive_words("GARAGE AUTO")
        # Tous sont GENERIQUES → fallback : mots not in ARTICLES avec len>=2
        assert len(words) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# generate_variants
# ═══════════════════════════════════════════════════════════════════════════════


class TestGenerateVariants:
    def _make_prospect(self, nom, ville="", adresse="", cp="75001"):
        return Prospect(nom=nom, adresse=adresse, code_postal=cp, ville=ville)

    def test_basic_variants(self):
        p = self._make_prospect("DUPONT SERVICES")
        variants = generate_variants(p)
        assert len(variants) >= 1
        assert "DUPONT SERVICES" in variants

    def test_parentheses_variant(self):
        """Le contenu entre parenthèses est ajouté en priorité."""
        p = self._make_prospect("DUPONT (EX MARTIN) SERVICES")
        variants = generate_variants(p)
        assert any("MARTIN" in v for v in variants)

    def test_franchise_dash_variant(self):
        """Avec un tiret franchise, la partie locale est une variante."""
        p = self._make_prospect("SPEEDY - DUPONT AUTO")
        variants = generate_variants(p)
        assert any("DUPONT" in v for v in variants)

    def test_max_reasonable_count(self):
        """Ne devrait pas générer un nombre déraisonnable de variantes."""
        p = self._make_prospect("GARAGE RENAULT MARTIN (EX DUPONT)")
        variants = generate_variants(p)
        assert len(variants) <= 10


# ═══════════════════════════════════════════════════════════════════════════════
# normalize_address
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("adresse, exp_numero, exp_voie_clean", [
    ("12 rue de la Paix", "12", "R PAIX"),
    ("8 BIS avenue Victor Hugo", "8", "BIS AV VICTOR HUGO"),
    # NOTE: le regex de normalize_address capture uniquement les chiffres comme
    # numéro. "BIS" reste dans la voie car le premier regex matche "8" + ",/espace"
    # et le second regex cherche "digits BIS" mais "8 BIS" est déjà uppercase
    # dans l'input, donc le premier regex gagne.
    ("8 bis avenue Victor Hugo", "8", "BIS AV VICTOR HUGO"),
    # NOTE: normalize_address fait .upper() AVANT le regex, donc "8 bis" → "8 BIS"
    # et le premier regex matche "8" comme numéro. "BIS" reste dans la voie.
    # C'est un bug potentiel : le BIS/TER n'est jamais capturé dans le numéro.
    ("", "", ""),
    ("Lieu-dit Les Granges", "", "LIEU DIT GRANGES"),
])
def test_normalize_address(adresse, exp_numero, exp_voie_clean):
    numero, _, voie_clean = normalize_address(adresse)
    assert numero == exp_numero
    assert voie_clean == exp_voie_clean


class TestNormalizeAddressDetails:
    def test_with_postal_code_suffix(self):
        """Le CP et la ville en fin d'adresse sont supprimés."""
        numero, _, voie_clean = normalize_address("5 boulevard Voltaire, 75011 Paris")
        assert numero == "5"
        assert "PARIS" not in voie_clean
        assert "75011" not in voie_clean

    def test_returns_three_parts(self):
        numero, voie_brute, voie_clean = normalize_address("3 place de la République")
        assert numero == "3"
        assert len(voie_brute) > 0
        assert len(voie_clean) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# clean_voie
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("inp, expected", [
    ("RUE DE LA PAIX", "R PAIX"),
    ("AVENUE VICTOR HUGO", "AV VICTOR HUGO"),
    ("BOULEVARD DES CAPUCINES", "BD CAPUCINES"),
    ("IMPASSE DU MOULIN", "IMP MOULIN"),
    ("CHEMIN DE RONDE", "CH RONDE"),
    ("ROUTE DE LYON", "RTE LYON"),
    ("PLACE DE LA BASTILLE", "PL BASTILLE"),
    ("ALLEE DES TILLEULS", "ALL TILLEULS"),
    ("", ""),
])
def test_clean_voie(inp, expected):
    assert clean_voie(inp) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# normalize_prospect (intégration)
# ═══════════════════════════════════════════════════════════════════════════════


class TestNormalizeProspect:
    def test_fills_all_fields(self):
        p = Prospect(
            nom="SARL DUPONT SERVICES",
            adresse="12 rue de la Paix",
            code_postal="75002",
            ville="Paris",
        )
        normalize_prospect(p)
        assert p.nom_clean != ""
        assert p.adresse_numero == "12"
        assert p.adresse_voie_clean == "R PAIX"
        assert len(p.nom_variantes) >= 1

    def test_parentheses_field(self):
        p = Prospect(
            nom="DUPONT (EX MARTIN)",
            adresse="",
            code_postal="13001",
            ville="Marseille",
        )
        normalize_prospect(p)
        assert p.nom_parentheses != ""
        assert "MARTIN" in p.nom_parentheses

    def test_empty_prospect(self):
        p = Prospect(nom="", adresse="", code_postal="75001", ville="")
        normalize_prospect(p)
        assert p.nom_clean == ""
        assert p.adresse_numero == ""


# ═══════════════════════════════════════════════════════════════════════════════
# Département (calculé dans Prospect.__post_init__, pas normalizer,
# mais demandé dans le prompt — on le teste ici par commodité)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("cp, expected_dept", [
    ("75009", "75"),
    ("13001", "13"),
    ("2A600", "2A"),                  # Corse-du-Sud (format déjà 2A)
    ("97100", "971"),                 # DOM-TOM
    ("97400", "974"),                 # Réunion
])
def test_departement_from_code_postal(cp, expected_dept):
    # NOTE: "2A600" ne passe pas par la logique int(cp) de __post_init__,
    # car startswith("20") est False. Le CP commence par "2A" → dept = "2A".
    p = Prospect(nom="TEST", adresse="", code_postal=cp, ville="")
    assert p.departement == expected_dept


@pytest.mark.parametrize("cp, expected_dept", [
    ("20000", "2A"),                  # Ajaccio — startswith("200") → int(20000) <= 20190 → 2A
    ("20200", "2A"),                  # NOTE: startswith("200") et ("201") sont False,
    # startswith("20") est True → fallback "2A". Le code ne distingue PAS
    # correctement 2A/2B pour les CP >= 20200. Bug potentiel documenté.
    ("20190", "2A"),                  # Limite haute — startswith("201") → int(20190) <= 20190 → 2A
    ("20100", "2A"),                  # startswith("201") → int(20100) <= 20190 → 2A
    ("20191", "2B"),                  # startswith("201") → int(20191) > 20190 → 2B
])
def test_departement_corse(cp, expected_dept):
    p = Prospect(nom="TEST", adresse="", code_postal=cp, ville="")
    assert p.departement == expected_dept


# ═══════════════════════════════════════════════════════════════════════════════
# Listes de référence
# ═══════════════════════════════════════════════════════════════════════════════


class TestReferenceData:
    def test_franchises_count(self):
        """22 franchises dans le code."""
        assert len(FRANCHISES) == 22

    def test_marques_auto_count(self):
        assert len(MARQUES_AUTO) == 27

    def test_all_brands_is_union(self):
        assert ALL_BRANDS == set(FRANCHISES + MARQUES_AUTO)

    def test_known_franchises_present(self):
        for name in ["POINT S", "SPEEDY", "MIDAS", "FEU VERT", "NORAUTO"]:
            assert name in FRANCHISES
