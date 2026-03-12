#!/bin/bash
# =============================================================
# SIRET Matcher — Script de réparation et finalisation
# Lance-le dans screen : screen -S fix && bash /opt/siret-matcher/fix_db.sh
# =============================================================
set -e

PSQL="sudo -u postgres psql -d sirene"

echo "=============================================="
echo "ÉTAPE 1/7 — Diagnostic"
echo "=============================================="
$PSQL -c "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE etat_administratif = 'A') AS actifs, COUNT(*) FILTER (WHERE denomination IS NOT NULL AND denomination != '') AS avec_denom FROM etablissements;"

echo ""
echo "=============================================="
echo "ÉTAPE 2/7 — Tuer toute requête en cours"
echo "=============================================="
$PSQL -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'sirene' AND pid != pg_backend_pid() AND state = 'active';" || true
sleep 2

echo ""
echo "=============================================="
echo "ÉTAPE 3/7 — Supprimer les établissements inactifs"
echo "=============================================="
ACTIFS=$($PSQL -t -c "SELECT COUNT(*) FROM etablissements WHERE etat_administratif != 'A';" | xargs)
if [ "$ACTIFS" -gt 0 ]; then
    echo "→ Suppression de $ACTIFS lignes inactives..."
    $PSQL -c "DELETE FROM etablissements WHERE etat_administratif != 'A';"
    echo "→ VACUUM..."
    $PSQL -c "VACUUM FULL etablissements;"
else
    echo "→ Déjà nettoyé, rien à supprimer."
fi
$PSQL -c "SELECT COUNT(*) AS restant FROM etablissements;"

echo ""
echo "=============================================="
echo "ÉTAPE 4/7 — Vérifier et injecter les dénominations"
echo "=============================================="
AVEC_DENOM=$($PSQL -t -c "SELECT COUNT(*) FROM etablissements WHERE denomination IS NOT NULL AND denomination != '';" | xargs)
TOTAL=$($PSQL -t -c "SELECT COUNT(*) FROM etablissements;" | xargs)
echo "→ $AVEC_DENOM / $TOTAL ont une dénomination"

if [ "$AVEC_DENOM" -lt "$((TOTAL / 2))" ]; then
    echo "→ Dénominations manquantes, injection depuis tmp_ul..."
    
    # Vérifier si tmp_ul existe encore
    TMP_EXISTS=$($PSQL -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'tmp_ul';" | xargs)
    
    if [ "$TMP_EXISTS" -eq 0 ]; then
        echo "→ Table tmp_ul absente, recréation depuis StockUniteLegale..."
        
        # Extraire siren + denom du CSV
        python3 -c "
import csv, sys
print('Extraction siren + denomination...', file=sys.stderr)
with open('/tmp/sirene_data/StockUniteLegale_utf8.csv', 'r', encoding='utf-8') as f, \
     open('/tmp/sirene_data/ul_denom.csv', 'w', newline='') as out:
    reader = csv.reader(f)
    header = next(reader)
    i_siren = header.index('siren')
    i_denom = header.index('denominationUniteLegale')
    i_usuelle = header.index('denominationUsuelle1UniteLegale')
    writer = csv.writer(out)
    count = 0
    for row in reader:
        writer.writerow([row[i_siren], row[i_denom], row[i_usuelle]])
        count += 1
        if count % 5000000 == 0:
            print(f'  {count:,}...', file=sys.stderr)
    print(f'  {count:,} extraites', file=sys.stderr)
"
        $PSQL -c "DROP TABLE IF EXISTS tmp_ul; CREATE TABLE tmp_ul (siren VARCHAR(9) PRIMARY KEY, denomination VARCHAR(500), denomination_usuelle VARCHAR(500));"
        $PSQL -c "\COPY tmp_ul FROM '/tmp/sirene_data/ul_denom.csv' WITH (FORMAT csv, NULL '')"
    fi
    
    echo "→ UPDATE JOIN (sur ~16M lignes cette fois, beaucoup plus rapide)..."
    $PSQL -c "UPDATE etablissements e SET denomination = t.denomination, denomination_usuelle = t.denomination_usuelle FROM tmp_ul t WHERE e.siren = t.siren AND (e.denomination IS NULL OR e.denomination = '');"
    $PSQL -c "DROP TABLE IF EXISTS tmp_ul;"
else
    echo "→ Dénominations OK, rien à faire."
fi

echo ""
echo "=============================================="
echo "ÉTAPE 5/7 — Recalculer les colonnes clean"
echo "=============================================="
$PSQL -c "
UPDATE etablissements SET
    denomination_clean = TRIM(REGEXP_REPLACE(REGEXP_REPLACE(
        UPPER(unaccent(COALESCE(denomination, '') || ' ' || COALESCE(denomination_usuelle, ''))),
        '[^A-Z0-9 ]', ' ', 'g'), '\s+', ' ', 'g')),
    enseigne_clean = TRIM(REGEXP_REPLACE(REGEXP_REPLACE(
        UPPER(unaccent(COALESCE(enseigne, '') || ' ' || COALESCE(enseigne2, ''))),
        '[^A-Z0-9 ]', ' ', 'g'), '\s+', ' ', 'g')),
    voie_clean = TRIM(REGEXP_REPLACE(REGEXP_REPLACE(
        UPPER(unaccent(COALESCE(voie, ''))),
        '[^A-Z0-9 ]', ' ', 'g'), '\s+', ' ', 'g'));
"

echo ""
echo "=============================================="
echo "ÉTAPE 6/7 — Recréer tous les index"
echo "=============================================="
for idx in idx_etab_cp idx_etab_dept idx_etab_etat idx_etab_siren idx_etab_cp_etat idx_etab_dept_etat idx_etab_cp_num idx_denom_trgm idx_enseigne_trgm idx_voie_trgm; do
    $PSQL -c "DROP INDEX IF EXISTS $idx;" 2>/dev/null
done

$PSQL -c "CREATE INDEX idx_etab_cp ON etablissements (code_postal);"
$PSQL -c "CREATE INDEX idx_etab_dept ON etablissements (departement);"
$PSQL -c "CREATE INDEX idx_etab_etat ON etablissements (etat_administratif);"
$PSQL -c "CREATE INDEX idx_etab_siren ON etablissements (siren);"
$PSQL -c "CREATE INDEX idx_etab_cp_etat ON etablissements (code_postal, etat_administratif);"
$PSQL -c "CREATE INDEX idx_etab_dept_etat ON etablissements (departement, etat_administratif);"
$PSQL -c "CREATE INDEX idx_etab_cp_num ON etablissements (code_postal, numero_voie);"
$PSQL -c "CREATE INDEX idx_denom_trgm ON etablissements USING GIN (denomination_clean gin_trgm_ops);"
$PSQL -c "CREATE INDEX idx_enseigne_trgm ON etablissements USING GIN (enseigne_clean gin_trgm_ops);"
$PSQL -c "CREATE INDEX idx_voie_trgm ON etablissements USING GIN (voie_clean gin_trgm_ops);"
$PSQL -c "ALTER DATABASE sirene SET pg_trgm.similarity_threshold = 0.2;"

echo ""
echo "=============================================="
echo "ÉTAPE 7/7 — Tests de validation"
echo "=============================================="
echo "--- Stats ---"
$PSQL -c "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE denomination IS NOT NULL AND denomination != '') AS avec_denom, COUNT(*) FILTER (WHERE enseigne IS NOT NULL AND enseigne != '') AS avec_enseigne FROM etablissements;"

echo ""
echo "--- Test trigram : GARAGE PACHA à Ajaccio ---"
$PSQL -c "SET pg_trgm.similarity_threshold = 0.15; SELECT siret, denomination, enseigne, code_postal, similarity(denomination_clean, 'GARAGE PACHA') AS sim FROM etablissements WHERE code_postal = '20090' AND denomination_clean % 'GARAGE PACHA' ORDER BY sim DESC LIMIT 5;"

echo ""
echo "--- Test trigram : AUTO PNEUS SERVICES ---"
$PSQL -c "SET pg_trgm.similarity_threshold = 0.15; SELECT siret, denomination, enseigne, code_postal, similarity(denomination_clean, 'AUTO PNEUS SERVICES') AS sim FROM etablissements WHERE code_postal IN ('20090','20000') AND denomination_clean % 'AUTO PNEUS SERVICES' ORDER BY sim DESC LIMIT 5;"

echo ""
echo "--- Test adresse : 32 AV NOEL FRANCHINI ---"
$PSQL -c "SELECT siret, denomination, enseigne, numero_voie, voie, code_postal FROM etablissements WHERE code_postal IN ('20090','20000') AND numero_voie = '32' AND voie_clean % 'AVENUE NOEL FRANCHINI' LIMIT 5;"

echo ""
echo "=============================================="
echo "TERMINÉ ! Base Sirene prête."
echo "=============================================="
