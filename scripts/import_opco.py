#!/usr/bin/env python3
"""
Import de la table SIRET-OPCO (France Compétences) dans PostgreSQL.

Source : https://www.data.gouv.fr/fr/datasets/table-siret-opco/
Fichier : SIRO_YYYYMM.csv (~100 Mo, séparateur pipe |, ~3.5M lignes)

Colonnes du CSV source :
    SIRET | IDCC | OPCO_PROPRIETAIRE | OPCO_GESTION

Étapes :
  1. Lecture CSV, validation SIRET (14 chiffres), dédup
  2. Import dans table temporaire siret_opco_new via COPY
  3. Création des index (PK + 2 btree, sans idx_opco_siret redondant avec PK)
  4. Swap atomique via RENAME
  5. Synchronisation IDCC (affichage des manquants)

Usage:
    python scripts/import_opco.py                          # Import depuis CSV local
    python scripts/import_opco.py --download               # Télécharger puis importer
    python scripts/import_opco.py --csv /path/to/file.csv  # CSV custom
    python scripts/import_opco.py --indexes-only           # Recréer les index uniquement
    python scripts/import_opco.py --sync-idcc              # Synchroniser idcc_libelles uniquement
"""
import argparse
import csv
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("import_opco")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DB_NAME = os.getenv("DB_NAME", "sirene")
DB_USER = os.getenv("DB_USER", "sirene_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "sirene_pass")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "5433")

DATA_DIR = Path("/tmp/sirene_data")
OPCO_URL = "https://static.data.gouv.fr/resources/table-siret-opco/20260108-123831/siro-202601.csv"
CSV_FILE = DATA_DIR / "siro_opco.csv"

SIRET_RE = re.compile(r"^\d{14}$")

DB_COLUMNS = ["siret", "idcc", "opco_proprietaire", "opco_gestion"]

# Index à créer (nom, DDL)
# NOTE : pas de idx_opco_siret — redondant avec la PK (dette identifiée et supprimée)
INDEXES = [
    ("siret_opco_pkey",
     "ALTER TABLE siret_opco ADD CONSTRAINT siret_opco_pkey PRIMARY KEY (siret)"),
    ("idx_opco_idcc",
     "CREATE INDEX idx_opco_idcc ON siret_opco (idcc)"),
    ("idx_siret_opco_idcc_siret",
     "CREATE INDEX idx_siret_opco_idcc_siret ON siret_opco (idcc, siret)"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def psql_rows(query: str) -> list[str]:
    """Exécuter une requête et retourner les lignes brutes."""
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD
    result = subprocess.run(
        ["psql", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", DB_NAME,
         "-t", "-A", "-c", query],
        env=env, capture_output=True, text=True,
    )
    return [line for line in result.stdout.strip().split("\n") if line]


# ---------------------------------------------------------------------------
# Étape 1 : Téléchargement
# ---------------------------------------------------------------------------

def download():
    """Télécharger le fichier SIRO depuis data.gouv.fr."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Téléchargement depuis %s ...", OPCO_URL)
    subprocess.run(
        ["wget", "-q", "--show-progress", "-O", str(CSV_FILE), OPCO_URL],
        check=True,
    )

    size_mb = CSV_FILE.stat().st_size / (1024**2)
    log.info("Fichier prêt : %s (%.0f Mo)", CSV_FILE, size_mb)


# ---------------------------------------------------------------------------
# Étape 2 : Préparation CSV (validation + dédup)
# ---------------------------------------------------------------------------

def prepare_csv(csv_path: Path) -> tuple[Path, int]:
    """Lire le CSV source, valider les SIRET, dédupliquer, écrire un CSV propre.

    Le CSV France Compétences utilise | comme séparateur.
    Colonnes attendues : SIRET | IDCC | OPCO_PROPRIETAIRE | OPCO_GESTION

    Returns: (chemin du CSV propre, nombre de lignes)
    """
    out_path = DATA_DIR / "opco_clean.csv"
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Préparation du CSV depuis %s ...", csv_path)
    t0 = time.time()

    seen_sirets: set[str] = set()
    count = 0
    skipped_invalid = 0
    skipped_dup = 0

    with open(csv_path, "r", encoding="utf-8") as f_in, \
         open(out_path, "w", encoding="utf-8", newline="") as f_out:

        reader = csv.reader(f_in, delimiter="|")
        header = next(reader)

        # Mapper les colonnes (insensible à la casse)
        header_upper = [h.strip().upper() for h in header]
        col_map = {}
        for expected in ["SIRET", "IDCC", "OPCO_PROPRIETAIRE", "OPCO_GESTION"]:
            if expected in header_upper:
                col_map[expected] = header_upper.index(expected)
        if "SIRET" not in col_map:
            log.error("Colonne SIRET introuvable. Header: %s", header)
            sys.exit(1)
        log.info("Colonnes mappées : %s", list(col_map.keys()))

        writer = csv.writer(f_out)

        def get(row, col):
            idx = col_map.get(col)
            if idx is not None and idx < len(row):
                return row[idx].strip()
            return ""

        for row in reader:
            siret = get(row, "SIRET")

            # Validation SIRET : exactement 14 chiffres
            if not SIRET_RE.match(siret):
                skipped_invalid += 1
                continue

            # Déduplication : garder la première occurrence
            if siret in seen_sirets:
                skipped_dup += 1
                continue
            seen_sirets.add(siret)

            writer.writerow([
                siret,
                get(row, "IDCC"),
                get(row, "OPCO_PROPRIETAIRE"),
                get(row, "OPCO_GESTION"),
            ])
            count += 1

            if count % 500_000 == 0:
                elapsed = time.time() - t0
                log.info("  %s lignes (%.0f lignes/s)", f"{count:,}", count / elapsed)

    elapsed = time.time() - t0
    log.info("%s lignes retenues en %.0fs", f"{count:,}", elapsed)
    if skipped_invalid:
        log.warning("  %s SIRET invalides ignorés", f"{skipped_invalid:,}")
    if skipped_dup:
        log.warning("  %s doublons supprimés", f"{skipped_dup:,}")

    return out_path, count


# ---------------------------------------------------------------------------
# Étape 3 : Import BDD
# ---------------------------------------------------------------------------

def create_temp_table():
    """Créer la table temporaire d'import."""
    log.info("Création de la table siret_opco_new ...")
    psql("DROP TABLE IF EXISTS siret_opco_new CASCADE;")
    psql("""
    CREATE TABLE siret_opco_new (
        siret             TEXT NOT NULL,
        idcc              TEXT,
        opco_proprietaire TEXT,
        opco_gestion      TEXT
    );
    """)


def copy_to_db(csv_path: Path, table: str = "siret_opco_new"):
    """COPY le CSV nettoyé (comma-separated, sans header) dans PostgreSQL."""
    log.info("COPY %s → %s ...", csv_path.name, table)
    t0 = time.time()

    cols = ",".join(DB_COLUMNS)
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD

    # Le CSV nettoyé est comma-separated, sans header
    result = subprocess.run(
        ["psql", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", DB_NAME,
         "-c", f"\\COPY {table} ({cols}) FROM '{csv_path}' WITH (FORMAT csv, NULL '')"],
        env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.error("COPY échoué : %s", result.stderr.strip())
        sys.exit(1)

    elapsed = time.time() - t0
    count = psql_val(f"SELECT COUNT(*) FROM {table};")
    log.info("COPY terminé : %s lignes en %.0fs", f"{int(count):,}", elapsed)


def swap_tables():
    """Swap atomique : siret_opco_new → siret_opco."""
    log.info("Swap des tables ...")

    new_count = psql_val("SELECT COUNT(*) FROM siret_opco_new;")
    log.info("  siret_opco_new : %s lignes", f"{int(new_count):,}")
    if not new_count or int(new_count) == 0:
        log.error("Table siret_opco_new vide — abandon du swap")
        sys.exit(1)

    # Protection : refuser si la nouvelle table a < 50% de l'ancienne
    old_exists = psql_val("SELECT to_regclass('siret_opco');")
    if old_exists:
        old_count = psql_val("SELECT COUNT(*) FROM siret_opco;")
        if old_count and int(old_count) > 0:
            ratio = int(new_count) / int(old_count)
            if ratio < 0.5:
                log.error(
                    "Nouvelle table a %.0f%% de l'ancienne (%s vs %s) — abandon",
                    ratio * 100, new_count, old_count,
                )
                sys.exit(1)

    psql("""
    BEGIN;
    DROP TABLE IF EXISTS siret_opco_old CASCADE;
    ALTER TABLE IF EXISTS siret_opco RENAME TO siret_opco_old;
    ALTER TABLE siret_opco_new RENAME TO siret_opco;
    DROP TABLE IF EXISTS siret_opco_old CASCADE;
    COMMIT;
    """)
    log.info("Swap terminé")


# ---------------------------------------------------------------------------
# Étape 4 : Index
# ---------------------------------------------------------------------------

def create_indexes(table: str = "siret_opco"):
    """Créer les index sur la table."""
    log.info("Création des index sur %s ...", table)
    t0 = time.time()

    # Supprimer l'index redondant idx_opco_siret s'il existe (dette technique)
    psql("DROP INDEX IF EXISTS idx_opco_siret;")

    for name, ddl in INDEXES:
        t1 = time.time()
        ddl_adapted = ddl.replace(" siret_opco ", f" {table} ")
        ddl_adapted = ddl_adapted.replace(" siret_opco(", f" {table}(")

        if "PRIMARY KEY" in ddl:
            pkey_name = f"{table}_pkey"
            psql(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {pkey_name};")
            ddl_adapted = ddl_adapted.replace("siret_opco_pkey", pkey_name)
        else:
            psql(f"DROP INDEX IF EXISTS {name};")

        psql(ddl_adapted)
        log.info("  %-40s %.0fs", name, time.time() - t1)

    log.info("Index terminés en %.0fs", time.time() - t0)


# ---------------------------------------------------------------------------
# Étape 5 : Stats et vérification
# ---------------------------------------------------------------------------

def verify():
    """Afficher les stats finales avec répartition par OPCO."""
    total = psql_val("SELECT COUNT(*) FROM siret_opco;")
    with_opco = psql_val(
        "SELECT COUNT(*) FROM siret_opco "
        "WHERE opco_proprietaire IS NOT NULL AND opco_proprietaire != '';"
    )
    sans_opco = psql_val(
        "SELECT COUNT(*) FROM siret_opco "
        "WHERE opco_proprietaire IS NULL OR opco_proprietaire = '';"
    )
    n_idcc = psql_val(
        "SELECT COUNT(DISTINCT idcc) FROM siret_opco "
        "WHERE idcc IS NOT NULL AND idcc != '';"
    )
    size = psql_val("SELECT pg_size_pretty(pg_total_relation_size('siret_opco'));")
    n_idx = psql_val(
        "SELECT COUNT(*) FROM pg_indexes WHERE tablename = 'siret_opco';"
    )

    log.info("--- Stats finales ---")
    log.info("  Total        : %s lignes", f"{int(total):,}" if total else "?")
    log.info("  Avec OPCO    : %s", f"{int(with_opco):,}" if with_opco else "?")
    log.info("  Sans OPCO    : %s", f"{int(sans_opco):,}" if sans_opco else "?")
    log.info("  IDCC distincts : %s", n_idcc)
    log.info("  Taille       : %s", size)
    log.info("  Index        : %s", n_idx)

    # Répartition par OPCO
    log.info("")
    log.info("--- Répartition par OPCO ---")
    rows = psql_rows(
        "SELECT COALESCE(NULLIF(opco_proprietaire, ''), '(vide)') AS opco, "
        "COUNT(*) AS n "
        "FROM siret_opco GROUP BY opco ORDER BY n DESC;"
    )
    for row in rows:
        parts = row.split("|")
        if len(parts) == 2:
            opco, n = parts[0], parts[1]
            log.info("  %-35s %s", opco, f"{int(n):>10,}")


# ---------------------------------------------------------------------------
# Étape 6 : Synchronisation IDCC
# ---------------------------------------------------------------------------

def sync_idcc():
    """Comparer les IDCC de siret_opco avec idcc_libelles et le JSON de référence.

    Affiche les IDCC manquants et propose les INSERT (sans exécuter).
    """
    log.info("")
    log.info("=" * 60)
    log.info("Synchronisation IDCC")
    log.info("=" * 60)

    # IDCC dans siret_opco (BDD)
    idcc_in_opco = set(psql_rows(
        "SELECT DISTINCT idcc FROM siret_opco "
        "WHERE idcc IS NOT NULL AND idcc != '' ORDER BY idcc;"
    ))
    log.info("IDCC distincts dans siret_opco : %d", len(idcc_in_opco))

    # IDCC dans idcc_libelles (BDD)
    idcc_in_libelles = set(psql_rows(
        "SELECT idcc FROM idcc_libelles ORDER BY idcc;"
    ))
    log.info("IDCC dans idcc_libelles        : %d", len(idcc_in_libelles))

    # IDCC dans le JSON de référence
    json_path = PROJECT_ROOT / "config" / "data" / "idcc_to_convention.json"
    idcc_in_json: dict[str, str] = {}
    if json_path.exists():
        with open(json_path, encoding="utf-8") as f:
            idcc_in_json = json.load(f)
        log.info("IDCC dans idcc_to_convention.json : %d", len(idcc_in_json))
    else:
        log.warning("Fichier %s introuvable", json_path)

    # --- Manquants dans idcc_libelles ---
    missing_in_table = sorted(idcc_in_opco - idcc_in_libelles)
    if missing_in_table:
        log.info("")
        log.info("--- IDCC dans siret_opco mais ABSENTS de idcc_libelles (%d) ---",
                 len(missing_in_table))

        # Compter combien de SIRET sont affectés par IDCC manquant
        counts_query = (
            "SELECT idcc, COUNT(*) FROM siret_opco "
            "WHERE idcc IN ('" + "','".join(missing_in_table) + "') "
            "GROUP BY idcc ORDER BY idcc;"
        )
        count_rows = psql_rows(counts_query)
        idcc_counts = {}
        for row in count_rows:
            parts = row.split("|")
            if len(parts) == 2:
                idcc_counts[parts[0]] = int(parts[1])

        inserts = []
        for idcc in missing_in_table:
            n_sirets = idcc_counts.get(idcc, 0)
            libelle = idcc_in_json.get(idcc, "")
            source = "JSON" if libelle else ""
            if libelle:
                log.info("  %s : %s SIRET — %s", idcc, f"{n_sirets:,}", libelle[:70])
            else:
                log.info("  %s : %s SIRET — (libellé inconnu)", idcc, f"{n_sirets:,}")

            if libelle:
                safe_libelle = libelle.replace("'", "''")
                inserts.append(
                    f"INSERT INTO idcc_libelles (idcc, libelle) "
                    f"VALUES ('{idcc}', '{safe_libelle}');"
                )

        if inserts:
            log.info("")
            log.info("--- INSERT proposés (non exécutés) ---")
            for stmt in inserts:
                log.info("  %s", stmt[:120] + ("..." if len(stmt) > 120 else ""))
    else:
        log.info("Tous les IDCC de siret_opco sont présents dans idcc_libelles.")

    # --- Manquants dans le JSON ---
    missing_in_json = sorted(idcc_in_opco - set(idcc_in_json.keys()))
    if missing_in_json:
        already_in_table = [i for i in missing_in_json if i in idcc_in_libelles]
        nowhere = [i for i in missing_in_json if i not in idcc_in_libelles]
        if nowhere:
            log.info("")
            log.info("--- IDCC sans libellé connu (ni JSON, ni table) : %d ---", len(nowhere))
            for idcc in nowhere[:20]:
                n = idcc_counts.get(idcc, 0) if 'idcc_counts' in dir() else "?"
                log.info("  %s (%s SIRET)", idcc, n)
            if len(nowhere) > 20:
                log.info("  ... et %d de plus", len(nowhere) - 20)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Import table SIRET-OPCO (France Compétences)")
    parser.add_argument("--download", action="store_true",
                        help="Télécharger le fichier depuis data.gouv.fr avant import")
    parser.add_argument("--csv", type=str, default=None,
                        help="Chemin vers un CSV custom (défaut: /tmp/sirene_data/siro_opco.csv)")
    parser.add_argument("--indexes-only", action="store_true",
                        help="Recréer uniquement les index")
    parser.add_argument("--sync-idcc", action="store_true",
                        help="Synchroniser idcc_libelles uniquement (pas d'import)")
    args = parser.parse_args()

    t_global = time.time()

    log.info("=" * 60)
    log.info("SIRET Matcher — Import Table SIRET-OPCO")
    log.info("=" * 60)

    # -- Mode sync-idcc seul --
    if args.sync_idcc:
        sync_idcc()
        log.info("Terminé en %.0fs", time.time() - t_global)
        return

    # -- Mode indexes-only --
    if args.indexes_only:
        create_indexes()
        verify()
        log.info("Terminé en %.0fs", time.time() - t_global)
        return

    # -- Téléchargement --
    if args.download:
        download()

    # -- Résolution du CSV --
    csv_path = Path(args.csv) if args.csv else CSV_FILE
    if not csv_path.exists():
        log.error("CSV introuvable : %s", csv_path)
        log.error("Lancez avec --download pour télécharger, ou --csv pour spécifier un fichier")
        sys.exit(1)
    size_mb = csv_path.stat().st_size / (1024**2)
    log.info("CSV source : %s (%.1f Mo)", csv_path, size_mb)

    # -- Test connexion BDD --
    test = psql_val("SELECT 1;")
    if test != "1":
        log.error("Impossible de se connecter à la BDD (host=%s port=%s db=%s)",
                   DB_HOST, DB_PORT, DB_NAME)
        sys.exit(1)
    log.info("Connexion BDD OK")

    # -- Préparation CSV (validation + dédup) --
    clean_csv, count = prepare_csv(csv_path)

    # -- Import --
    create_temp_table()
    copy_to_db(clean_csv, "siret_opco_new")

    # Nettoyage fichier intermédiaire
    clean_csv.unlink(missing_ok=True)

    # -- Index --
    create_indexes("siret_opco_new")

    # -- Swap --
    swap_tables()

    # -- Stats --
    verify()

    # -- Sync IDCC --
    sync_idcc()

    # -- Invalidation du cache Redis --
    try:
        import asyncio
        from siret_matcher.cache import connect, invalidate_all, close

        async def _invalidate():
            await connect()
            await invalidate_all()
            await close()

        asyncio.run(_invalidate())
        log.info("Cache Redis invalidé après import")
    except Exception as e:
        log.warning(f"Invalidation cache Redis échouée (non critique) : {e}")

    elapsed = time.time() - t_global
    log.info("")
    log.info("=" * 60)
    log.info("Import terminé en %.0fs (%.1f min)", elapsed, elapsed / 60)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
