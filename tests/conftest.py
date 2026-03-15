"""Fixtures partagées pour la suite de tests SIRET Matcher."""
import json
import logging
import os
import sys

import asyncpg
import httpx
import pytest
from dotenv import load_dotenv

from siret_matcher.logging_config import JSONFormatter

# Le projet utilise des chemins absolus — on s'assure que le package est importable
sys.path.insert(0, "/opt/siret-matcher")
os.chdir("/opt/siret-matcher")

# Charger la config exactement comme le fait le code principal
load_dotenv("config/.env")

# Clé de test pour l'authentification API
_DEV_API_KEY = None


def _get_dev_api_key() -> str:
    """Récupère la clé dev-testing depuis api_keys.json, avec fallback."""
    global _DEV_API_KEY
    if _DEV_API_KEY:
        return _DEV_API_KEY
    keys_path = os.path.join(os.path.dirname(__file__), "..", "config", "api_keys.json")
    try:
        with open(keys_path) as f:
            data = json.load(f)
        for entry in data.get("keys", []):
            if entry.get("name") == "dev-testing" and entry.get("active"):
                _DEV_API_KEY = entry["key"]
                return _DEV_API_KEY
    except FileNotFoundError:
        pass
    # Fallback : injecter une clé de test dans le module auth
    _DEV_API_KEY = "test-fallback-key-for-pytest"
    from siret_matcher.auth import _keys
    _keys[_DEV_API_KEY] = {"name": "pytest-fallback", "active": True}
    return _DEV_API_KEY


# ── Fixture DB ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
async def db_pool():
    """Pool asyncpg vers la base Sirene locale (session-scoped).

    Lit la configuration depuis config/.env, identique à siret_matcher.db.
    """
    pool = await asyncpg.create_pool(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "sirene"),
        user=os.getenv("DB_USER", "sirene_user"),
        password=os.getenv("DB_PASSWORD", "sirene_pass"),
        min_size=1,
        max_size=3,
    )
    yield pool
    await pool.close()


# ── Fixture API client ────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
async def api_client():
    """Client HTTP pointant vers l'app FastAPI via ASGITransport.

    Permet de tester les endpoints sans lancer de serveur.
    Déclenche manuellement les events startup/shutdown de l'app.
    """
    from api import app

    # Déclencher le startup (connecte la DB, crée le http_client interne)
    for handler in app.router.on_startup:
        await handler()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    # Déclencher le shutdown (ferme les connexions)
    for handler in app.router.on_shutdown:
        await handler()


# ── Fixture API client authentifié ────────────────────────────────────────────


class AuthedClient:
    """Wrapper autour d'un httpx.AsyncClient qui ajoute X-API-Key à chaque requête."""

    def __init__(self, client: httpx.AsyncClient, api_key: str):
        self._client = client
        self._api_key = api_key

    def _merge_headers(self, kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["X-API-Key"] = self._api_key
        kwargs["headers"] = headers
        return kwargs

    async def get(self, url, **kwargs):
        return await self._client.get(url, **self._merge_headers(kwargs))

    async def post(self, url, **kwargs):
        return await self._client.post(url, **self._merge_headers(kwargs))

    async def put(self, url, **kwargs):
        return await self._client.put(url, **self._merge_headers(kwargs))

    async def delete(self, url, **kwargs):
        return await self._client.delete(url, **self._merge_headers(kwargs))


@pytest.fixture(scope="session")
def api_client_with_key(api_client):
    """Client API avec API key dev-testing ajoutée à chaque requête."""
    key = _get_dev_api_key()
    return AuthedClient(api_client, key)


# ── Fixture log collector ─────────────────────────────────────────────────────


class JSONCollector(logging.Handler):
    """Handler qui collecte les lignes JSON formatées en mémoire."""

    def __init__(self):
        super().__init__()
        self.setFormatter(JSONFormatter())
        self.records_json: list[dict] = []

    def emit(self, record):
        line = self.format(record)
        try:
            self.records_json.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    def clear(self):
        self.records_json.clear()

    def find(self, message: str) -> list[dict]:
        return [r for r in self.records_json if r.get("message") == message]


@pytest.fixture()
def log_collector():
    """Injecte un collecteur JSON sur les loggers de l'application."""
    collector = JSONCollector()
    collector.setLevel(logging.DEBUG)
    loggers = [
        logging.getLogger("api"),
        logging.getLogger("siret_matcher"),
        logging.getLogger("siret_matcher.matcher"),
        logging.getLogger("siret_matcher.search"),
    ]
    for lg in loggers:
        lg.addHandler(collector)
        if lg.level > logging.DEBUG:
            lg._original_level = lg.level
            lg.setLevel(logging.DEBUG)

    yield collector

    for lg in loggers:
        lg.removeHandler(collector)
        if hasattr(lg, "_original_level"):
            lg.setLevel(lg._original_level)
            del lg._original_level
