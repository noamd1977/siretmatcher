#!/bin/bash
set -e
PSQL="sudo -u postgres psql"

echo "$(date '+%H:%M:%S') === ETAPE 1/6 - Pre-traitement CSV ==="
python3 /opt/siret-matcher/prepare_csv.py
ls -lh /tmp/sirene_data/clean_actifs.csv

echo ""
echo "$(date '+%H:%M:%S') === ETAPE 2/6 - Recreer la base ==="
$PSQL -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'sirene' AND pid != pg_backend_pid();" 2>/dev/null || true
$PSQL -c "DROP DATABASE IF EXISTS sirene;"
$PSQL -c "CREATE DATABASE sirene;"
$PSQL -d sirene -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
$PSQL -d sirene -c "CREATE EXTENSION IF NOT EXISTS unaccent;"
echo "OK base sirene recree"

echo ""
echo "$(date '+%H:%M:%S') === ETAPE 3/6 - Creer table + import ==="
$PSQL -d sirene -c "
CREATE TABLE etablissements (
    siret TEXT PRIMARY KEY, siren TEXT NOT NULL,
    denomination TEXT, denomination_usuelle TEXT,
    enseigne TEXT, enseigne2 TEXT, naf TEXT,
    numero_voie TEXT, type_voie TEXT, voie TEXT,
    code_postal TEXT, commune TEXT, tranche_effectif TEXT,
    date_creation TEXT, etat_administratif TEXT, departement TEXT,
    denomination_clean TEXT, enseigne_clean TEXT, voie_clean TEXT
);"
$PSQL -d sirene -c "\COPY etablissements(siret,siren,denomination,denomination_usuelle,enseigne,enseigne2,naf,numero_voie,type_voie,voie,code_postal,commune,tranche_effectif,date_creation,etat_administratif,departement) FROM '/tmp/sirene_data/clean_actifs.csv' WITH (FORMAT csv, NULL '')"
$PSQL -d sirene -c "SELECT COUNT(*) AS nombre_etablissements FROM etablissements;"

echo ""
echo "$(date '+%H:%M:%S') === ETAPE 4/6 - Colonnes clean ==="
$PSQL -d sirene -c "
UPDATE etablissements SET
    denomination_clean = TRIM(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(unaccent(COALESCE(denomination,'') || ' ' || COALESCE(denomination_usuelle,''))), '[^A-Z0-9 ]', ' ', 'g'), '\s+', ' ', 'g')),
    enseigne_clean = TRIM(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(unaccent(COALESCE(enseigne,'') || ' ' || COALESCE(enseigne2,''))), '[^A-Z0-9 ]', ' ', 'g'), '\s+', ' ', 'g')),
    voie_clean = TRIM(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(unaccent(COALESCE(voie,''))), '[^A-Z0-9 ]', ' ', 'g'), '\s+', ' ', 'g'));"
echo "OK colonnes clean"

echo ""
echo "$(date '+%H:%M:%S') === ETAPE 5/6 - Index ==="
$PSQL -d sirene -c "CREATE INDEX idx_cp ON etablissements (code_postal);"
$PSQL -d sirene -c "CREATE INDEX idx_dept ON etablissements (departement);"
$PSQL -d sirene -c "CREATE INDEX idx_siren ON etablissements (siren);"
$PSQL -d sirene -c "CREATE INDEX idx_cp_num ON etablissements (code_postal, numero_voie);"
echo "OK btree"
$PSQL -d sirene -c "CREATE INDEX idx_denom_trgm ON etablissements USING GIN (denomination_clean gin_trgm_ops);"
echo "OK denom trigram"
$PSQL -d sirene -c "CREATE INDEX idx_enseigne_trgm ON etablissements USING GIN (enseigne_clean gin_trgm_ops);"
echo "OK enseigne trigram"
$PSQL -d sirene -c "CREATE INDEX idx_voie_trgm ON etablissements USING GIN (voie_clean gin_trgm_ops);"
echo "OK voie trigram"
$PSQL -d sirene -c "ALTER DATABASE sirene SET pg_trgm.similarity_threshold = 0.2;"

echo ""
echo "$(date '+%H:%M:%S') === ETAPE 6/6 - Tests ==="
$PSQL -d sirene -c "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE denomination != '') AS avec_denom, COUNT(*) FILTER (WHERE enseigne != '') AS avec_enseigne FROM etablissements;"
$PSQL -d sirene -c "SELECT siret, denomination, enseigne, denomination_clean FROM etablissements WHERE code_postal = '20090' LIMIT 5;"
$PSQL -d sirene -c "SET pg_trgm.similarity_threshold = 0.15; SELECT siret, denomination, enseigne, similarity(denomination_clean, 'AUTO PNEUS SERVICES') AS sim FROM etablissements WHERE code_postal IN ('20090','20000') AND denomination_clean % 'AUTO PNEUS SERVICES' ORDER BY sim DESC LIMIT 5;"
$PSQL -d sirene -c "SET pg_trgm.similarity_threshold = 0.15; SELECT siret, denomination, enseigne, numero_voie, voie FROM etablissements WHERE code_postal IN ('20090','20000') AND numero_voie = '32' AND voie_clean % 'AVENUE NOEL FRANCHINI' LIMIT 5;"

echo ""
echo "$(date '+%H:%M:%S') === Auth sirene_user ==="
$PSQL -c "DROP USER IF EXISTS sirene_user;"
$PSQL -c "CREATE USER sirene_user WITH PASSWORD 'sirene_pass';"
$PSQL -d sirene -c "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO sirene_user;"
PG_HBA=$(find /etc/postgresql -name pg_hba.conf 2>/dev/null | head -1)
if [ -n "$PG_HBA" ] && ! grep -q "sirene_user" "$PG_HBA"; then
    sed -i '/^host.*all.*all.*127/i host sirene sirene_user 127.0.0.1/32 md5' "$PG_HBA"
    sed -i '/^local.*all.*all/i local sirene sirene_user md5' "$PG_HBA"
    systemctl reload postgresql
fi
sleep 2
PGPASSWORD=sirene_pass psql -h 127.0.0.1 -U sirene_user -d sirene -c "SELECT COUNT(*) AS test FROM etablissements;" 2>&1 || echo "Auth sirene_user echouee"

echo ""
echo "$(date '+%H:%M:%S') === TERMINE ==="
