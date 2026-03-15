# Tests — SIRET Matcher

## Installation

```bash
cd /opt/siret-matcher
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
```

La base PostgreSQL Sirene locale doit tourner (voir `config/.env`).

## Lancer les tests

```bash
# Tous les tests
make test

# Tests rapides (pas de réseau ni DB) — normalizer, scoring, scraper unit
make test-fast

# Tests d'intégration (API gouv.fr + BAN + PostgreSQL)
make test-integration

# Tests de non-régression du pipeline complet
make test-regression

# Rapport de couverture de code
make test-coverage
```

## Structure des fichiers

| Fichier | Contenu | Marqueurs |
|---|---|---|
| `test_normalizer.py` | Normalisation des noms, adresses, variantes | aucun (rapide) |
| `test_scoring.py` | Fonctions de scoring (nom, géo, adresse) | aucun (rapide) |
| `test_api.py` | Endpoints FastAPI via ASGITransport | `integration` |
| `test_stages.py` | Chaque étape du pipeline isolée (1→5 + orchestrateur) | `integration` |
| `test_pipeline.py` | Non-régression sur le jeu de données de référence | `regression`, `slow` |
| `test_matching.py` | Tests legacy du matching (ancien format) | `integration` |

## Marqueurs pytest

Définis dans `pytest.ini` :

- **`integration`** — Nécessite la base PostgreSQL et/ou les API externes (gouv.fr, BAN). Ces tests font de vrais appels réseau.
- **`regression`** — Tests de non-régression avec seuils de qualité. Vérifie que le pipeline maintient un taux de matching acceptable.
- **`slow`** — Tests lents (> 30s). Typiquement les tests de régression qui matchent tout le jeu de référence.

## Jeu de données de référence

Le fichier `tests/data/prospects_reference.json` contient les prospects de référence pour les tests de non-régression.

### Format d'une entrée

```json
{
  "id": "easy_001",
  "difficulty": "easy",
  "input": {
    "nom": "GOOGLE FRANCE",
    "adresse": "8 Rue de Londres",
    "code_postal": "75009",
    "ville": "Paris"
  },
  "expected": {
    "should_match": true,
    "siret": "44306184100047",
    "min_score": 60
  }
}
```

### Ajouter un prospect de test

1. Choisir la difficulté : `easy`, `medium`, `hard`, ou `impossible`
2. Trouver le SIRET attendu sur [annuaire-entreprises.data.gouv.fr](https://annuaire-entreprises.data.gouv.fr)
3. Ajouter l'entrée dans le JSON
4. Lancer `make test-regression` pour vérifier

### Catégories de difficulté et seuils

| Difficulté | Seuil minimum | Cas typique |
|---|---|---|
| `easy` | 90% | Nom exact, adresse complète, entreprise connue |
| `medium` | 60% | Nom approximatif, franchise, enseigne vs raison sociale |
| `hard` | 30% | Nom très différent, adresse partielle, micro-entreprise |
| `impossible` | 100% NON_TROUVE | Nom inventé, entreprise fermée, données incohérentes |
