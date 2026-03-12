#!/bin/bash
set -e

echo "=== Installation API SIRET Matcher ==="

# 1. Installer FastAPI + Uvicorn
cd /opt/siret-matcher
source venv/bin/activate
pip install fastapi uvicorn httpx --quiet
echo "OK deps installees"

# 2. Copier les fichiers
cp /opt/siret-matcher/api.py /opt/siret-matcher/api.py 2>/dev/null || true
cp /opt/siret-matcher/siret-matcher.service /etc/systemd/system/siret-matcher.service

# 3. Donner accès à postgres
chown -R postgres:postgres /opt/siret-matcher

# 4. Démarrer le service
systemctl daemon-reload
systemctl enable siret-matcher
systemctl restart siret-matcher
sleep 3

# 5. Test
echo ""
echo "=== Test API ==="
curl -s http://127.0.0.1:8042/health | python3 -m json.tool

echo ""
echo "=== Test matching ==="
curl -s -X POST http://127.0.0.1:8042/match \
  -H "Content-Type: application/json" \
  -d '{"nom": "Garage franchini", "adresse": "Route des Sanguinaires", "code_postal": "20000", "ville": "Ajaccio"}' \
  | python3 -m json.tool

echo ""
echo "=== TERMINE ==="
echo "API disponible sur http://127.0.0.1:8042"
echo "n8n peut appeler POST http://127.0.0.1:8042/match"
echo "ou POST http://127.0.0.1:8042/match/batch"
