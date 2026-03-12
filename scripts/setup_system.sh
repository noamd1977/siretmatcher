#!/bin/bash
set -e

echo "=== SIRET Matcher — Installation système ==="

# PostgreSQL
if ! command -v psql &> /dev/null; then
    echo "→ Installation PostgreSQL 16..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq postgresql-16 postgresql-contrib-16 libpq-dev
fi

# Python build deps
sudo apt-get install -y -qq python3-venv python3-dev wget unzip

# Démarrer PostgreSQL
sudo systemctl enable postgresql
sudo systemctl start postgresql

# Créer la base et l'utilisateur
echo "→ Configuration base de données..."
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='sirene_user'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER sirene_user WITH PASSWORD 'sirene_pass';"

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='sirene'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE sirene OWNER sirene_user;"

sudo -u postgres psql -d sirene -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
sudo -u postgres psql -d sirene -c "CREATE EXTENSION IF NOT EXISTS unaccent;"
sudo -u postgres psql -d sirene -c "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO sirene_user;"
sudo -u postgres psql -d sirene -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO sirene_user;"

# Optimiser PostgreSQL pour les requêtes trigrams
PG_CONF=$(sudo -u postgres psql -tc "SHOW config_file" | xargs)
echo "→ Optimisation PostgreSQL ($PG_CONF)..."
sudo tee -a "$PG_CONF" > /dev/null << 'PGCONF'

# SIRET Matcher optimizations
work_mem = 256MB
maintenance_work_mem = 512MB
effective_cache_size = 2GB
pg_trgm.similarity_threshold = 0.2
PGCONF

sudo systemctl restart postgresql

echo "=== Installation terminée ==="
echo "  Base: sirene"
echo "  User: sirene_user"
echo "  Extension pg_trgm: activée"
