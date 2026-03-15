"""Fixtures partagées pour la suite de tests SIRET Matcher."""
import os
import sys

import asyncpg
import httpx
import pytest
from dotenv import load_dotenv

# Le projet utilise des chemins absolus — on s'assure que le package est importable
sys.path.insert(0, "/opt/siret-matcher")
os.chdir("/opt/siret-matcher")

# Charger la config exactement comme le fait le code principal
load_dotenv("config/.env")


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
