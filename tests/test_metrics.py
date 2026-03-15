"""Tests des métriques Prometheus."""
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_metrics_endpoint_returns_200(api_client):
    """GET /metrics retourne status 200."""
    resp = await api_client.get("/metrics")
    assert resp.status_code == 200


async def test_metrics_content_type(api_client):
    """GET /metrics retourne le bon content-type Prometheus."""
    resp = await api_client.get("/metrics")
    assert "text/plain" in resp.headers.get("content-type", "")


async def test_metrics_contains_expected_names(api_client):
    """Le contenu contient les noms de métriques attendus."""
    resp = await api_client.get("/metrics")
    body = resp.text
    expected = [
        "siret_matcher_requests_total",
        "siret_matcher_request_duration_seconds",
        "siret_matcher_match_total",
        "siret_matcher_match_score",
        "siret_matcher_match_method_total",
        "siret_matcher_match_stages_tried",
        "siret_matcher_dst_lookup_total",
        "siret_matcher_external_api_seconds",
        "siret_matcher_external_api_errors_total",
        "siret_matcher_db_pool_size",
        "siret_matcher_etablissements_total",
    ]
    for name in expected:
        assert name in body, f"Métrique manquante : {name}"


async def test_metrics_prometheus_format(api_client):
    """Le format est du texte Prometheus valide (lignes # HELP, # TYPE, valeurs)."""
    resp = await api_client.get("/metrics")
    lines = resp.text.strip().splitlines()
    has_help = any(l.startswith("# HELP") for l in lines)
    has_type = any(l.startswith("# TYPE") for l in lines)
    assert has_help, "Pas de ligne # HELP trouvée"
    assert has_type, "Pas de ligne # TYPE trouvée"


async def test_dst_lookup_increments_counter(api_client):
    """Après un appel à /api/dst/siret, la métrique dst_lookup_total est incrémentée."""
    # Lire la valeur avant
    resp_before = await api_client.get("/metrics")
    before_text = resp_before.text

    # Faire un lookup
    await api_client.get("/api/dst/siret/44306184100047")

    # Lire la valeur après
    resp_after = await api_client.get("/metrics")
    after_text = resp_after.text

    # Extraire la valeur du compteur found="true"
    def _extract_dst_found_true(text: str) -> float:
        for line in text.splitlines():
            if line.startswith("siret_matcher_dst_lookup_total") and 'found="true"' in line:
                return float(line.split()[-1])
        return 0.0

    before_val = _extract_dst_found_true(before_text)
    after_val = _extract_dst_found_true(after_text)
    assert after_val > before_val, (
        f"Le compteur dst_lookup_total(found=true) n'a pas été incrémenté: "
        f"{before_val} → {after_val}"
    )


async def test_metrics_not_logged_by_middleware(api_client, log_collector):
    """Les requêtes /metrics ne sont PAS loggées par le middleware."""
    log_collector.clear()
    await api_client.get("/metrics")
    request_logs = log_collector.find("request")
    for log in request_logs:
        assert log.get("path") != "/metrics", "/metrics ne devrait pas être loggé"


async def test_etablissements_gauge_set(api_client):
    """Le gauge etablissements_total est > 0 après startup."""
    resp = await api_client.get("/metrics")
    for line in resp.text.splitlines():
        if line.startswith("siret_matcher_etablissements_total "):
            val = float(line.split()[-1])
            assert val > 0, "Le gauge etablissements_total devrait être > 0"
            return
    pytest.fail("Métrique siret_matcher_etablissements_total non trouvée")


async def test_db_pool_gauge_set(api_client):
    """Le gauge db_pool_size est > 0 après startup."""
    resp = await api_client.get("/metrics")
    for line in resp.text.splitlines():
        if line.startswith("siret_matcher_db_pool_size "):
            val = float(line.split()[-1])
            assert val > 0, "Le gauge db_pool_size devrait être > 0"
            return
    pytest.fail("Métrique siret_matcher_db_pool_size non trouvée")


async def test_match_increments_counters(api_client_with_key):
    """Après un POST /match, les métriques match_total et match_method sont incrémentées."""
    resp_before = await api_client_with_key.get("/metrics")

    payload = {"nom": "Google France", "code_postal": "75009", "ville": "Paris"}
    await api_client_with_key.post("/match", json=payload)

    resp_after = await api_client_with_key.get("/metrics")

    def _sum_match_total(text: str) -> float:
        total = 0.0
        for line in text.splitlines():
            if line.startswith("siret_matcher_match_total{"):
                total += float(line.split()[-1])
        return total

    before_val = _sum_match_total(resp_before.text)
    after_val = _sum_match_total(resp_after.text)
    assert after_val > before_val, "match_total n'a pas été incrémenté"
