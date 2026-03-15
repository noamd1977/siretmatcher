#!/bin/bash
# Déploiement sur le serveur de production
# Usage: ./scripts/deploy.sh [branch]
set -euo pipefail

BRANCH=${1:-v3}
PROJECT_DIR="/opt/siret-matcher"

cd "$PROJECT_DIR"

echo "=== Pulling $BRANCH ==="
git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"

echo "=== Installing dependencies ==="
source venv/bin/activate
pip install -r requirements.txt -q

echo "=== Running unit tests ==="
pytest tests/ -m "not integration and not regression and not slow" -v --tb=short
if [ $? -ne 0 ]; then
    echo "❌ Tests failed — aborting deployment"
    exit 1
fi

echo "=== Restarting service ==="
sudo systemctl restart siret-matcher
sleep 3

echo "=== Health check ==="
curl -sf http://localhost:8042/health | python3 -m json.tool
if [ $? -ne 0 ]; then
    echo "❌ Health check failed"
    exit 1
fi

echo "✅ Deployment successful"
