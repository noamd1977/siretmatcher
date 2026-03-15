#!/usr/bin/env python3
"""
Import du stock Sirene dans PostgreSQL.

Télécharge le fichier StockEtablissement depuis data.gouv.fr (~2 Go compressé),
l'importe dans PostgreSQL via COPY et crée les index trigrams.

Stratégie zero-downtime :
  1. Import dans table temporaire etablissements_new
  2. Swap atomique : RENAME old → _old, RENAME new → etablissements
  3. DROP _old

Usage:
    python scripts/import_sirene.py                       # Import depuis CSV local
    python scripts/import_sirene.py --download            # Télécharger puis importer
    python scripts/import_sirene.py --incremental         # (pas encore implémenté)
    python scripts/import_sirene.py --indexes-only        # Recréer les index uniquement
    python scripts/import_sirene.py --csv /path/to/file   # CSV custom
"""
import argparse
import csv
import gzip
import io
import logging
import os
import sys
import subprocess
import time
from pathlib import Path

# Ajouter le projet au path pour accéder au normalizer
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from siret_matcher.normalizer import normalize_base, clean_voie

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("import_sirene")

DB_NAME = os.getenv("DB_NAME", "sirene")
DB_USER = os.getenv("DB_USER", "sirene_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "sirene_pass")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "5433")

DATA_DIR = Path("/tmp/sirene_data")
STOCK_URL = "https://files.data.gouv.fr/insee-sirene/StockEtablissement_utf8.csv.gz"
CSV_FILE = DATA_DIR / "StockEtablissement_utf8.csv"

CHUNK_SIZE = 100_000  # Lignes par chunk pour le fichier intermédiaire

# Mapping colonnes INSEE → colonnes BDD
COLUMN_MAP = {
    "siret": "siret",
    "siren": "siren",
    "denominationUniteLegale": "denomination",
    "denominationUsuelle1UniteLegale": "denomination_usuelle",
    "enseigne1Etablissement": "enseigne",
    "enseigne2Etablissement": "enseigne2",
    "activitePrincipaleEtablissement": "naf",
    "numeroVoieEtablissement": "numero_voie",
    "typeVoieEtablissement": "type_voie",
    "libelleVoieEtablissement": "voie",
    "codePostalEtablissement": "code_postal",
    "libelleCommuneEtablissement": "commune",
    "trancheEffectifsEtablissement": "tranche_effectif",
    "dateCreationEtablissement": "date_creation",
    "etatAdministratifEtablissement": "etat_administratif",
}

# Colonnes BDD dans l'ordre d'insertion (COPY)
DB_COLUMNS = [
    "siret", "siren", "denomination", "denomination_usuelle",
    "enseigne", "enseigne2", "naf", "numero_voie", "type_voie", "voie",
    "code_postal", "commune", "tranche_effectif", "date_creation",
    "etat_administratif", "departement",
    "denomination_clean", "enseigne_clean", "voie_clean",
]

# Index à créer (nom, DDL) — dans l'ordre de priorité
INDEXES = [
    # PK
    ("etablissements_pkey",
     "ALTER TABLE etablissements ADD CONSTRAINT etablissements_pkey PRIMARY KEY (siret)"),
    # B-tree simples
    ("idx_siren",
     "CREATE INDEX idx_siren ON etablissements (siren)"),
    ("idx_cp",
     "CREATE INDEX idx_cp ON etablissements (code_postal)"),
    ("idx_dept",
     "CREATE INDEX idx_dept ON etablissements (departement)"),
    # B-tree composites
    ("idx_cp_num",
     "CREATE INDEX idx_cp_num ON etablissements (code_postal, numero_voie)"),
    # Composites pour /search
    ("idx_etab_dept_etat_effectif",
     "CREATE INDEX idx_etab_dept_etat_effectif ON etablissements (departement, etat_administratif, tranche_effectif)"),
    ("idx_etab_dept_etat_naf",
     "CREATE INDEX idx_etab_dept_etat_naf ON etablissements (departement, etat_administratif, naf)"),
    # Partiel pour les actifs
    ("idx_etab_actif_siret_dept_eff",
     "CREATE INDEX idx_etab_actif_siret_dept_eff ON etablissements (siret, departement, tranche_effectif) WHERE (etat_administratif = 'A')"),
    # GIN trigram — les plus longs à créer
    ("idx_denom_trgm",
     "CREATE INDEX idx_denom_trgm ON etablissements USING GIN (denomination_clean gin_trgm_ops)"),
    ("idx_enseigne_trgm",
     "CREATE INDEX idx_enseigne_trgm ON etablissements USING GIN (enseigne_clean gin_trgm_ops)"),
    ("idx_voie_trgm",
     "CREATE INDEX idx_voie_trgm ON etablissements USING GIN (voie_clean gin_trgm_ops)"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def psql(cmd: str, db: str = DB_NAME) -> subprocess.CompletedProcess:
    """Exécuter une commande psql."""
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD
    result = subprocess.run(
        ["psql", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", db, "-c", cmd],
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


def compute_departement(code_postal: str) -> str:
    """Extraire le département du code postal.

    Même logique que models.py Prospect.__post_init__ :
    - 97xxx → 3 chiffres (DOM-TOM)
    - 200xx / 201xx → 2A si int(cp) <= 20190, sinon 2B
    - 20xxx (autres) → 2A (fallback Corse)
    - sinon → 2 premiers chiffres
    """
    if not code_postal:
        return ""
    cp = code_postal.strip()
    if cp.startswith("97") and len(cp) >= 3:
        return cp[:3]
    if cp.upper().startswith("2A") or cp.upper().startswith("2B"):
        return cp[:2].upper()
    if cp.startswith("200") or cp.startswith("201"):
        try:
            return "2A" if int(cp) <= 20190 else "2B"
        except ValueError:
            return "2A"
    if cp.startswith("20"):
        return "2A"
    if len(cp) >= 2:
        return cp[:2]
    return ""


def compute_denomination_clean(denomination: str, denomination_usuelle: str) -> str:
    """Nettoyer denomination + denomination_usuelle pour le matching trigram."""
    parts = []
    if denomination:
        parts.append(denomination)
    if denomination_usuelle:
        parts.append(denomination_usuelle)
    combined = " ".join(parts)
    if not combined.strip():
        return ""
    return normalize_base(combined).strip()


def compute_enseigne_clean(enseigne: str, enseigne2: str) -> str:
    """Nettoyer enseigne + enseigne2 pour le matching trigram."""
    parts = []
    if enseigne:
        parts.append(enseigne)
    if enseigne2:
        parts.append(enseigne2)
    combined = " ".join(parts)
    if not combined.strip():
        return ""
    return normalize_base(combined).strip()


def compute_voie_clean(voie: str) -> str:
    """Nettoyer la voie pour le matching trigram."""
    if not voie or not voie.strip():
        return ""
    return clean_voie(voie)


# ---------------------------------------------------------------------------
# Étape 1 : Téléchargement
# ---------------------------------------------------------------------------

def download():
    """Télécharger et décompresser le stock Sirene."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    gz_file = DATA_DIR / "StockEtablissement_utf8.csv.gz"

    log.info("Téléchargement depuis %s ...", STOCK_URL)
    log.info("(~2 Go compressé, 5-15 min selon connexion)")
    subprocess.run(
        ["wget", "-q", "--show-progress", "-O", str(gz_file), STOCK_URL],
        check=True,
    )

    log.info("Décompression .gz ...")
    # Décompresser en streaming pour limiter la RAM
    with gzip.open(gz_file, "rb") as f_in, open(CSV_FILE, "wb") as f_out:
        while True:
            chunk = f_in.read(8 * 1024 * 1024)  # 8 Mo
            if not chunk:
                break
            f_out.write(chunk)
    gz_file.unlink()

    size_gb = CSV_FILE.stat().st_size / (1024**3)
    log.info("Fichier prêt : %s (%.1f Go)", CSV_FILE, size_gb)


# ---------------------------------------------------------------------------
# Étape 2 : Import
# ---------------------------------------------------------------------------

def create_temp_table():
    """Créer la table temporaire d'import."""
    log.info("Création de la table etablissements_new ...")
    psql("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    psql("CREATE EXTENSION IF NOT EXISTS unaccent;")
    psql("DROP TABLE IF EXISTS etablissements_new CASCADE;")
    psql("""
    CREATE TABLE etablissements_new (
        siret       TEXT NOT NULL,
        siren       TEXT NOT NULL,
        denomination        TEXT,
        denomination_usuelle TEXT,
        enseigne    TEXT,
        enseigne2   TEXT,
        naf         TEXT,
        numero_voie TEXT,
        type_voie   TEXT,
        voie        TEXT,
        code_postal TEXT,
        commune     TEXT,
        tranche_effectif TEXT,
        date_creation    TEXT,
        etat_administratif TEXT,
        departement TEXT,
        denomination_clean TEXT,
        enseigne_clean     TEXT,
        voie_clean         TEXT
    );
    """)


def prepare_csv(csv_path: Path) -> tuple[Path, int]:
    """Lire le CSV INSEE, calculer les colonnes dérivées, écrire un CSV propre.

    Returns: (chemin du CSV propre, nombre de lignes)
    """
    out_path = DATA_DIR / "import_ready.csv"
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Préparation du CSV depuis %s ...", csv_path)
    t0 = time.time()
    count = 0

    with open(csv_path, "r", encoding="utf-8") as f_in, \
         open(out_path, "w", encoding="utf-8", newline="") as f_out:

        reader = csv.reader(f_in)
        header = next(reader)

        # Index des colonnes utiles
        col_idx = {}
        for insee_col in COLUMN_MAP:
            if insee_col in header:
                col_idx[insee_col] = header.index(insee_col)
        if not col_idx:
            log.error("Colonnes introuvables. Header: %s", header[:10])
            sys.exit(1)
        log.info("Colonnes mappées : %d/%d", len(col_idx), len(COLUMN_MAP))

        writer = csv.writer(f_out)

        def get(row, col):
            idx = col_idx.get(col)
            if idx is not None and idx < len(row):
                return row[idx].strip()
            return ""

        for row in reader:
            siret = get(row, "siret")
            if not siret or len(siret) != 14:
                continue

            denom = get(row, "denominationUniteLegale")
            denom_usuelle = get(row, "denominationUsuelle1UniteLegale")
            enseigne = get(row, "enseigne1Etablissement")
            enseigne2 = get(row, "enseigne2Etablissement")
            voie_raw = get(row, "libelleVoieEtablissement")
            cp = get(row, "codePostalEtablissement")

            writer.writerow([
                siret,
                get(row, "siren"),
                denom,
                denom_usuelle,
                enseigne,
                enseigne2,
                get(row, "activitePrincipaleEtablissement"),
                get(row, "numeroVoieEtablissement"),
                get(row, "typeVoieEtablissement"),
                voie_raw,
                cp,
                get(row, "libelleCommuneEtablissement"),
                get(row, "trancheEffectifsEtablissement"),
                get(row, "dateCreationEtablissement"),
                get(row, "etatAdministratifEtablissement"),
                compute_departement(cp),
                compute_denomination_clean(denom, denom_usuelle),
                compute_enseigne_clean(enseigne, enseigne2),
                compute_voie_clean(voie_raw),
            ])
            count += 1
            if count % 500_000 == 0:
                elapsed = time.time() - t0
                rate = count / elapsed
                log.info("  %10s lignes (%.0f lignes/s)", f"{count:,}", rate)

    elapsed = time.time() - t0
    log.info("%s lignes préparées en %.0fs (%.0f lignes/s)",
             f"{count:,}", elapsed, count / elapsed if elapsed else 0)
    return out_path, count


def copy_to_db(csv_path: Path, table: str = "etablissements_new"):
    """COPY le CSV propre dans PostgreSQL."""
    log.info("COPY %s → %s ...", csv_path.name, table)
    t0 = time.time()

    cols = ",".join(DB_COLUMNS)
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD
    result = subprocess.run(
        ["psql", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", DB_NAME,
         "-c", f"\\COPY {table} ({cols}) FROM '{csv_path}' WITH (FORMAT csv, NULL '')"],
        env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.error("COPY échoué : %s", result.stderr.strip())
        sys.exit(1)

    elapsed = time.time() - t0
    log.info("COPY terminé en %.0fs", elapsed)


def swap_tables():
    """Swap atomique : etablissements_new → etablissements."""
    log.info("Swap des tables ...")

    # Vérifier que la nouvelle table a des données
    new_count = psql_val("SELECT COUNT(*) FROM etablissements_new;")
    log.info("  etablissements_new : %s lignes", new_count)
    if not new_count or int(new_count) == 0:
        log.error("Table etablissements_new vide — abandon du swap")
        sys.exit(1)

    # Vérifier qu'on ne perd pas trop de données
    old_count = psql_val(
        "SELECT COUNT(*) FROM etablissements;" if psql_val(
            "SELECT to_regclass('etablissements');"
        ) != "" else "SELECT 0;"
    )
    if old_count and int(old_count) > 0:
        ratio = int(new_count) / int(old_count)
        if ratio < 0.5:
            log.error("Nouvelle table a %.0f%% de l'ancienne — abandon (protection)", ratio * 100)
            sys.exit(1)

    # Swap atomique dans une transaction
    psql("""
    BEGIN;
    DROP TABLE IF EXISTS etablissements_old CASCADE;
    ALTER TABLE IF EXISTS etablissements RENAME TO etablissements_old;
    ALTER TABLE etablissements_new RENAME TO etablissements;
    DROP TABLE IF EXISTS etablissements_old CASCADE;
    COMMIT;
    """)
    log.info("Swap terminé")


# ---------------------------------------------------------------------------
# Étape 3 : Index
# ---------------------------------------------------------------------------

def create_indexes(table: str = "etablissements"):
    """Créer tous les index sur la table."""
    log.info("Création des index sur %s ...", table)
    t0 = time.time()

    for name, ddl in INDEXES:
        t1 = time.time()
        # Adapter le DDL si on cible une autre table
        ddl_adapted = ddl.replace(" etablissements ", f" {table} ")
        ddl_adapted = ddl_adapted.replace(" etablissements(", f" {table}(")

        # Le nom de la PK est une constraint, les autres sont des index
        if "PRIMARY KEY" in ddl:
            pkey_name = f"{table}_pkey"
            psql(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {pkey_name};")
            ddl_adapted = ddl_adapted.replace("etablissements_pkey", pkey_name)
        else:
            psql(f"DROP INDEX IF EXISTS {name};")

        psql(ddl_adapted)
        log.info("  %-40s %.0fs", name, time.time() - t1)

    log.info("Index terminés en %.0fs", time.time() - t0)


def set_trigram_threshold():
    """Configurer le seuil trigram."""
    psql("ALTER DATABASE sirene SET pg_trgm.similarity_threshold = 0.2;")
    log.info("Seuil trigram : 0.2")


# ---------------------------------------------------------------------------
# Vérification
# ---------------------------------------------------------------------------

def verify():
    """Afficher les stats finales."""
    total = psql_val("SELECT COUNT(*) FROM etablissements;")
    actifs = psql_val("SELECT COUNT(*) FROM etablissements WHERE etat_administratif = 'A';")
    size = psql_val("SELECT pg_size_pretty(pg_total_relation_size('etablissements'));")
    n_idx = psql_val(
        "SELECT COUNT(*) FROM pg_indexes WHERE tablename = 'etablissements';"
    )

    log.info("--- Stats finales ---")
    log.info("  Total       : %s établissements", f"{int(total):,}" if total else "?")
    log.info("  Actifs      : %s", f"{int(actifs):,}" if actifs else "?")
    log.info("  Taille      : %s", size)
    log.info("  Index       : %s", n_idx)

    # Spot-check colonnes _clean
    sample = psql_val("""
        SELECT denomination_clean FROM etablissements
        WHERE denomination_clean IS NOT NULL AND denomination_clean != ''
        LIMIT 1;
    """)
    if sample:
        log.info("  denomination_clean sample : %s", sample[:60])
    else:
        log.warning("  denomination_clean vide — vérifier le calcul")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Import base Sirene dans PostgreSQL")
    parser.add_argument("--download", action="store_true",
                        help="Télécharger le stock depuis data.gouv.fr avant import")
    parser.add_argument("--csv", type=str, default=None,
                        help="Chemin vers un CSV custom (par défaut: /tmp/sirene_data/StockEtablissement_utf8.csv)")
    parser.add_argument("--incremental", action="store_true",
                        help="Import incrémental (non implémenté)")
    parser.add_argument("--indexes-only", action="store_true",
                        help="Recréer uniquement les index")
    args = parser.parse_args()

    t_global = time.time()

    log.info("=" * 60)
    log.info("SIRET Matcher — Import Base Sirene")
    log.info("=" * 60)

    # -- Mode indexes-only --
    if args.indexes_only:
        create_indexes()
        set_trigram_threshold()
        verify()
        log.info("Terminé en %.0fs", time.time() - t_global)
        return

    # -- Mode incrémental (stub) --
    if args.incremental:
        print("Import incrémental non encore implémenté.")
        print("Utilisez le mode full (sans --incremental) pour l'instant.")
        sys.exit(0)

    # -- Téléchargement --
    if args.download:
        download()

    # -- Résolution du CSV --
    csv_path = Path(args.csv) if args.csv else CSV_FILE
    if not csv_path.exists():
        log.error("CSV introuvable : %s", csv_path)
        log.error("Lancez avec --download pour télécharger, ou --csv pour spécifier un fichier")
        sys.exit(1)
    size_gb = csv_path.stat().st_size / (1024**3)
    log.info("CSV source : %s (%.1f Go)", csv_path, size_gb)

    # -- Test connexion BDD --
    test = psql_val("SELECT 1;")
    if test != "1":
        log.error("Impossible de se connecter à la BDD (host=%s port=%s db=%s)",
                   DB_HOST, DB_PORT, DB_NAME)
        sys.exit(1)
    log.info("Connexion BDD OK")

    # -- Import --
    create_temp_table()
    clean_csv, count = prepare_csv(csv_path)
    copy_to_db(clean_csv, "etablissements_new")

    # Nettoyage du fichier intermédiaire
    clean_csv.unlink(missing_ok=True)

    # -- Index sur la nouvelle table --
    create_indexes("etablissements_new")
    set_trigram_threshold()

    # -- Swap --
    swap_tables()

    # -- Vérification --
    verify()

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
    log.info("=" * 60)
    log.info("Import terminé en %.0fs (%.1f min)", elapsed, elapsed / 60)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
