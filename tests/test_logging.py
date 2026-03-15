"""Tests du logging structuré JSON."""
import json
import logging

import pytest

from siret_matcher.logging_config import JSONFormatter, log_structured, setup_logging

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_logger(capfd):
    """Crée un logger isolé qui écrit en JSON sur stderr (capturé par capfd)."""
    name = f"test_json_{id(capfd)}"
    lg = logging.getLogger(name)
    lg.handlers.clear()
    handler = logging.StreamHandler()  # stderr par défaut
    handler.setFormatter(JSONFormatter())
    lg.addHandler(handler)
    lg.setLevel(logging.DEBUG)
    lg.propagate = False
    return lg


def _capture_json(capfd) -> dict:
    """Lit la dernière ligne JSON émise sur stderr."""
    _, err = capfd.readouterr()
    lines = [l for l in err.strip().splitlines() if l.strip()]
    assert lines, "Aucune ligne de log capturée"
    return json.loads(lines[-1])


# ── Tests format JSON ────────────────────────────────────────────────────────


def test_json_format_basic(capfd):
    """Un log simple produit du JSON valide avec les champs obligatoires."""
    lg = _make_logger(capfd)
    lg.info("hello test")
    entry = _capture_json(capfd)
    assert entry["level"] == "INFO"
    assert entry["message"] == "hello test"
    assert "timestamp" in entry
    assert "logger" in entry


def test_json_format_structured_fields(capfd):
    """log_structured ajoute des champs extra au JSON."""
    lg = _make_logger(capfd)
    log_structured(lg, logging.INFO, "dst_lookup", siret="44306184100047", found=True, duration_ms=12)
    entry = _capture_json(capfd)
    assert entry["message"] == "dst_lookup"
    assert entry["siret"] == "44306184100047"
    assert entry["found"] is True
    assert entry["duration_ms"] == 12


def test_json_timestamp_format(capfd):
    """Le timestamp est au format ISO 8601 avec millisecondes."""
    lg = _make_logger(capfd)
    lg.info("ts test")
    entry = _capture_json(capfd)
    ts = entry["timestamp"]
    # Format attendu : 2026-03-15T14:30:00.123Z
    assert ts.endswith("Z")
    assert "T" in ts
    # Vérifier la présence des millisecondes (3 chiffres avant Z)
    assert len(ts.split(".")[-1]) == 4  # "123Z"


def test_json_debug_level(capfd):
    """Les logs DEBUG sont bien émis quand le niveau le permet."""
    lg = _make_logger(capfd)
    log_structured(lg, logging.DEBUG, "stage_result", stage="api_recherche_cp", found=False, duration_ms=180)
    entry = _capture_json(capfd)
    assert entry["level"] == "DEBUG"
    assert entry["stage"] == "api_recherche_cp"


def test_json_one_line_per_log(capfd):
    """Chaque log = exactement une ligne."""
    lg = _make_logger(capfd)
    lg.info("line1")
    lg.warning("line2")
    _, err = capfd.readouterr()
    lines = [l for l in err.strip().splitlines() if l.strip()]
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # Chaque ligne est du JSON valide


# ── Tests setup_logging ──────────────────────────────────────────────────────


def test_setup_logging_creates_handler():
    """setup_logging configure le logger siret_matcher."""
    root = logging.getLogger("siret_matcher")
    old_handlers = list(root.handlers)
    root.handlers.clear()
    try:
        setup_logging()
        assert len(root.handlers) > 0
        assert isinstance(root.handlers[0].formatter, JSONFormatter)
    finally:
        # Restaurer les handlers pour ne pas casser les autres tests
        root.handlers.clear()
        root.handlers.extend(old_handlers)


def test_setup_logging_idempotent():
    """Appeler setup_logging deux fois ne duplique pas les handlers."""
    root = logging.getLogger("siret_matcher")
    old_handlers = list(root.handlers)
    root.handlers.clear()
    try:
        setup_logging()
        n = len(root.handlers)
        setup_logging()
        assert len(root.handlers) == n
    finally:
        root.handlers.clear()
        root.handlers.extend(old_handlers)


# ── Tests middleware (via API client + collecteur) ───────────────────────────


async def test_middleware_logs_request(api_client, log_collector):
    """Le middleware logge chaque requête avec method, path, status, duration_ms."""
    log_collector.clear()
    resp = await api_client.get("/api/dst/siret/44306184100047")
    assert resp.status_code == 200
    request_logs = log_collector.find("request")
    assert len(request_logs) >= 1, f"Pas de log 'request'. Logs: {log_collector.records_json}"
    log = request_logs[-1]
    assert log["method"] == "GET"
    assert "/api/dst/siret/" in log["path"]
    assert "status" in log
    assert "duration_ms" in log
    assert isinstance(log["duration_ms"], (int, float))


async def test_middleware_skips_health(api_client, log_collector):
    """Le middleware ne logge PAS les requêtes /health."""
    log_collector.clear()
    await api_client.get("/health")
    request_logs = log_collector.find("request")
    for log in request_logs:
        assert log.get("path") != "/health", "/health ne devrait pas être loggé"


# ── Tests logs structurés des endpoints ──────────────────────────────────────


async def test_dst_lookup_structured_log(api_client, log_collector):
    """GET /api/dst/siret émet un log structuré dst_lookup."""
    log_collector.clear()
    await api_client.get("/api/dst/siret/44306184100047")
    dst_logs = log_collector.find("dst_lookup")
    assert len(dst_logs) >= 1, f"Pas de log 'dst_lookup'. Logs: {log_collector.records_json}"
    log = dst_logs[-1]
    assert log["siret"] == "44306184100047"
    assert "found" in log
    assert "duration_ms" in log


async def test_match_single_structured_log(api_client_with_key, log_collector):
    """POST /match émet un log structuré match_single."""
    log_collector.clear()
    payload = {"nom": "Google France", "code_postal": "75009", "ville": "Paris"}
    await api_client_with_key.post("/match", json=payload)
    match_logs = log_collector.find("match_single")
    assert len(match_logs) >= 1, f"Pas de log 'match_single'. Logs: {log_collector.records_json}"
    log = match_logs[-1]
    assert log["prospect_name"] == "Google France"
    assert "matched" in log
    assert "duration_ms" in log
    assert "score" in log
