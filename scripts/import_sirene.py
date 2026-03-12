#!/usr/bin/env python3
"""
Import du stock Sirene dans PostgreSQL.

Télécharge le fichier StockEtablissement depuis data.gouv.fr (~2 Go compressé),
l'importe dans PostgreSQL et crée les index trigrams.

Usage:
    python scripts/import_sirene.py           # Import complet
    python scripts/import_sirene.py --update  # Mise à jour (re-télécharge + replace)
    python scripts/import_sirene.py --skip-download  # Import depuis fichier local existant
"""
import os
import sys
import time
import subprocess
import argparse
import csv
from pathlib import Path

# Config
DB_NAME = os.getenv("DB_NAME", "sirene")
DB_USER = os.getenv("DB_USER", "sirene_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "sirene_pass")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")

DATA_DIR = Path("/tmp/sirene_data")
STOCK_URL = "https://files.data.gouv.fr/insee-sirene/StockEtablissement_utf8.zip"
CSV_FILE = DATA_DIR / "StockEtablissement_utf8.csv"

# Colonnes à extraire du CSV Sirene (sur ~40 colonnes, on en garde ~20)
COLONNES_UTILES = [
    "siret", "siren",
    "denominationUniteLegale", "denominationUsuelle1UniteLegale",
    "enseigne1Etablissement", "enseigne2Etablissement",
    "activitePrincipaleEtablissement",
    "numeroVoieEtablissement", "typeVoieEtablissement", "libelleVoieEtablissement",
    "codePostalEtablissement", "libelleCommuneEtablissement",
    "trancheEffectifsEtablissement",
    "dateCreationEtablissement",
    "etatAdministratifEtablissement",
]


def psql(cmd: str, db: str = DB_NAME):
    """Exécuter une commande psql."""
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD
    result = subprocess.run(
        ["psql", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", db, "-c", cmd],
        env=env, capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  ERREUR psql: {result.stderr.strip()}")
    return result


def download():
    """Télécharger le stock Sirene."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    zip_file = DATA_DIR / "stock.zip"

    print(f"→ Téléchargement ({STOCK_URL})...")
    print("  (environ 2 Go, ~5-15 min selon connexion)")
    subprocess.run(["wget", "-q", "--show-progress", "-O", str(zip_file), STOCK_URL], check=True)

    print("→ Décompression...")
    subprocess.run(["unzip", "-o", str(zip_file), "-d", str(DATA_DIR)], check=True)
    zip_file.unlink()

    if not CSV_FILE.exists():
        # Le fichier peut avoir un nom légèrement différent
        csvs = list(DATA_DIR.glob("Stock*.csv"))
        if csvs:
            csvs[0].rename(CSV_FILE)
    
    size_gb = CSV_FILE.stat().st_size / (1024**3)
    print(f"  Fichier: {CSV_FILE} ({size_gb:.1f} Go)")


def create_table():
    """Créer la table et les extensions."""
    print("→ Création de la table...")
    psql("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    psql("CREATE EXTENSION IF NOT EXISTS unaccent;")

    psql("DROP TABLE IF EXISTS etablissements CASCADE;")
    psql("""
    CREATE TABLE etablissements (
        siret VARCHAR(14) PRIMARY KEY,
        siren VARCHAR(9) NOT NULL,
        denomination VARCHAR(200),
        denomination_usuelle VARCHAR(200),
        enseigne VARCHAR(200),
        enseigne2 VARCHAR(200),
        naf VARCHAR(10),
        numero_voie VARCHAR(10),
        type_voie VARCHAR(10),
        voie VARCHAR(200),
        code_postal VARCHAR(5),
        commune VARCHAR(100),
        tranche_effectif VARCHAR(5),
        date_creation VARCHAR(20),
        etat_administratif VARCHAR(1),
        -- Colonnes calculées pour le matching
        departement VARCHAR(3),
        denomination_clean VARCHAR(200),
        enseigne_clean VARCHAR(200),
        voie_clean VARCHAR(200)
    );
    """)


def import_csv():
    """Importer le CSV dans PostgreSQL en streaming."""
    print("→ Import du CSV dans PostgreSQL...")
    print("  (environ 10-20 min pour 12M lignes)")

    t0 = time.time()
    count = 0
    batch = []
    BATCH_SIZE = 50000

    # Build column index mapping
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        col_idx = {}
        for col in COLONNES_UTILES:
            if col in header:
                col_idx[col] = header.index(col)

        if not col_idx:
            print(f"  ERREUR: colonnes introuvables. Header: {header[:10]}...")
            sys.exit(1)

        def get(row, col):
            idx = col_idx.get(col)
            if idx is not None and idx < len(row):
                return row[idx].replace("'", "''").strip()
            return ""

        # Préparer la copie via un fichier temporaire
        tmp_csv = DATA_DIR / "import_clean.csv"
        with open(tmp_csv, "w", encoding="utf-8", newline="") as out:
            writer = csv.writer(out)
            for row in reader:
                siret = get(row, "siret")
                if not siret or len(siret) != 14:
                    continue

                denom = get(row, "denominationUniteLegale")
                denom_usuelle = get(row, "denominationUsuelle1UniteLegale")
                enseigne = get(row, "enseigne1Etablissement")
                enseigne2 = get(row, "enseigne2Etablissement")
                cp = get(row, "codePostalEtablissement")

                # Calculer département
                dept = ""
                if cp:
                    if cp.startswith("97"):
                        dept = cp[:3]
                    elif cp.startswith("200") or cp.startswith("201"):
                        dept = "2A" if cp.isdigit() and int(cp) <= 20190 else "2B"
                    elif cp.startswith("20"):
                        dept = "2A"
                    else:
                        dept = cp[:2]

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
                    get(row, "libelleVoieEtablissement"),
                    cp,
                    get(row, "libelleCommuneEtablissement"),
                    get(row, "trancheEffectifsEtablissement"),
                    get(row, "dateCreationEtablissement"),
                    get(row, "etatAdministratifEtablissement"),
                    dept,
                    "",  # denomination_clean (calculé après)
                    "",  # enseigne_clean
                    "",  # voie_clean
                ])
                count += 1
                if count % 500000 == 0:
                    elapsed = time.time() - t0
                    print(f"  {count:>10,} lignes préparées ({elapsed:.0f}s)")

    print(f"  {count:,} lignes préparées en {time.time()-t0:.0f}s")

    # COPY depuis le fichier nettoyé
    print("→ COPY dans PostgreSQL...")
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD
    subprocess.run([
        "psql", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", DB_NAME,
        "-c", f"\\COPY etablissements FROM '{tmp_csv}' WITH (FORMAT csv, NULL '')"
    ], env=env, check=True)

    elapsed = time.time() - t0
    print(f"  {count:,} lignes importées en {elapsed:.0f}s")


def compute_clean_columns():
    """Calculer les colonnes _clean pour le matching trigram."""
    print("→ Calcul des colonnes de matching (unaccent + upper)...")
    t0 = time.time()

    psql("""
    UPDATE etablissements SET
        denomination_clean = UPPER(unaccent(COALESCE(denomination, '') || ' ' || COALESCE(denomination_usuelle, ''))),
        enseigne_clean = UPPER(unaccent(COALESCE(enseigne, '') || ' ' || COALESCE(enseigne2, ''))),
        voie_clean = UPPER(unaccent(COALESCE(voie, '')));
    """)

    # Nettoyer les caractères spéciaux dans les colonnes clean
    psql("""
    UPDATE etablissements SET
        denomination_clean = REGEXP_REPLACE(denomination_clean, '[^A-Z0-9 ]', ' ', 'g'),
        denomination_clean = REGEXP_REPLACE(denomination_clean, '\\s+', ' ', 'g'),
        denomination_clean = TRIM(denomination_clean),
        enseigne_clean = REGEXP_REPLACE(enseigne_clean, '[^A-Z0-9 ]', ' ', 'g'),
        enseigne_clean = REGEXP_REPLACE(enseigne_clean, '\\s+', ' ', 'g'),
        enseigne_clean = TRIM(enseigne_clean),
        voie_clean = REGEXP_REPLACE(voie_clean, '[^A-Z0-9 ]', ' ', 'g'),
        voie_clean = REGEXP_REPLACE(voie_clean, '\\s+', ' ', 'g'),
        voie_clean = TRIM(voie_clean);
    """)

    print(f"  Terminé en {time.time()-t0:.0f}s")


def create_indexes():
    """Créer tous les index pour les requêtes de matching."""
    print("→ Création des index (peut prendre 5-10 min)...")
    t0 = time.time()

    indexes = [
        # Index B-tree classiques
        ("idx_etab_cp", "CREATE INDEX idx_etab_cp ON etablissements (code_postal);"),
        ("idx_etab_dept", "CREATE INDEX idx_etab_dept ON etablissements (departement);"),
        ("idx_etab_etat", "CREATE INDEX idx_etab_etat ON etablissements (etat_administratif);"),
        ("idx_etab_siren", "CREATE INDEX idx_etab_siren ON etablissements (siren);"),
        ("idx_etab_num_voie", "CREATE INDEX idx_etab_num_voie ON etablissements (numero_voie);"),
        # Index composites pour les requêtes courantes
        ("idx_etab_cp_etat", "CREATE INDEX idx_etab_cp_etat ON etablissements (code_postal, etat_administratif);"),
        ("idx_etab_dept_etat", "CREATE INDEX idx_etab_dept_etat ON etablissements (departement, etat_administratif);"),
        ("idx_etab_cp_num", "CREATE INDEX idx_etab_cp_num ON etablissements (code_postal, numero_voie);"),
        # INDEX TRIGRAM — le cœur du matching fuzzy
        ("idx_denom_trgm", "CREATE INDEX idx_denom_trgm ON etablissements USING GIN (denomination_clean gin_trgm_ops);"),
        ("idx_enseigne_trgm", "CREATE INDEX idx_enseigne_trgm ON etablissements USING GIN (enseigne_clean gin_trgm_ops);"),
        ("idx_voie_trgm", "CREATE INDEX idx_voie_trgm ON etablissements USING GIN (voie_clean gin_trgm_ops);"),
    ]

    for name, sql in indexes:
        t1 = time.time()
        psql(f"DROP INDEX IF EXISTS {name};")
        psql(sql)
        print(f"  {name}: {time.time()-t1:.0f}s")

    print(f"  Total index: {time.time()-t0:.0f}s")


def set_trigram_threshold():
    """Configurer le seuil trigram global."""
    psql("ALTER DATABASE sirene SET pg_trgm.similarity_threshold = 0.2;")
    print("→ Seuil trigram: 0.2")


def verify():
    """Vérifier l'import."""
    print("\n→ Vérification...")
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD

    result = subprocess.run(
        ["psql", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", DB_NAME,
         "-t", "-c", "SELECT COUNT(*) FROM etablissements;"],
        env=env, capture_output=True, text=True
    )
    total = result.stdout.strip()

    result = subprocess.run(
        ["psql", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", DB_NAME,
         "-t", "-c", "SELECT COUNT(*) FROM etablissements WHERE etat_administratif = 'A';"],
        env=env, capture_output=True, text=True
    )
    actifs = result.stdout.strip()

    result = subprocess.run(
        ["psql", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", DB_NAME,
         "-t", "-c", "SELECT pg_size_pretty(pg_total_relation_size('etablissements'));"],
        env=env, capture_output=True, text=True
    )
    size = result.stdout.strip()

    print(f"  Total: {total} établissements")
    print(f"  Actifs: {actifs}")
    print(f"  Taille: {size}")

    # Test trigram
    print("\n→ Test trigram...")
    result = subprocess.run(
        ["psql", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", DB_NAME,
         "-c", """
         SELECT siret, denomination, enseigne, code_postal,
                similarity(denomination_clean, 'GARAGE PACHA') AS sim
         FROM etablissements
         WHERE code_postal = '20090'
           AND etat_administratif = 'A'
           AND denomination_clean % 'GARAGE PACHA'
         ORDER BY sim DESC LIMIT 5;
         """],
        env=env, capture_output=True, text=True
    )
    print(result.stdout)


def main():
    parser = argparse.ArgumentParser(description="Import base Sirene dans PostgreSQL")
    parser.add_argument("--update", action="store_true", help="Re-télécharger et remplacer")
    parser.add_argument("--skip-download", action="store_true", help="Utiliser le CSV local existant")
    parser.add_argument("--indexes-only", action="store_true", help="Recréer uniquement les index")
    args = parser.parse_args()

    print("=" * 60)
    print("SIRET Matcher — Import Base Sirene")
    print("=" * 60)

    if args.indexes_only:
        create_indexes()
        set_trigram_threshold()
        verify()
        return

    if not args.skip_download:
        if CSV_FILE.exists() and not args.update:
            size_gb = CSV_FILE.stat().st_size / (1024**3)
            print(f"Fichier existant: {CSV_FILE} ({size_gb:.1f} Go)")
            print("Utilisez --update pour re-télécharger ou --skip-download pour réutiliser")
            resp = input("Continuer avec le fichier existant? [O/n] ")
            if resp.lower() == "n":
                download()
        else:
            download()
    
    if not CSV_FILE.exists():
        print(f"ERREUR: {CSV_FILE} introuvable")
        sys.exit(1)

    create_table()
    import_csv()
    compute_clean_columns()
    create_indexes()
    set_trigram_threshold()
    verify()

    print("\n" + "=" * 60)
    print("Import terminé avec succès!")
    print("=" * 60)


if __name__ == "__main__":
    main()
