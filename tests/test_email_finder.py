"""Tests du module de détection d'email professionnel."""
import pytest

from siret_matcher.enrichment.email_finder import (
    extract_emails_from_html,
    filter_emails,
    extract_domain,
    check_mx_record,
    generate_dirigeant_patterns,
    EmailResult,
)


# ══════════════════════════════════════════════════════════════════════════════
# Extraction d'emails depuis HTML
# ══════════════════════════════════════════════════════════════════════════════


class TestExtractEmails:
    def test_basic_extraction(self):
        html = '<p>Contactez-nous : contact@example.fr</p>'
        assert extract_emails_from_html(html) == ["contact@example.fr"]

    def test_multiple_emails(self):
        html = '''
        <p>contact@acme.fr</p>
        <p>info@acme.fr</p>
        <p>rh@acme.fr</p>
        '''
        result = extract_emails_from_html(html)
        assert len(result) == 3
        assert "contact@acme.fr" in result

    def test_html_encoded_at(self):
        html = '<p>contact&#64;example.fr</p>'
        assert extract_emails_from_html(html) == ["contact@example.fr"]

    def test_obfuscated_at(self):
        html = '<p>contact[at]example[dot]fr</p>'
        assert extract_emails_from_html(html) == ["contact@example.fr"]

    def test_deduplication(self):
        html = '<p>a@b.fr a@b.fr A@B.FR</p>'
        assert extract_emails_from_html(html) == ["a@b.fr"]

    def test_empty_html(self):
        assert extract_emails_from_html("") == []
        assert extract_emails_from_html(None) == []

    def test_no_emails(self):
        html = '<p>Pas d\'email ici</p>'
        assert extract_emails_from_html(html) == []

    def test_complex_html(self):
        html = '''
        <footer>
            <a href="mailto:contact@entreprise.com">contact@entreprise.com</a>
            <span>Tél: 01 23 45 67 89</span>
            <a href="mailto:info@entreprise.com">info@entreprise.com</a>
            <img src="logo@2x.png" />
        </footer>
        '''
        result = extract_emails_from_html(html)
        assert "contact@entreprise.com" in result
        assert "info@entreprise.com" in result


# ══════════════════════════════════════════════════════════════════════════════
# Filtrage et priorisation
# ══════════════════════════════════════════════════════════════════════════════


class TestFilterEmails:
    def test_blacklist(self):
        emails = ["noreply@acme.fr", "contact@acme.fr", "postmaster@acme.fr"]
        result = filter_emails(emails)
        assert result == ["contact@acme.fr"]

    def test_priority_order(self):
        emails = ["rh@acme.fr", "info@acme.fr", "contact@acme.fr", "ventes@acme.fr"]
        result = filter_emails(emails)
        assert result[0] == "contact@acme.fr"
        assert result[1] == "info@acme.fr"

    def test_file_extensions(self):
        emails = ["logo@2x.png", "contact@acme.fr"]
        result = filter_emails(emails)
        assert result == ["contact@acme.fr"]


# ══════════════════════════════════════════════════════════════════════════════
# Extraction de domaine
# ══════════════════════════════════════════════════════════════════════════════


class TestExtractDomain:
    def test_simple_url(self):
        assert extract_domain("https://google.fr") == "google.fr"

    def test_www(self):
        assert extract_domain("https://www.google.fr") == "google.fr"

    def test_no_protocol(self):
        assert extract_domain("google.fr") == "google.fr"

    def test_with_path(self):
        assert extract_domain("https://www.acme.com/contact") == "acme.com"

    def test_empty(self):
        assert extract_domain("") == ""


# ══════════════════════════════════════════════════════════════════════════════
# MX Record
# ══════════════════════════════════════════════════════════════════════════════


class TestMxRecord:
    def test_known_domain(self):
        """google.com a forcément un MX record."""
        assert check_mx_record("google.com") is True

    def test_nonexistent_domain(self):
        """Domaine inexistant → False."""
        assert check_mx_record("zzxxyyqqww-nonexistent.xyz") is False

    def test_empty(self):
        assert check_mx_record("") is False


# ══════════════════════════════════════════════════════════════════════════════
# Patterns dirigeant
# ══════════════════════════════════════════════════════════════════════════════


class TestDirigeantPatterns:
    def test_basic(self):
        patterns = generate_dirigeant_patterns("DUPONT", "Jean", "acme.fr")
        assert "jean.dupont@acme.fr" in patterns
        assert "j.dupont@acme.fr" in patterns
        assert "jean@acme.fr" in patterns
        assert "dupont@acme.fr" in patterns

    def test_accents(self):
        patterns = generate_dirigeant_patterns("LÉGER", "François", "acme.fr")
        assert "francois.leger@acme.fr" in patterns

    def test_composed_name(self):
        patterns = generate_dirigeant_patterns("MARTIN", "Jean-Pierre", "acme.fr")
        # Premier prénom utilisé
        assert "jean.martin@acme.fr" in patterns

    def test_no_domain(self):
        assert generate_dirigeant_patterns("DUPONT", "Jean", "") == []

    def test_no_nom(self):
        assert generate_dirigeant_patterns("", "Jean", "acme.fr") == []

    def test_no_prenom(self):
        patterns = generate_dirigeant_patterns("DUPONT", "", "acme.fr")
        assert "dupont@acme.fr" in patterns
        assert len(patterns) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Test API enrichissement avec emails
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestEmailEnrichAPI:
    async def test_enrich_has_emails_field(self, api_client):
        """L'endpoint /enrich retourne le champ emails."""
        resp = await api_client.get("/api/v3/etablissements/44306184100047/enrich")
        assert resp.status_code == 200
        data = resp.json()
        assert "emails" in data
        assert isinstance(data["emails"], list)

    async def test_enrich_with_site_web(self, api_client):
        """Avec site_web en paramètre, l'email finder est déclenché."""
        resp = await api_client.get(
            "/api/v3/etablissements/44306184100047/enrich",
            params={"site_web": "google.fr"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # On devrait avoir au moins des emails (verified ou suggested)
        assert "emails" in data
