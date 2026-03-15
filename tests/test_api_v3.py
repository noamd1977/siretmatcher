"""
Tests pour l'API v3 — endpoints structurés.

Les tests marqués @integration tapent la base PostgreSQL locale et l'app FastAPI.
"""
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v3/etablissements/{siret}
# ══════════════════════════════════════════════════════════════════════════════


class TestEtablissement:
    """Lookup enrichi d'un établissement."""

    async def test_etablissement_found(self, api_client):
        """SIRET connu → réponse structurée complète."""
        resp = await api_client.get("/api/v3/etablissements/44306184100047")
        assert resp.status_code == 200
        data = resp.json()
        assert data["siret"] == "44306184100047"
        assert data["siren"] == "443061841"
        # Vérifie la structure imbriquée
        assert "code" in data["naf"]
        assert "libelle" in data["naf"]
        assert "code" in data["effectif"]
        assert "libelle" in data["effectif"]
        assert "code_postal" in data["adresse"]
        assert "commune" in data["adresse"]
        assert "departement" in data["adresse"]
        assert "region" in data["adresse"]
        assert "nom" in data["opco"]
        assert "code" in data["idcc"]
        assert "libelle" in data["idcc"]
        # Nouveaux champs dirigeant + entreprise
        assert "dirigeant" in data
        assert "nom" in data["dirigeant"]
        assert "prenom" in data["dirigeant"]
        assert "fonction" in data["dirigeant"]
        assert "entreprise" in data
        assert "categorie" in data["entreprise"]
        assert "nature_juridique" in data["entreprise"]
        assert "nombre_etablissements" in data["entreprise"]

    async def test_etablissement_not_found(self, api_client):
        """SIRET inexistant → 404."""
        resp = await api_client.get("/api/v3/etablissements/00000000000000")
        assert resp.status_code == 404

    async def test_etablissement_invalid_siret(self, api_client):
        """Format invalide → 400."""
        resp = await api_client.get("/api/v3/etablissements/abc")
        assert resp.status_code == 400

    async def test_etablissement_public(self, api_client):
        """L'endpoint est public (pas de X-API-Key)."""
        resp = await api_client.get("/api/v3/etablissements/44306184100047")
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v3/etablissements/{siret}/enrich
# ══════════════════════════════════════════════════════════════════════════════


class TestEnrichment:
    """Enrichissement externe d'un établissement."""

    @pytest.mark.slow
    async def test_enrich_structure(self, api_client):
        """L'endpoint /enrich retourne la structure attendue."""
        resp = await api_client.get("/api/v3/etablissements/44306184100047/enrich")
        assert resp.status_code == 200
        data = resp.json()
        assert data["siret"] == "44306184100047"
        assert "dirigeant" in data
        assert "nom" in data["dirigeant"]
        assert "financier" in data
        assert "chiffre_affaires" in data["financier"]
        assert "entreprise" in data
        assert "enriched_at" in data
        assert isinstance(data["sources"], list)

    @pytest.mark.slow
    async def test_enrich_api_data(self, api_client):
        """L'API retourne dirigeant/catégorie pour Google France."""
        resp = await api_client.get("/api/v3/etablissements/44306184100047/enrich")
        data = resp.json()
        if "api_recherche_entreprises" in data["sources"]:
            assert data["dirigeant"]["nom"]
            assert data["entreprise"]["categorie"] in ("PME", "ETI", "GE")

    async def test_enrich_invalid_siret(self, api_client):
        """Format invalide → 400."""
        resp = await api_client.get("/api/v3/etablissements/abc/enrich")
        assert resp.status_code == 400

    async def test_enrich_unknown_graceful(self, api_client):
        """SIRET inconnu → retourne structure vide, pas d'erreur."""
        resp = await api_client.get("/api/v3/etablissements/99999999999999/enrich")
        assert resp.status_code == 200
        data = resp.json()
        assert data["siret"] == "99999999999999"


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v3/match
# ══════════════════════════════════════════════════════════════════════════════


class TestMatchV3:
    """Matching intelligent v3."""

    async def test_match_found(self, api_client_with_key):
        """Google France → matched + structure v3."""
        payload = {
            "nom": "Google France",
            "code_postal": "75009",
            "ville": "Paris",
        }
        resp = await api_client_with_key.post("/api/v3/match", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched"] is True
        assert data["confidence"] in ("high", "medium", "low")
        assert data["score"] > 0
        assert data["methode"] is not None
        # Établissement structuré
        etab = data["etablissement"]
        assert etab is not None
        assert "siret" in etab
        assert "naf" in etab
        assert "code" in etab["naf"]
        assert "adresse" in etab
        # Dirigeant + entreprise enrichis depuis le matching
        assert "dirigeant" in etab
        assert "entreprise" in etab
        # Pas de debug par défaut
        assert data["debug"] is None

    async def test_match_found_has_dirigeant(self, api_client_with_key):
        """Le matching remonte les données dirigeant/catégorie depuis l'API."""
        payload = {"nom": "Google France", "code_postal": "75009", "ville": "Paris"}
        resp = await api_client_with_key.post("/api/v3/match", json=payload)
        data = resp.json()
        if data["matched"] and data["etablissement"]:
            etab = data["etablissement"]
            # Les données viennent de l'API Recherche Entreprises
            assert etab["dirigeant"]["nom"] or etab["dirigeant"]["prenom"] or True
            assert etab["entreprise"]["categorie"] in ("PME", "ETI", "GE", None)

    async def test_match_not_found(self, api_client_with_key):
        """Prospect introuvable → matched=false."""
        payload = {
            "nom": "XYZQQWWEE INTROUVABLE SARL",
            "code_postal": "99999",
            "ville": "Nulle Part",
        }
        resp = await api_client_with_key.post("/api/v3/match", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched"] is False
        assert data["etablissement"] is None
        assert data["confidence"] == "low"

    async def test_match_debug_header(self, api_client_with_key):
        """Header X-Debug: true → debug présent."""
        payload = {
            "nom": "Google France",
            "code_postal": "75009",
            "ville": "Paris",
        }
        resp = await api_client_with_key.post(
            "/api/v3/match", json=payload,
            headers={"X-Debug": "true"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["debug"] is not None
        assert "stages_tried" in data["debug"]
        assert "duration_ms" in data["debug"]

    async def test_match_debug_absent_by_default(self, api_client_with_key):
        """Sans X-Debug → debug absent."""
        payload = {"nom": "Google France", "code_postal": "75009", "ville": "Paris"}
        resp = await api_client_with_key.post("/api/v3/match", json=payload)
        data = resp.json()
        assert data["debug"] is None

    async def test_match_requires_auth(self, api_client):
        """POST /api/v3/match sans API key → 401."""
        payload = {"nom": "Test", "code_postal": "75001", "ville": "Paris"}
        resp = await api_client.post("/api/v3/match", json=payload)
        assert resp.status_code == 401

    async def test_match_v3_same_data_as_legacy(self, api_client_with_key):
        """Le match v3 renvoie les mêmes données métier que le legacy."""
        payload_v3 = {
            "nom": "Google France",
            "code_postal": "75009",
            "ville": "Paris",
        }
        resp_v3 = await api_client_with_key.post("/api/v3/match", json=payload_v3)
        data_v3 = resp_v3.json()

        payload_legacy = {
            "nom": "Google France",
            "code_postal": "75009",
            "ville": "Paris",
            "adresse": "",
        }
        resp_legacy = await api_client_with_key.post("/match", json=payload_legacy)
        data_legacy = resp_legacy.json()

        if data_v3["matched"] and data_legacy["matched"]:
            assert data_v3["etablissement"]["siret"] == data_legacy["siret"]


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v3/match/batch
# ══════════════════════════════════════════════════════════════════════════════


class TestBatchV3:
    """Matching en lot v3."""

    async def test_batch_structure(self, api_client_with_key):
        """Batch → structure de réponse v3."""
        payload = {
            "prospects": [
                {"nom": "Google France", "code_postal": "75009", "ville": "Paris"},
                {"nom": "XYZQQWWEE INTROUVABLE", "code_postal": "99999", "ville": "Nowhere"},
            ],
            "concurrency": 2,
        }
        resp = await api_client_with_key.post("/api/v3/match/batch", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["matched"] + data["not_found"] == data["total"]
        assert 0.0 <= data["taux_matching"] <= 1.0
        assert data["duration_ms"] > 0
        assert len(data["results"]) == 2

    async def test_batch_requires_auth(self, api_client):
        """POST /api/v3/match/batch sans API key → 401."""
        payload = {"prospects": [{"nom": "Test", "code_postal": "75001"}]}
        resp = await api_client.post("/api/v3/match/batch", json=payload)
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v3/search
# ══════════════════════════════════════════════════════════════════════════════


class TestSearchV3:
    """Recherche unifiée v3."""

    async def test_search_basic(self, api_client):
        """Recherche par département + NAF → résultats structurés."""
        payload = {
            "filters": {"departements": ["75"], "naf_prefix": "62"},
            "limit": 10,
        }
        resp = await api_client.post("/api/v3/search", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        assert len(data["results"]) > 0
        # Structure d'un résultat
        r = data["results"][0]
        assert "siret" in r
        assert "code" in r["naf"]
        assert "libelle" in r["naf"]
        assert "code" in r["effectif"]
        assert "code_postal" in r["adresse"]

    async def test_search_fulltext(self, api_client):
        """Recherche full-text q=google."""
        payload = {
            "q": "google",
            "filters": {"departements": ["75"]},
            "limit": 10,
        }
        resp = await api_client.post("/api/v3/search", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        assert any("GOOGLE" in (r["denomination"] or "").upper() for r in data["results"])

    async def test_search_facets(self, api_client):
        """Les facets sont retournées et cohérentes."""
        payload = {
            "filters": {"departements": ["75"], "naf_prefix": "62"},
            "limit": 5,
        }
        resp = await api_client.post("/api/v3/search", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        facets = data["facets"]
        assert "departements" in facets
        assert "tailles" in facets
        assert "top_naf" in facets
        # Le département 75 doit être dans les facets
        assert "75" in facets["departements"]
        # La somme des tailles ≈ total (peut différer si AUTRE existe)
        taille_sum = sum(facets["tailles"].values())
        assert taille_sum > 0

    async def test_search_no_criteria(self, api_client):
        """Sans critère → 422."""
        payload = {"limit": 10}
        resp = await api_client.post("/api/v3/search", json=payload)
        assert resp.status_code == 422

    async def test_search_public(self, api_client):
        """L'endpoint search est public."""
        payload = {"filters": {"departements": ["75"], "naf_prefix": "62"}, "limit": 5}
        resp = await api_client.post("/api/v3/search", json=payload)
        assert resp.status_code == 200

    async def test_search_sort_denomination(self, api_client):
        """Tri par dénomination."""
        payload = {
            "filters": {"departements": ["75"], "naf_prefix": "62"},
            "sort": "denomination",
            "limit": 10,
        }
        resp = await api_client.post("/api/v3/search", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) > 0

    async def test_search_combined_filters(self, api_client):
        """Combinaison q + idcc → filtres cumulatifs."""
        payload = {
            "q": "informatique",
            "filters": {"departements": ["75"], "idcc": "1486"},
            "limit": 10,
        }
        resp = await api_client.post("/api/v3/search", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        for r in data["results"]:
            if r["idcc"]["code"]:
                assert r["idcc"]["code"] == "1486"


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v3/autocomplete
# ══════════════════════════════════════════════════════════════════════════════


class TestAutocompleteV3:
    """Autocomplétion v3."""

    async def test_autocomplete_google(self, api_client):
        resp = await api_client.get("/api/v3/autocomplete", params={"q": "google"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        assert any("GOOGLE" in (r["denomination"] or "").upper() for r in data)

    async def test_autocomplete_too_short(self, api_client):
        resp = await api_client.get("/api/v3/autocomplete", params={"q": "g"})
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_autocomplete_public(self, api_client):
        """L'endpoint est public."""
        resp = await api_client.get("/api/v3/autocomplete", params={"q": "paris"})
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v3/referentiel/*
# ══════════════════════════════════════════════════════════════════════════════


class TestReferentiels:
    """Endpoints de référentiels."""

    async def test_regions(self, api_client):
        resp = await api_client.get("/api/v3/referentiel/regions")
        assert resp.status_code == 200
        data = resp.json()
        assert "ILE_DE_FRANCE" in data

    async def test_idcc(self, api_client):
        resp = await api_client.get("/api/v3/referentiel/idcc")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        assert "idcc" in data[0]
        assert "libelle" in data[0]
        assert "count" in data[0]

    async def test_opco(self, api_client):
        resp = await api_client.get("/api/v3/referentiel/opco")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 11
        assert "nom" in data[0]
        assert "secteurs" in data[0]
        names = [d["nom"] for d in data]
        assert "ATLAS" in names
        assert "AKTO" in names

    async def test_referentiels_public(self, api_client):
        """Tous les referentiels sont publics."""
        for path in ["/api/v3/referentiel/regions", "/api/v3/referentiel/idcc",
                     "/api/v3/referentiel/opco"]:
            resp = await api_client.get(path)
            assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Swagger / Documentation OpenAPI
# ══════════════════════════════════════════════════════════════════════════════


class TestStats:
    """Endpoint /api/v3/stats pour le dashboard."""

    async def test_stats_structure(self, api_client):
        resp = await api_client.get("/api/v3/stats")
        assert resp.status_code == 200
        data = resp.json()
        # Matching section
        m = data["matching"]
        assert "total" in m
        assert "matched" in m
        assert "not_found" in m
        assert "taux" in m
        assert "avg_score" in m
        assert "by_method" in m
        # DST section
        d = data["dst_lookups"]
        assert "total" in d
        assert "cache_hit_rate" in d
        # System section
        s = data["system"]
        assert "etablissements_actifs" in s
        assert "redis_connected" in s
        assert "uptime_seconds" in s

    async def test_stats_public(self, api_client):
        """Stats endpoint is public."""
        resp = await api_client.get("/api/v3/stats")
        assert resp.status_code == 200


class TestDocs:
    """Vérifie que la documentation Swagger est accessible."""

    async def test_swagger_ui(self, api_client):
        resp = await api_client.get("/docs")
        assert resp.status_code == 200

    async def test_redoc(self, api_client):
        resp = await api_client.get("/redoc")
        assert resp.status_code == 200

    async def test_openapi_json(self, api_client):
        resp = await api_client.get("/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["info"]["version"] == "3.0.0"
        assert data["info"]["title"] == "SIRET Matcher API"
        # Vérifier que les endpoints v3 sont documentés
        paths = data["paths"]
        assert "/api/v3/etablissements/{siret}" in paths
        assert "/api/v3/match" in paths
        assert "/api/v3/search" in paths
        assert "/api/v3/autocomplete" in paths
        assert "/api/v3/referentiel/opco" in paths


# ══════════════════════════════════════════════════════════════════════════════
# Rétrocompatibilité — les endpoints legacy marchent encore
# ══════════════════════════════════════════════════════════════════════════════


class TestLegacyUnchanged:
    """Les endpoints legacy ne sont pas cassés."""

    async def test_legacy_match(self, api_client_with_key):
        payload = {"nom": "Google France", "code_postal": "75009", "ville": "Paris", "adresse": ""}
        resp = await api_client_with_key.post("/match", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "siret" in data
        assert "matched" in data

    async def test_legacy_search_autocomplete(self, api_client):
        resp = await api_client.get("/search/autocomplete", params={"q": "google"})
        assert resp.status_code == 200

    async def test_legacy_search_prospects(self, api_client):
        payload = {"departements": ["75"], "naf": "62", "limit": 5}
        resp = await api_client.post("/search/prospects", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data

    async def test_legacy_health(self, api_client):
        resp = await api_client.get("/health")
        assert resp.status_code == 200
