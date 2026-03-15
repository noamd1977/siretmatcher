#!/usr/bin/env python3
"""
Migration : ajout du full-text search PostgreSQL sur la table etablissements.

Ajoute :
  1. Colonne search_vector (tsvector) combinant denomination + enseigne + commune
  2. Index GIN sur search_vector
  3. Trigger pour maintenir le vecteur à jour sur INSERT/UPDATE

Usage:
    python scripts/migrate_fulltext.py
    python scripts/migrate_fulltext.py --check   # Vérifie si la migration est nécessaire
"""
import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("migrate_fulltext")

DB_NAME = os.getenv("DB_NAME", "sirene")
DB_USER = os.getenv("DB_USER", "sirene_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "sirene_pass")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "5433")


def psql(cmd: str) -> subprocess.CompletedProcess:
    """Exécuter une commande psql."""
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD
    result = subprocess.run(
        ["psql", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", DB_NAME, "-c", cmd],
        env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.error("psql: %s", result.stderr.strip())
    return result


def psql_val(query: str) -> str:
    """Exécuter une requête et retourner la valeur scalaire."""
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD
    result = subprocess.run(
        ["psql", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", DB_NAME,
         "-t", "-A", "-c", query],
        env=env, capture_output=True, text=True,
    )
    return result.stdout.strip()


def check_column_exists() -> bool:
    """Vérifie si la colonne search_vector existe déjà."""
    val = psql_val(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_name = 'etablissements' AND column_name = 'search_vector';"
    )
    return val == "1"


def check_index_exists() -> bool:
    """Vérifie si l'index GIN existe."""
    val = psql_val(
        "SELECT COUNT(*) FROM pg_indexes "
        "WHERE tablename = 'etablissements' AND indexname = 'idx_search_vector';"
    )
    return val == "1"


def check_trigger_exists() -> bool:
    """Vérifie si le trigger existe."""
    val = psql_val(
        "SELECT COUNT(*) FROM information_schema.triggers "
        "WHERE trigger_name = 'trg_search_vector' AND event_object_table = 'etablissements';"
    )
    return val == "1"


def migrate():
    """Exécute la migration full-text search."""
    t_global = time.time()

    log.info("=" * 60)
    log.info("Migration Full-Text Search")
    log.info("=" * 60)

    # Test connexion
    test = psql_val("SELECT 1;")
    if test != "1":
        log.error("Impossible de se connecter à la BDD")
        sys.exit(1)
    log.info("Connexion BDD OK")

    # Étape 1 : Ajouter la colonne search_vector
    if check_column_exists():
        log.info("Colonne search_vector existe déjà — skip ALTER TABLE")
    else:
        log.info("Ajout de la colonne search_vector ...")
        t0 = time.time()
        psql("ALTER TABLE etablissements ADD COLUMN search_vector tsvector;")
        log.info("  Colonne ajoutée en %.1fs", time.time() - t0)

    # Étape 2 : Peupler la colonne (le plus long — ~16.7M lignes)
    # Vérifier si déjà peuplé
    populated = psql_val(
        "SELECT COUNT(*) FROM etablissements "
        "WHERE search_vector IS NOT NULL LIMIT 1;"
    )
    if populated and int(populated) > 0:
        log.info("search_vector déjà peuplé (%s lignes non-null) — skip UPDATE", populated)
    else:
        log.info("Peuplement de search_vector (UPDATE sur toutes les lignes) ...")
        log.info("  Cela peut prendre plusieurs minutes sur 16.7M lignes ...")
        t0 = time.time()

        # UPDATE par batch pour éviter de bloquer la table trop longtemps
        # et pour avoir un suivi de progression
        total = psql_val("SELECT COUNT(*) FROM etablissements;")
        log.info("  %s lignes à mettre à jour", total)

        psql("""
        UPDATE etablissements SET search_vector =
            setweight(to_tsvector('french', COALESCE(denomination, '')), 'A') ||
            setweight(to_tsvector('french', COALESCE(enseigne, '')), 'B') ||
            setweight(to_tsvector('french', COALESCE(commune, '')), 'C');
        """)

        elapsed = time.time() - t0
        log.info("  UPDATE terminé en %.0fs (%.1f min)", elapsed, elapsed / 60)

    # Étape 3 : Créer l'index GIN
    if check_index_exists():
        log.info("Index idx_search_vector existe déjà — skip")
    else:
        log.info("Création de l'index GIN sur search_vector ...")
        t0 = time.time()
        psql("CREATE INDEX idx_search_vector ON etablissements USING GIN(search_vector);")
        log.info("  Index créé en %.0fs", time.time() - t0)

    # Étape 4 : Créer la fonction et le trigger
    if check_trigger_exists():
        log.info("Trigger trg_search_vector existe déjà — skip")
    else:
        log.info("Création de la fonction et du trigger ...")
        psql("""
        CREATE OR REPLACE FUNCTION update_search_vector() RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('french', COALESCE(NEW.denomination, '')), 'A') ||
                setweight(to_tsvector('french', COALESCE(NEW.enseigne, '')), 'B') ||
                setweight(to_tsvector('french', COALESCE(NEW.commune, '')), 'C');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """)
        psql("""
        CREATE TRIGGER trg_search_vector
            BEFORE INSERT OR UPDATE ON etablissements
            FOR EACH ROW EXECUTE FUNCTION update_search_vector();
        """)
        log.info("  Trigger créé")

    # Vérification
    log.info("--- Vérification ---")
    count_vec = psql_val(
        "SELECT COUNT(*) FROM etablissements WHERE search_vector IS NOT NULL;"
    )
    count_total = psql_val("SELECT COUNT(*) FROM etablissements;")
    log.info("  search_vector peuplé : %s / %s lignes", count_vec, count_total)

    # Test rapide du full-text
    test_result = psql_val(
        "SELECT denomination FROM etablissements "
        "WHERE search_vector @@ plainto_tsquery('french', 'google') "
        "LIMIT 1;"
    )
    if test_result:
        log.info("  Test full-text 'google' → %s", test_result[:60])
    else:
        log.warning("  Test full-text 'google' → aucun résultat (vérifier les données)")

    elapsed = time.time() - t_global
    log.info("=" * 60)
    log.info("Migration terminée en %.0fs (%.1f min)", elapsed, elapsed / 60)
    log.info("=" * 60)


def check():
    """Vérifie l'état de la migration."""
    col = check_column_exists()
    idx = check_index_exists()
    trg = check_trigger_exists()
    log.info("Colonne search_vector : %s", "OK" if col else "MANQUANTE")
    log.info("Index idx_search_vector : %s", "OK" if idx else "MANQUANT")
    log.info("Trigger trg_search_vector : %s", "OK" if trg else "MANQUANT")
    if col and idx and trg:
        log.info("Migration complète — rien à faire")
        return True
    else:
        log.info("Migration nécessaire — lancez: python scripts/migrate_fulltext.py")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migration full-text search")
    parser.add_argument("--check", action="store_true",
                        help="Vérifier si la migration est nécessaire (sans rien modifier)")
    args = parser.parse_args()

    if args.check:
        check()
    else:
        migrate()
