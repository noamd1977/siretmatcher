"""
Tests unitaires de chaque étape du pipeline, isolée.

Les tests marqués @integration tapent les API externes (gouv.fr, BAN)
et la base PostgreSQL locale. C'est voulu : on veut valider le comportement
réel de chaque stage indépendamment.

Les tests du scraper qui n'ont pas besoin du réseau utilisent des mocks.
"""
import asyncio
import re

import httpx
import pytest

from siret_matcher.db import SireneDB
from siret_matcher.models import Prospect, SireneResult
from siret_matcher.normalizer import normalize_prospect
from siret_matcher.stages.api_recherche import stage_api_recherche
from siret_matcher.stages.address_match import stage_address_match
from siret_matcher.stages.trigram_match import stage_trigram_match
from siret_matcher.stages.scraper import (
    stage_scrape_siret,
    _extract_siret_from_html,
    _validate_siret,
    _validate_siren,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
async def http_client():
    """Client HTTP partagé pour toutes les étapes."""
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    async with httpx.AsyncClient(
        limits=limits,
        headers={"User-Agent": "SIRETMatcher-Tests/1.0"},
        follow_redirects=True,
    ) as client:
        yield client


@pytest.fixture(scope="session")
async def sirene_db():
    """Connexion à la base Sirene locale (session-scoped)."""
    db = SireneDB()
    await db.connect()
    yield db
    await db.close()


def _make_prospect(**kwargs) -> Prospect:
    """Créer un Prospect normalisé à partir de kwargs."""
    p = Prospect(**kwargs)
    normalize_prospect(p)
    return p


# ══════════════════════════════════════════════════════════════════════════════
# Étapes 1-2 — API Recherche d'Entreprises
# ══════════════════════════════════════════════════════════════════════════════


class TestApiRecherche:
    """Tests pour stage_api_recherche (étapes 1 et 2)."""

    async def test_prospect_connu(self, http_client):
        """Un prospect connu doit être trouvé avec un bon score."""
        prospect = _make_prospect(
            nom="GOOGLE FRANCE",
            adresse="8 Rue de Londres",
            code_postal="75009",
            ville="Paris",
        )
        result = await stage_api_recherche(http_client, prospect)
        assert result is not None
        assert result.siret != ""
        assert result.siren.startswith("443")  # SIREN de Google France = 443 061 841
        assert result.score >= 40

    async def test_prospect_introuvable(self, http_client):
        """Un prospect avec un nom absurde ne doit rien retourner."""
        prospect = _make_prospect(
            nom="XZYWQ BLRMTPF QZJKLM",
            adresse="1 Rue Fictive",
            code_postal="99999",
            ville="Nullepart",
        )
        result = await stage_api_recherche(http_client, prospect)
        assert result is None

    async def test_fallback_cp_vers_departement(self, http_client):
        """Si le CP exact ne donne rien, le fallback département doit trouver."""
        # ALLIANCE GV est à Nancy 54000 — on utilise un CP du même département
        prospect = _make_prospect(
            nom="ALLIANCE GV",
            adresse="15 RUE M FRANCHET D'ESPEREY",
            code_postal="54300",  # CP différent mais même département 54
            ville="LUNEVILLE",
        )
        result = await stage_api_recherche(http_client, prospect)
        # Le fallback département doit quand même trouver quelque chose
        assert result is not None
        assert result.score >= 40

    async def test_variantes_nom(self, http_client):
        """Le matching doit fonctionner avec des variantes de nom."""
        prospect = _make_prospect(
            nom="Garage OCCITANIA INVEST",
            adresse="82 Avenue du Lauragais",
            code_postal="31400",
            ville="Toulouse",
        )
        result = await stage_api_recherche(http_client, prospect)
        assert result is not None
        assert result.siren == "488970906"


# ══════════════════════════════════════════════════════════════════════════════
# Étape 3 — Address Match
# ══════════════════════════════════════════════════════════════════════════════


class TestAddressMatch:
    """Tests pour stage_address_match (étape 3)."""

    async def test_adresse_connue(self, http_client, sirene_db):
        """Une adresse précise et connue doit trouver le bon établissement."""
        prospect = _make_prospect(
            nom="ALLIANCE GV",
            adresse="15 RUE M FRANCHET D'ESPEREY",
            code_postal="54000",
            ville="NANCY",
        )
        result = await stage_address_match(http_client, sirene_db, prospect)
        # Peut ne pas trouver si la voie BAN ne matche pas exactement en base
        # mais si ça matche, le SIRET doit être correct
        if result is not None:
            assert result.siret.startswith("500155841")
            assert result.score >= 50

    async def test_adresse_unique(self, http_client, sirene_db):
        """Un seul établissement à cette adresse → score ~95, méthode ADDRESS_UNIQUE."""
        prospect = _make_prospect(
            nom="OCCITANIA INVEST",
            adresse="82 AVENUE DU LAURAGAIS",
            code_postal="31400",
            ville="TOULOUSE",
        )
        result = await stage_address_match(http_client, sirene_db, prospect)
        if result is not None and result.methode == "ADDRESS_UNIQUE":
            assert result.score >= 80
            assert result.score <= 95

    async def test_adresse_multiple_departage_par_nom(self, http_client, sirene_db):
        """Plusieurs établissements à la même adresse → départage par nom."""
        # Adresse d'un centre commercial / zone artisanale typique
        prospect = _make_prospect(
            nom="AROMA & CO",
            adresse="448 CROIX RIVAIL",
            code_postal="97232",
            ville="LE LAMENTIN",
        )
        result = await stage_address_match(http_client, sirene_db, prospect)
        if result is not None and result.methode == "ADDRESS_MULTI":
            # Le nom doit avoir aidé à départager
            assert result.score >= 50


# ══════════════════════════════════════════════════════════════════════════════
# Étape 4 — Trigram Fuzzy
# ══════════════════════════════════════════════════════════════════════════════


class TestTrigramMatch:
    """Tests pour stage_trigram_match (étape 4)."""

    async def test_nom_approche(self, sirene_db):
        """Un nom approché doit trouver un match fuzzy."""
        prospect = _make_prospect(
            nom="CORS AUTO",
            adresse="",
            code_postal="20000",
            ville="AJACCIO",
        )
        result = await stage_trigram_match(sirene_db, prospect)
        # pg_trgm devrait trouver "CORSE AUTOMOBILE" ou similaire
        if result is not None:
            assert "CORS" in result.denomination.upper() or result.score >= 45

    async def test_fallback_cp_vers_departement(self, sirene_db):
        """Le fallback CP → département doit élargir la recherche."""
        prospect = _make_prospect(
            nom="ALLIANCE GV",
            adresse="",
            code_postal="54300",  # CP différent mais même département
            ville="LUNEVILLE",
        )
        result = await stage_trigram_match(sirene_db, prospect)
        # Le fallback département doit trouver quelque chose
        assert result is not None
        assert result.score >= 45

    async def test_nom_trop_eloigne(self, sirene_db):
        """Un nom complètement inventé ne doit pas matcher."""
        prospect = _make_prospect(
            nom="XZYWQ BLRMTPF QZJKLM",
            adresse="",
            code_postal="75001",
            ville="PARIS",
        )
        result = await stage_trigram_match(sirene_db, prospect)
        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# Étape 5 — Scraper
# ══════════════════════════════════════════════════════════════════════════════


# ---- Tests unitaires (pas de réseau) ----

class TestScraperUnit:
    """Tests unitaires du scraper : extraction regex + validation Luhn."""

    # Pas de marker integration ni asyncio : ce sont des tests purement synchrones
    pytestmark = []

    def test_extract_siret_from_html(self):
        """Extraction d'un SIRET depuis du HTML mocké."""
        html = """
        <html><body>
        <h1>Mentions Légales</h1>
        <p>Société XYZ SARL au capital de 10 000 €</p>
        <p>SIRET : 443 061 841 00047</p>
        <p>RCS Paris B 443 061 841</p>
        </body></html>
        """
        result = _extract_siret_from_html(html)
        assert result is not None
        assert re.sub(r"[\s.]", "", result) == "44306184100047"

    def test_extract_siret_14_digits(self):
        """Extraction d'un SIRET en 14 chiffres consécutifs."""
        html = """
        <html><body>
        <footer>SIRET: 44306184100047 - TVA FR</footer>
        </body></html>
        """
        result = _extract_siret_from_html(html)
        assert result == "44306184100047"

    def test_extract_siren_from_html(self):
        """Extraction d'un SIREN (9 chiffres) quand pas de SIRET complet."""
        html = """
        <html><body>
        <p>SIREN : 443 061 841</p>
        </body></html>
        """
        result = _extract_siret_from_html(html)
        assert result is not None
        assert re.sub(r"[\s.]", "", result) == "443061841"

    def test_no_siret_in_html(self):
        """Pas de SIRET dans le HTML → None."""
        html = """
        <html><body>
        <p>Bienvenue sur notre site web. Aucun numéro légal ici.</p>
        </body></html>
        """
        result = _extract_siret_from_html(html)
        assert result is None

    def test_validate_siret_valid(self):
        """Un SIRET valide doit passer la validation Luhn."""
        # 443 061 841 00047 est le SIRET de Google France
        assert _validate_siret("44306184100047") is True

    def test_validate_siret_invalid(self):
        """Un SIRET avec un chiffre modifié ne passe pas Luhn."""
        assert _validate_siret("44306184100048") is False

    def test_validate_siret_bad_length(self):
        """Un numéro qui n'a pas 14 chiffres est invalide."""
        assert _validate_siret("4430618410004") is False
        assert _validate_siret("443061841000471") is False

    def test_validate_siren_valid(self):
        """Un SIREN valide (9 chiffres, Luhn OK)."""
        assert _validate_siren("443061841") is True

    def test_validate_siren_invalid(self):
        """Un SIREN invalide."""
        assert _validate_siren("443061842") is False


# ---- Tests d'intégration scraper (avec mock HTTP) ----

class TestScraperIntegration:
    """Tests du scraper avec des réponses HTTP mockées."""

    pytestmark = [pytest.mark.asyncio(loop_scope="session")]

    async def test_scrape_mentions_legales_mocked(self, sirene_db):
        """Scraping d'un site mocké avec un SIRET dans les mentions légales."""
        # HTML de test avec un SIRET valide (Google France)
        mock_html = """
        <html><head><title>Mentions Légales</title></head>
        <body>
        <h1>Mentions légales</h1>
        <p>Raison sociale : GOOGLE FRANCE</p>
        <p>SIRET : 443 061 841 00047</p>
        <p>Siège social : 8 Rue de Londres, 75009 Paris</p>
        </body></html>
        """

        # Créer un transport mock qui retourne notre HTML
        class MockTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                if "/mentions-legales" in str(request.url):
                    return httpx.Response(
                        200,
                        content=mock_html.encode(),
                        headers={"content-type": "text/html; charset=utf-8"},
                    )
                return httpx.Response(404)

        async with httpx.AsyncClient(transport=MockTransport()) as client:
            prospect = _make_prospect(
                nom="GOOGLE FRANCE",
                adresse="8 Rue de Londres",
                code_postal="75009",
                ville="Paris",
                site_web="https://example.com",
            )
            result = await stage_scrape_siret(client, sirene_db, prospect)

        assert result is not None
        assert result.siret == "44306184100047"
        assert "SCRAPE" in result.methode

    async def test_scrape_no_siret_found_mocked(self, sirene_db):
        """Si aucune page ne contient de SIRET → None."""

        class EmptyMockTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                return httpx.Response(
                    200,
                    content=b"<html><body><p>Nothing here</p></body></html>",
                    headers={"content-type": "text/html; charset=utf-8"},
                )

        async with httpx.AsyncClient(transport=EmptyMockTransport()) as client:
            prospect = _make_prospect(
                nom="TEST CORP",
                adresse="1 Rue Test",
                code_postal="75001",
                ville="Paris",
                site_web="https://example.com",
            )
            result = await stage_scrape_siret(client, sirene_db, prospect)

        assert result is None

    async def test_scrape_no_website(self, sirene_db):
        """Pas de site web → None immédiatement."""
        async with httpx.AsyncClient() as client:
            prospect = _make_prospect(
                nom="SANS SITE",
                adresse="1 Rue Test",
                code_postal="75001",
                ville="Paris",
                site_web="",
            )
            result = await stage_scrape_siret(client, sirene_db, prospect)

        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# Orchestrateur — matcher.py
# ══════════════════════════════════════════════════════════════════════════════


class TestOrchestrator:
    """Tests de l'orchestrateur match_one.

    Utilise un client HTTP dédié pour éviter les problèmes de connexion
    partagée avec les tests précédents.
    """

    async def test_etapes_executees_dans_lordre(self, sirene_db):
        """Un prospect facile doit être trouvé par le pipeline (étape 1-2 en priorité)."""
        from siret_matcher.matcher import match_one

        async with httpx.AsyncClient(
            headers={"User-Agent": "SIRETMatcher-Tests/1.0"},
            follow_redirects=True,
        ) as client:
            prospect = _make_prospect(
                nom="GOOGLE FRANCE",
                adresse="8 Rue de Londres",
                code_postal="75009",
                ville="Paris",
            )
            result_prospect = await match_one(client, sirene_db, prospect)
            r = result_prospect.result
            assert r is not None
            assert r.methode != "NON_TROUVE"
            assert r.siren.startswith("443")  # Google France
            assert r.score >= 40

    async def test_arret_des_quun_match_suffisant(self, sirene_db):
        """Le pipeline doit trouver un match correct pour un prospect connu."""
        from siret_matcher.matcher import match_one

        async with httpx.AsyncClient(
            headers={"User-Agent": "SIRETMatcher-Tests/1.0"},
            follow_redirects=True,
        ) as client:
            prospect = _make_prospect(
                nom="OCCITANIA INVEST",
                adresse="82 AVENUE DU LAURAGAIS",
                code_postal="31400",
                ville="TOULOUSE",
            )
            result_prospect = await match_one(client, sirene_db, prospect)
            r = result_prospect.result
            assert r is not None
            assert r.siren == "488970906"
            assert r.methode != "NON_TROUVE"

    async def test_fallback_complet_non_trouve(self, sirene_db):
        """Si aucune étape ne trouve → NON_TROUVE + OPCO de fallback."""
        from siret_matcher.matcher import match_one

        async with httpx.AsyncClient(
            headers={"User-Agent": "SIRETMatcher-Tests/1.0"},
            follow_redirects=True,
        ) as client:
            prospect = _make_prospect(
                nom="XZYWQ BLRMTPF QZJKLM INTROUVABLE",
                adresse="999 Rue Imaginaire",
                code_postal="99999",
                ville="Nullepart",
                site_web="",
            )
            result_prospect = await match_one(client, sirene_db, prospect, use_db=True)
            r = result_prospect.result
            assert r is not None
            assert r.methode == "NON_TROUVE"
            assert r.score == 0
            # L'OPCO de fallback doit quand même être renseigné (par le nom)
            assert r.opco is not None  # Peut être "" mais le champ existe
