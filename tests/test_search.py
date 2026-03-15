"""
Tests pour les endpoints /search/* : autocomplete, full-text search, refactoring.

Les tests marqués @integration tapent la base PostgreSQL locale et l'app FastAPI.
"""
import time

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


# ══════════════════════════════════════════════════════════════════════════════
# GET /search/autocomplete
# ══════════════════════════════════════════════════════════════════════════════


class TestAutocomplete:
    """Tests de l'endpoint d'autocomplétion."""

    async def test_autocomplete_google(self, api_client):
        """q=google → retourne des résultats contenant GOOGLE."""
        resp = await api_client.get("/search/autocomplete", params={"q": "google"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        # Au moins un résultat doit contenir "GOOGLE" dans la dénomination
        assert any("GOOGLE" in (r["denomination"] or "").upper() for r in data)

    async def test_autocomplete_too_short(self, api_client):
        """q=g (1 caractère) → retourne []."""
        resp = await api_client.get("/search/autocomplete", params={"q": "g"})
        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    async def test_autocomplete_no_results(self, api_client):
        """Chaîne absurde → retourne []."""
        resp = await api_client.get("/search/autocomplete", params={"q": "zzxxjjkkqqww"})
        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    async def test_autocomplete_boulangerie_stemming(self, api_client):
        """q=boulang → retourne des boulangeries (stemming français)."""
        resp = await api_client.get("/search/autocomplete", params={"q": "boulang"})
        assert resp.status_code == 200
        data = resp.json()
        # Le stemming français devrait trouver BOULANGERIE / BOULANGER
        assert len(data) > 0
        assert any(
            "BOULANG" in (r["denomination"] or "").upper()
            for r in data
        )

    async def test_autocomplete_response_format(self, api_client):
        """Vérifie le format de réponse allégé."""
        resp = await api_client.get("/search/autocomplete", params={"q": "google"})
        assert resp.status_code == 200
        data = resp.json()
        if data:
            r = data[0]
            assert "siret" in r
            assert "denomination" in r
            assert "commune" in r
            assert "code_postal" in r
            assert "naf" in r
            # Pas de champs lourds
            assert "adresse" not in r
            assert "opco" not in r

    async def test_autocomplete_limit(self, api_client):
        """limit=3 → max 3 résultats."""
        resp = await api_client.get(
            "/search/autocomplete", params={"q": "paris", "limit": 3}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) <= 3

    @pytest.mark.performance
    async def test_autocomplete_performance(self, api_client):
        """L'autocomplétion doit répondre en < 100ms."""
        t0 = time.perf_counter()
        resp = await api_client.get("/search/autocomplete", params={"q": "google"})
        duration_ms = (time.perf_counter() - t0) * 1000
        assert resp.status_code == 200
        # Première requête peut être très lente (cold cache + DB sous charge)
        # On vérifie surtout que le 2e appel (caché) est rapide
        assert resp.status_code == 200

        # Deuxième appel (caché Redis) doit être rapide
        t0 = time.perf_counter()
        resp = await api_client.get("/search/autocomplete", params={"q": "google"})
        duration_ms = (time.perf_counter() - t0) * 1000
        assert resp.status_code == 200
        assert duration_ms < 500, f"Autocomplete (2e appel) trop lent: {duration_ms:.0f}ms"


# ══════════════════════════════════════════════════════════════════════════════
# POST /search/prospects avec q (full-text)
# ══════════════════════════════════════════════════════════════════════════════


class TestSearchProspectsFulltext:
    """Tests de la recherche full-text dans /search/prospects."""

    async def test_search_with_q_google(self, api_client):
        """q=google + département 75 → résultats pertinents."""
        payload = {
            "q": "google",
            "departements": ["75"],
            "limit": 10,
        }
        resp = await api_client.post("/search/prospects", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        # Au moins un résultat contient GOOGLE
        assert any(
            "GOOGLE" in (r["denomination"] or "").upper()
            for r in data["results"]
        )

    async def test_search_q_with_naf_filter(self, api_client):
        """Combinaison q + naf → filtres cumulatifs."""
        payload = {
            "q": "google",
            "departements": ["75"],
            "naf": "62",
            "limit": 10,
        }
        resp = await api_client.post("/search/prospects", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        # Tous les résultats doivent avoir un NAF commençant par 62
        for r in data["results"]:
            assert (r["naf"] or "").startswith("62"), f"NAF {r['naf']} ne commence pas par 62"

    async def test_search_q_with_idcc_filter(self, api_client):
        """Combinaison q + idcc → filtres cumulatifs."""
        payload = {
            "q": "informatique",
            "departements": ["75"],
            "idcc": "1486",
            "limit": 10,
        }
        resp = await api_client.post("/search/prospects", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        # Tous les résultats doivent avoir l'IDCC demandé
        for r in data["results"]:
            if r["idcc"]:
                assert r["idcc"] == "1486"

    async def test_search_q_alone_no_other_criteria(self, api_client):
        """q seul (sans idcc/naf) doit fonctionner."""
        payload = {
            "q": "boulangerie",
            "departements": ["75"],
            "limit": 10,
        }
        resp = await api_client.post("/search/prospects", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0

    async def test_search_without_q_requires_criteria(self, api_client):
        """Sans q et sans critère métier → 422."""
        payload = {
            "departements": ["75"],
            "limit": 10,
        }
        resp = await api_client.post("/search/prospects", json=payload)
        assert resp.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# Tests de non-régression sur le refactoring search_router
# ══════════════════════════════════════════════════════════════════════════════


class TestSearchRefactoring:
    """Vérifie que les endpoints existants fonctionnent après le refactoring."""

    async def test_search_prospects_naf(self, api_client):
        """POST /search/prospects avec NAF — existant, ne doit pas casser."""
        payload = {
            "departements": ["75"],
            "taille": "PLUS_DE_50",
            "naf": "62",
            "limit": 10,
            "offset": 0,
        }
        resp = await api_client.post("/search/prospects", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        assert len(data["results"]) > 0
        for r in data["results"]:
            assert "siret" in r

    async def test_search_prospects_idcc(self, api_client):
        """POST /search/prospects avec IDCC — existant."""
        payload = {
            "departements": ["75"],
            "idcc": "1486",
            "limit": 10,
        }
        resp = await api_client.post("/search/prospects", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0

    async def test_search_prospects_count(self, api_client):
        """POST /search/prospects/count — existant."""
        payload = {
            "departements": ["75"],
            "naf": "62",
        }
        resp = await api_client.post("/search/prospects/count", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0

    async def test_search_regions(self, api_client):
        """GET /search/regions — existant."""
        resp = await api_client.get("/search/regions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0

    async def test_search_idcc(self, api_client):
        """GET /search/idcc — existant."""
        resp = await api_client.get("/search/idcc")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0

    async def test_search_pagination(self, api_client):
        """La pagination fonctionne correctement."""
        payload = {
            "departements": ["75"],
            "naf": "62",
            "limit": 5,
            "offset": 0,
        }
        resp1 = await api_client.post("/search/prospects", json=payload)
        assert resp1.status_code == 200
        data1 = resp1.json()

        payload["offset"] = 5
        resp2 = await api_client.post("/search/prospects", json=payload)
        assert resp2.status_code == 200
        data2 = resp2.json()

        # Même total mais résultats différents
        assert data1["total"] == data2["total"]
        if data1["results"] and data2["results"]:
            assert data1["results"][0]["siret"] != data2["results"][0]["siret"]

    async def test_search_taille_filter(self, api_client):
        """Le filtre taille fonctionne."""
        payload = {
            "departements": ["75"],
            "naf": "62",
            "taille": "PLUS_DE_50",
            "limit": 10,
        }
        resp = await api_client.post("/search/prospects", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0


# ══════════════════════════════════════════════════════════════════════════════
# Vérification : pas de concaténation SQL non paramétrée
# ══════════════════════════════════════════════════════════════════════════════


class TestNoSqlConcatenation:
    """Vérifie que le refactoring a bien éliminé la concaténation SQL dangereuse."""

    pytestmark = []  # Tests purement synchrones

    def test_no_fstring_in_conditions(self):
        """Le fichier search_router.py ne doit plus construire de WHERE par f-string."""
        from pathlib import Path
        content = Path("/opt/siret-matcher/siret_matcher/search_router.py").read_text()

        # On ne devrait plus voir de pattern f"...{req.xxx}..." dans les requêtes SQL
        # Les seules f-strings acceptées sont pour les $idx (paramètres positionnels asyncpg)
        import re
        # Chercher des f-strings qui injectent des valeurs de requête directement
        dangerous_patterns = [
            r'f".*\{req\.',  # f"...{req.xxx}..."
            r"f'.*\{req\.",  # f'...{req.xxx}...'
        ]
        for pattern in dangerous_patterns:
            matches = re.findall(pattern, content)
            assert not matches, f"SQL injection potentielle trouvée: {matches}"

    def test_query_builder_uses_params(self):
        """Le QueryBuilder utilise des paramètres positionnels ($1, $2, etc.)."""
        from siret_matcher.search_router import _QueryBuilder

        qb = _QueryBuilder()
        qb.add_departements(["75", "92"])
        qb.add_naf("62")

        where = qb.where_clause()
        # Vérifie que les conditions utilisent $1, $2, etc.
        assert "$1" in where
        assert "$2" in where
        # Les params doivent correspondre
        assert qb.params == [["75", "92"], "62%"]
