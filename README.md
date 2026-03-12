# SIRET Matcher v2.0

Outil de matching SIRET haute performance pour enrichir des prospects Google Maps avec leurs données Sirene.

## Architecture

```
Prospect Google Maps
    ↓
[Étape 1] API Recherche d'Entreprises (nom + CP)         ~45%
    ↓ non trouvé
[Étape 2] API Recherche élargie (nom + département)       +8%
    ↓ non trouvé
[Étape 3] Matching par ADRESSE (BAN → base locale)        +15%
    ↓ non trouvé
[Étape 4] Fuzzy trigrams PostgreSQL (base Sirene locale)   +12%
    ↓ non trouvé
[Étape 5] Scraping mentions légales (si site web dispo)    +5%
    ↓
Résultat enrichi : SIRET, SIREN, NAF, effectif, dirigeant, OPCO...
```

**Taux de matching estimé : 75-90%**

## Prérequis VPS

- Ubuntu 24.04 (ou 22.04)
- 2 vCPU, 4 Go RAM, 25 Go SSD minimum
- PostgreSQL 16+
- Python 3.11+

Recommandé : Hetzner CX22 (~4,50€/mois) ou OVH Starter (~6€/mois)

## Installation rapide

```bash
# 1. Cloner le projet
git clone <repo> && cd siret-matcher

# 2. Setup système (PostgreSQL + extensions)
sudo bash scripts/setup_system.sh

# 3. Environnement Python
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Télécharger et importer la base Sirene (~20 min)
python scripts/import_sirene.py

# 5. Configurer
cp config/config.example.env config/.env
# Éditer config/.env avec vos clés API si nécessaire

# 6. Tester sur un fichier CSV
python -m siret_matcher.cli prospects.csv --output enriched.csv
```

## Utilisation

### Depuis un CSV
```bash
python -m siret_matcher.cli input.csv --output output.csv
```

### Depuis Google Sheets
```bash
python -m siret_matcher.cli --gsheet "ID_DU_SPREADSHEET" --sheet "Feuille 1"
```

### Colonnes d'entrée requises
| Colonne | Obligatoire | Exemple |
|---|---|---|
| nom | ✅ | Garage Le Pacha |
| adresse | ✅ | 12 Rue Paul Colonna, 20090 Ajaccio |
| code_postal | ✅ | 20090 |
| ville | ✅ | Ajaccio |
| site_web | ❌ | https://lepacha.fr |

### Colonnes de sortie ajoutées
siret, siren, denomination_sirene, naf, effectif, tranche_effectif_code,
date_creation, dirigeant, opco, source_opco, score_confiance, methode_matching

## Mise à jour de la base Sirene

La base Sirene est mise à jour mensuellement par l'INSEE. Pour rafraîchir :

```bash
python scripts/import_sirene.py --update
```

## Structure du projet

```
siret_matcher/
├── __init__.py
├── cli.py              # Point d'entrée CLI
├── matcher.py          # Pipeline de matching (orchestrateur)
├── normalizer.py       # Nettoyage noms/adresses
├── scoring.py          # Algorithmes de scoring (Levenshtein, Jaro-Winkler)
├── stages/
│   ├── __init__.py
│   ├── api_recherche.py    # Étape 1-2 : API Recherche d'Entreprises
│   ├── address_match.py    # Étape 3 : Matching par adresse (BAN + base locale)
│   ├── trigram_match.py    # Étape 4 : Fuzzy trigrams PostgreSQL
│   └── scraper.py          # Étape 5 : Scraping mentions légales
├── db.py               # Connexion PostgreSQL + requêtes
├── opco.py             # Mapping NAF/IDCC → OPCO
└── models.py           # Dataclasses Prospect / SireneResult
scripts/
├── setup_system.sh     # Installation PostgreSQL + pg_trgm
├── import_sirene.py    # Téléchargement + import base Sirene
└── update_sirene.py    # Mise à jour mensuelle
config/
├── config.example.env
tests/
├── test_matching.py    # Tests sur les 19 entreprises d'Ajaccio
requirements.txt
```
