"""Connexion PostgreSQL et requêtes Sirene."""
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv("config/.env")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "sirene"),
    "user": os.getenv("DB_USER", "sirene_user"),
    "password": os.getenv("DB_PASSWORD", "sirene_pass"),
}


class SireneDB:
    """Pool de connexions PostgreSQL pour la base Sirene."""

    def __init__(self):
        self.pool: asyncpg.Pool | None = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(**DB_CONFIG, min_size=2, max_size=10)

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def search_by_address(
        self, numero: str, voie_clean: str, code_postal: str, limit: int = 10
    ) -> list[dict]:
        """Chercher des établissements par adresse physique.
        
        C'est LE game-changer : quand "Point S" est au "32 Av Noël Franchini",
        on cherche tous les établissements actifs à cette adresse → souvent 1 seul.
        """
        query = """
        SELECT siret, siren, denomination, enseigne, naf,
               numero_voie, type_voie, voie, code_postal, commune,
               tranche_effectif, date_creation, etat_administratif,
               similarity(voie_clean, $3) AS voie_sim
        FROM etablissements
        WHERE etat_administratif = 'A'
          AND code_postal = $1
          AND numero_voie = $2
          AND voie_clean % $3
        ORDER BY voie_sim DESC
        LIMIT $4
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, code_postal, numero, voie_clean, limit)
        return [dict(r) for r in rows]

    async def search_by_address_no_numero(
        self, voie_clean: str, code_postal: str, limit: int = 5
    ) -> list[dict]:
        """Recherche par voie sans numéro (plus large)."""
        query = """
        SELECT siret, siren, denomination, enseigne, naf,
               numero_voie, type_voie, voie, code_postal, commune,
               tranche_effectif, date_creation, etat_administratif,
               similarity(voie_clean, $2) AS voie_sim
        FROM etablissements
        WHERE etat_administratif = 'A'
          AND code_postal = $1
          AND voie_clean % $2
        ORDER BY voie_sim DESC
        LIMIT $3
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, code_postal, voie_clean, limit)
        return [dict(r) for r in rows]

    async def search_trigram_name(
        self, nom_clean: str, code_postal: str, departement: str = "",
        limit: int = 10
    ) -> list[dict]:
        """Recherche fuzzy par trigrams sur dénomination ET enseigne.
        
        pg_trgm permet de matcher "CORS AUTO" → "CORSE AUTOMOBILE"
        avec un score de similarité, même sans mot exact en commun.
        """
        # Chercher d'abord par CP, puis par département si pas de résultat
        query_cp = """
        SELECT siret, siren, denomination, enseigne, naf,
               numero_voie, type_voie, voie, code_postal, commune,
               tranche_effectif, date_creation, etat_administratif,
               GREATEST(
                   similarity(denomination_clean, $1),
                   similarity(enseigne_clean, $1)
               ) AS best_sim
        FROM etablissements
        WHERE etat_administratif = 'A'
          AND code_postal = $2
          AND (denomination_clean % $1 OR enseigne_clean % $1)
        ORDER BY best_sim DESC
        LIMIT $3
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query_cp, nom_clean, code_postal, limit)
            if rows:
                return [dict(r) for r in rows]

            # Fallback département
            if departement:
                query_dept = """
                SELECT siret, siren, denomination, enseigne, naf,
                       numero_voie, type_voie, voie, code_postal, commune,
                       tranche_effectif, date_creation, etat_administratif,
                       GREATEST(
                           similarity(denomination_clean, $1),
                           similarity(enseigne_clean, $1)
                       ) AS best_sim
                FROM etablissements
                WHERE etat_administratif = 'A'
                  AND departement = $2
                  AND (denomination_clean % $1 OR enseigne_clean % $1)
                ORDER BY best_sim DESC
                LIMIT $3
                """
                rows = await conn.fetch(query_dept, nom_clean, departement, limit)
                return [dict(r) for r in rows]
        return []

    async def search_trigram_all_variants(
        self, variants: list[str], code_postal: str, departement: str = "",
        limit: int = 10
    ) -> list[dict]:
        """Tester toutes les variantes de nom et retourner le meilleur résultat."""
        all_results = []
        seen_siret = set()
        for variant in variants[:5]:  # Max 5 variantes
            rows = await self.search_trigram_name(variant, code_postal, departement, limit=5)
            for r in rows:
                if r["siret"] not in seen_siret:
                    seen_siret.add(r["siret"])
                    r["_query_variant"] = variant
                    all_results.append(r)
        # Trier par meilleur score global
        all_results.sort(key=lambda r: r.get("best_sim", 0), reverse=True)
        return all_results[:limit]

    async def validate_siret(self, siret: str) -> dict | None:
        """Valider un SIRET trouvé par scraping."""
        query = """
        SELECT siret, siren, denomination, enseigne, naf,
               numero_voie, type_voie, voie, code_postal, commune,
               tranche_effectif, date_creation, etat_administratif
        FROM etablissements
        WHERE siret = $1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, siret)
        return dict(row) if row else None

    async def get_stats(self) -> dict:
        """Stats de la base."""
        async with self.pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM etablissements")
            active = await conn.fetchval(
                "SELECT COUNT(*) FROM etablissements WHERE etat_administratif = 'A'"
            )
        return {"total": total, "active": active}

    async def get_opco(self, siret: str) -> dict:
        """Chercher l'OPCO officiel via la table France Compétences."""
        if not self.pool:
            return {}
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT opco_proprietaire, opco_gestion, idcc FROM siret_opco WHERE siret = $1",
                    siret
                )
            if row:
                return {
                    "opco": row["opco_proprietaire"] or row["opco_gestion"] or "",
                    "idcc": row["idcc"] or "",
                    "source_opco": "FRANCE_COMPETENCES"
                }
        except Exception:
            pass
        return {}
