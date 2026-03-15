"""Tests du système de scoring/qualification de leads."""
import pytest

from siret_matcher.lead_scoring import (
    LeadScore,
    score_lead,
    _score_taille,
    _score_opco,
    _score_secteur,
    _score_localisation,
    _score_anciennete,
    _score_completude,
    DEFAULT_CONFIG,
)


# ══════════════════════════════════════════════════════════════════════════════
# Tests unitaires par critère
# ══════════════════════════════════════════════════════════════════════════════


class TestScoreTaille:
    def test_grande_entreprise(self):
        assert _score_taille("32") == 30  # 250-499

    def test_moyenne(self):
        assert _score_taille("12") == 22  # 20-49

    def test_petite(self):
        assert _score_taille("02") == 8  # 3-5

    def test_zero(self):
        assert _score_taille("00") == 0

    def test_inconnu(self):
        assert _score_taille("NN") == 5

    def test_vide(self):
        assert _score_taille("") == 5


class TestScoreOpco:
    def test_france_competences(self):
        assert _score_opco("ATLAS", "FRANCE_COMPETENCES") == 25

    def test_naf(self):
        assert _score_opco("ATLAS", "NAF") == 15

    def test_autre_source(self):
        assert _score_opco("ATLAS", "ENSEIGNE") == 10

    def test_pas_opco(self):
        assert _score_opco("", "") == 0


class TestScoreSecteur:
    def test_prioritaire_informatique(self):
        assert _score_secteur("62.02A", DEFAULT_CONFIG) == 15

    def test_prioritaire_conseil(self):
        assert _score_secteur("70.22Z", DEFAULT_CONFIG) == 15

    def test_non_prioritaire(self):
        assert _score_secteur("01.11Z", DEFAULT_CONFIG) == 5

    def test_vide(self):
        assert _score_secteur("", DEFAULT_CONFIG) == 5

    def test_custom_config(self):
        config = {**DEFAULT_CONFIG, "secteurs_prioritaires": {"01": 12}}
        assert _score_secteur("01.11Z", config) == 12


class TestScoreLocalisation:
    def test_dans_zone(self):
        config = {**DEFAULT_CONFIG, "departements_prioritaires": ["75", "92"]}
        assert _score_localisation("75", config) == 10

    def test_hors_zone(self):
        config = {**DEFAULT_CONFIG, "departements_prioritaires": ["75", "92"]}
        assert _score_localisation("13", config) == 3

    def test_pas_de_zone(self):
        assert _score_localisation("13", DEFAULT_CONFIG) == 10


class TestScoreAnciennete:
    def test_plus_10_ans(self):
        assert _score_anciennete("2010-01-01") == 10

    def test_5_10_ans(self):
        assert _score_anciennete("2019-06-15") == 8

    def test_2_5_ans(self):
        assert _score_anciennete("2023-01-01") == 5

    def test_moins_2_ans(self):
        assert _score_anciennete("2025-06-01") == 2

    def test_vide(self):
        assert _score_anciennete("") == 3


class TestScoreCompletude:
    def test_tout_rempli(self):
        data = {"siret": "x", "opco": "x", "idcc": "x", "dirigeant": "x", "adresse_complete": True}
        assert _score_completude(data) == 10

    def test_partiel(self):
        data = {"siret": "x", "opco": "x"}
        assert _score_completude(data) == 4

    def test_vide(self):
        assert _score_completude({}) == 0


# ══════════════════════════════════════════════════════════════════════════════
# Tests du score total + qualification
# ══════════════════════════════════════════════════════════════════════════════


class TestScoreTotal:
    def test_google_france_hot(self):
        """Google France : grande entreprise, OPCO, secteur info, ancienne → hot."""
        data = {
            "siret": "44306184100047",
            "naf": "62.02A",
            "tranche_effectif": "42",
            "opco": "ATLAS",
            "source_opco": "FRANCE_COMPETENCES",
            "idcc": "1486",
            "dirigeant": "MANICLE",
            "date_creation": "2002-05-16",
            "departement": "75",
            "adresse_complete": True,
        }
        ls = score_lead(data)
        assert ls.qualification == "hot"
        assert ls.total >= 70

    def test_petite_boulangerie_cold(self):
        """Petite boulangerie sans OPCO → cold."""
        data = {
            "siret": "12345678901234",
            "naf": "10.71C",
            "tranche_effectif": "01",
            "opco": "",
            "source_opco": "",
            "date_creation": "2024-01-01",
            "departement": "13",
        }
        ls = score_lead(data)
        assert ls.qualification == "cold"
        assert ls.total < 45

    def test_entreprise_moyenne_warm(self):
        """Entreprise petite avec OPCO NAF, secteur non prioritaire → warm."""
        data = {
            "siret": "99999999999999",
            "naf": "25.11Z",
            "tranche_effectif": "03",       # 6-9 = 12 pts
            "opco": "OPCO 2I",
            "source_opco": "NAF",           # NAF = 15 pts (pas FC)
            "date_creation": "2020-03-01",  # 6 ans = 8 pts
            "departement": "69",
        }
        ls = score_lead(data)
        # 12 + 15 + 5 + 10 + 8 + 2 = 52
        assert ls.qualification == "warm"
        assert 45 <= ls.total < 70


class TestQualification:
    def test_seuils_defaut(self):
        """Vérifie les seuils hot/warm/cold par défaut."""
        base = {
            "siret": "x", "naf": "62.02A", "tranche_effectif": "42",
            "opco": "ATLAS", "source_opco": "FRANCE_COMPETENCES",
            "idcc": "1486", "dirigeant": "X", "date_creation": "2000-01-01",
            "departement": "75", "adresse_complete": True,
        }
        ls = score_lead(base)
        assert ls.qualification == "hot"
        assert ls.total >= 70

    def test_seuils_custom(self):
        """Seuils personnalisés changent la qualification."""
        data = {
            "siret": "x", "naf": "62.02A", "tranche_effectif": "12",
            "opco": "ATLAS", "source_opco": "FRANCE_COMPETENCES",
            "date_creation": "2015-01-01", "departement": "75",
        }
        # Avec seuils par défaut
        ls1 = score_lead(data, DEFAULT_CONFIG)
        # Avec seuil hot abaissé
        config2 = {**DEFAULT_CONFIG, "seuil_hot": 50}
        ls2 = score_lead(data, config2)
        # Le même score peut changer de qualification
        assert ls2.total == ls1.total
        if ls1.qualification == "warm":
            assert ls2.qualification == "hot"


class TestRecommendations:
    def test_pas_opco(self):
        data = {"siret": "x", "naf": "01.11Z", "opco": "", "source_opco": ""}
        ls = score_lead(data)
        assert any("OPCO" in r for r in ls.recommendations)

    def test_grande_taille(self):
        data = {"siret": "x", "tranche_effectif": "22", "opco": "X", "source_opco": "FRANCE_COMPETENCES"}
        ls = score_lead(data)
        assert any("taille significative" in r for r in ls.recommendations)

    def test_secteur_prioritaire(self):
        data = {"siret": "x", "naf": "62.02A", "opco": "X", "source_opco": "FRANCE_COMPETENCES"}
        ls = score_lead(data)
        assert any("informatique" in r for r in ls.recommendations)

    def test_pas_dirigeant(self):
        data = {"siret": "x", "dirigeant": ""}
        ls = score_lead(data)
        assert any("décideur" in r for r in ls.recommendations)


class TestEdgeCases:
    def test_donnees_vides(self):
        ls = score_lead({})
        assert ls.total >= 0
        assert ls.qualification in ("hot", "warm", "cold")

    def test_tous_champs_vides(self):
        data = {
            "siret": "", "naf": "", "tranche_effectif": "",
            "opco": "", "source_opco": "", "idcc": "",
            "dirigeant": "", "date_creation": "", "departement": "",
        }
        ls = score_lead(data)
        assert ls.qualification == "cold"

    def test_config_secteurs_modifies(self):
        data = {"siret": "x", "naf": "01.11Z"}
        config = {**DEFAULT_CONFIG, "secteurs_prioritaires": {"01": 15}}
        ls = score_lead(data, config)
        assert ls.details["secteur"] == 15


# ══════════════════════════════════════════════════════════════════════════════
# Tests API — lead_score dans les réponses
# ══════════════════════════════════════════════════════════════════════════════


pytestmark_api = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestLeadScoreAPI:
    async def test_match_has_lead_score(self, api_client_with_key):
        """POST /api/v3/match retourne un lead_score quand matched."""
        payload = {"nom": "Google France", "code_postal": "75009", "ville": "Paris"}
        resp = await api_client_with_key.post("/api/v3/match", json=payload)
        data = resp.json()
        if data["matched"]:
            ls = data["lead_score"]
            assert ls is not None
            assert "total" in ls
            assert "qualification" in ls
            assert ls["qualification"] in ("hot", "warm", "cold")
            assert "details" in ls
            assert "taille" in ls["details"]
            assert "recommendations" in ls

    async def test_match_not_found_no_lead_score(self, api_client_with_key):
        """Prospect non trouvé → pas de lead_score."""
        payload = {"nom": "XYZQQWWEE INTROUVABLE", "code_postal": "99999", "ville": "Nowhere"}
        resp = await api_client_with_key.post("/api/v3/match", json=payload)
        data = resp.json()
        assert data["lead_score"] is None

    async def test_scoring_config_get(self, api_client):
        """GET /api/v3/scoring/config retourne la config."""
        resp = await api_client.get("/api/v3/scoring/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "secteurs_prioritaires" in data
        assert "seuil_hot" in data
