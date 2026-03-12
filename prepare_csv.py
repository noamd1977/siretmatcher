#!/usr/bin/env python3
"""Pre-traitement : joindre UniteLegale + Etablissement, filtrer actifs.
Utilise SQLite pour la jointure (pas de RAM necessaire)."""
import csv
import sqlite3
import sys
import os
import time

SIRENE_CSV = "/tmp/sirene_data/StockEtablissement_utf8.csv"
UL_CSV = "/tmp/sirene_data/StockUniteLegale_utf8.csv"
CLEAN_CSV = "/tmp/sirene_data/clean_actifs.csv"
SQLITE_DB = "/tmp/sirene_data/tmp_denom.db"

t0 = time.time()

# --- Phase 1 : Charger denominations dans SQLite (sur disque, pas en RAM) ---
print(f"{time.strftime('%H:%M:%S')} Phase 1 : Import denominations dans SQLite...")

if os.path.exists(SQLITE_DB):
    os.remove(SQLITE_DB)

conn = sqlite3.connect(SQLITE_DB)
conn.execute("PRAGMA journal_mode=OFF")
conn.execute("PRAGMA synchronous=OFF")
conn.execute("CREATE TABLE ul (siren TEXT PRIMARY KEY, denom TEXT, usuelle TEXT)")

with open(UL_CSV, "r", encoding="utf-8") as f:
    reader = csv.reader(f)
    header = next(reader)
    idx_siren = header.index("siren")
    idx_denom = header.index("denominationUniteLegale")
    idx_usuelle = header.index("denominationUsuelle1UniteLegale")
    
    batch = []
    n = 0
    for row in reader:
        d = row[idx_denom]
        u = row[idx_usuelle]
        if d or u:  # ne stocker que les non-vides
            batch.append((row[idx_siren], d, u))
        n += 1
        if len(batch) >= 100000:
            conn.executemany("INSERT OR IGNORE INTO ul VALUES (?,?,?)", batch)
            batch = []
        if n % 5000000 == 0:
            print(f"  {n:>12,} unites legales lues...")
    
    if batch:
        conn.executemany("INSERT OR IGNORE INTO ul VALUES (?,?,?)", batch)
    conn.commit()

stored = conn.execute("SELECT COUNT(*) FROM ul").fetchone()[0]
print(f"  {n:>12,} lues, {stored:,} avec denomination, en {time.time()-t0:.0f}s")

# --- Phase 2 : Parcourir Etablissements, filtrer actifs, joindre ---
print(f"\n{time.strftime('%H:%M:%S')} Phase 2 : Extraction etablissements actifs...")
t1 = time.time()

with open(SIRENE_CSV, "r", encoding="utf-8") as f, \
     open(CLEAN_CSV, "w", newline="", encoding="utf-8") as out:

    reader = csv.reader(f)
    header = next(reader)

    def idx(name):
        try:
            return header.index(name)
        except ValueError:
            print(f"  ATTENTION: colonne '{name}' introuvable")
            return None

    col = {
        "siret": idx("siret"),
        "siren": idx("siren"),
        "enseigne1": idx("enseigne1Etablissement"),
        "enseigne2": idx("enseigne2Etablissement"),
        "denom_usuelle_etab": idx("denominationUsuelleEtablissement"),
        "naf": idx("activitePrincipaleEtablissement"),
        "numero_voie": idx("numeroVoieEtablissement"),
        "type_voie": idx("typeVoieEtablissement"),
        "voie": idx("libelleVoieEtablissement"),
        "code_postal": idx("codePostalEtablissement"),
        "commune": idx("libelleCommuneEtablissement"),
        "tranche_effectif": idx("trancheEffectifsEtablissement"),
        "date_creation": idx("dateCreationEtablissement"),
        "etat_admin": idx("etatAdministratifEtablissement"),
    }

    for name in ["siret", "siren", "etat_admin"]:
        if col[name] is None:
            print(f"ERREUR FATALE: colonne '{name}' manquante")
            sys.exit(1)

    def get(row, name):
        i = col.get(name)
        if i is not None and i < len(row):
            return row[i].strip()
        return ""

    def calc_dept(cp):
        if not cp or len(cp) < 2:
            return ""
        try:
            if cp.startswith("97") and len(cp) >= 3:
                return cp[:3]
            if cp.startswith("20") and len(cp) == 5 and cp.isdigit():
                return "2A" if int(cp) <= 20190 else "2B"
            if cp.startswith("20"):
                return "2A"
            if cp[:2].isdigit():
                return cp[:2]
        except (ValueError, IndexError):
            pass
        return cp[:2] if len(cp) >= 2 else ""

    cursor = conn.cursor()
    writer = csv.writer(out)
    total = 0
    actifs = 0

    for row in reader:
        total += 1
        if get(row, "etat_admin") != "A":
            if total % 5000000 == 0:
                print(f"  {total:>12,} lues, {actifs:>10,} actives...")
            continue

        siret = get(row, "siret")
        siren = get(row, "siren")
        if not siret or len(siret) != 14:
            continue

        # Lookup denomination dans SQLite
        r = cursor.execute("SELECT denom, usuelle FROM ul WHERE siren=?", (siren,)).fetchone()
        denom = r[0] if r else ""
        denom_usuelle = r[1] if r else ""
        
        denom_usuelle_etab = get(row, "denom_usuelle_etab")
        if denom_usuelle_etab and denom_usuelle:
            denom_usuelle = denom_usuelle + " " + denom_usuelle_etab
        elif denom_usuelle_etab:
            denom_usuelle = denom_usuelle_etab

        cp = get(row, "code_postal")
        writer.writerow([
            siret, siren, denom, denom_usuelle,
            get(row, "enseigne1"), get(row, "enseigne2"),
            get(row, "naf"), get(row, "numero_voie"),
            get(row, "type_voie"), get(row, "voie"),
            cp, get(row, "commune"),
            get(row, "tranche_effectif"), get(row, "date_creation"),
            "A", calc_dept(cp),
        ])
        actifs += 1
        if total % 5000000 == 0:
            print(f"  {total:>12,} lues, {actifs:>10,} actives...")

    print(f"\n  Total lues:      {total:>12,}")
    print(f"  Actifs exportes: {actifs:>12,}")
    print(f"  Duree:           {time.time()-t1:.0f}s")

conn.close()
os.remove(SQLITE_DB)
print(f"\n{time.strftime('%H:%M:%S')} Termine. Fichier: {CLEAN_CSV}")
