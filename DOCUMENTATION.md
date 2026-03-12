# SIRET Matcher v2.0 — Documentation technique complète

> **Version** : 2.0
> **Serveur** : `srv910455.hstgr.cloud`
> **Port interne** : 8042
> **Runtime** : FastAPI + Uvicorn + asyncpg
> **Base de données** : PostgreSQL 16, port 5433, base `sirene`
> **Langage** : Python 3.11+

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Architecture technique](#2-architecture-technique)
3. [Installation et déploiement](#3-installation-et-déploiement)
4. [Base de données](#4-base-de-données)
5. [API — Endpoints](#5-api--endpoints)
6. [Pipeline de matching (5 étapes)](#6-pipeline-de-matching-5-étapes)
7. [Algorithmes de scoring](#7-algorithmes-de-scoring)
8. [Normalisation des données](#8-normalisation-des-données)
9. [Scripts](#9-scripts)
10. [CLI — Interface en ligne de commande](#10-cli--interface-en-ligne-de-commande)
11. [Enrichissement OPCO et conventions collectives](#11-enrichissement-opco-et-conventions-collectives)
12. [Configuration](#12-configuration)
13. [Sécurité et rate limiting](#13-sécurité-et-rate-limiting)
14. [Infrastructure et réseau](#14-infrastructure-et-réseau)
15. [Performance et capacité](#15-performance-et-capacité)
16. [Tests](#16-tests)
17. [Dépendances](#17-dépendances)
18. [Exemples d'intégration](#18-exemples-dintégration)
19. [Administration](#19-administration)

---

## 1. Vue d'ensemble

**SIRET Matcher** est une API haute performance qui enrichit des prospects Google Maps avec les données du répertoire SIRENE (INSEE). Il fait correspondre le nom, l'adresse et le code postal d'un prospect avec la base complète des établissements français pour extraire :

- **SIRET / SIREN** — identifiants légaux
- **NAF** — code activité + libellé
- **Effectif** — tranche salariale
- **Dirigeant** — nom du dirigeant
- **OPCO** — organisme paritaire de formation
- **IDCC** — code convention collective + libellé
- **Adresse SIRENE** — adresse officielle
- **Région** — région administrative

**Taux de matching estimé : 75-90%**

### Architecture du pipeline

```
Prospect Google Maps (nom, adresse, CP, ville)
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
Résultat enrichi : SIRET, SIREN, NAF, effectif, dirigeant, OPCO…
```

---

## 2. Architecture technique

### Diagramme d'architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Internet                                                       │
│                                                                 │
│  dstcampus.fr (JS client)          n8n (Docker)                 │
│       │                                 │                       │
│       │ HTTPS                           │ HTTP (bridge Docker)  │
│       ▼                                 ▼                       │
│  ┌─────────────┐                 172.17.0.1:8042                │
│  │   Traefik    │──────┐                │                       │
│  │  (port 443)  │      │                │                       │
│  └─────────────┘      │                │                       │
│                        ▼                ▼                       │
│               ┌──────────────────────────────┐                  │
│               │   FastAPI (port 8042)         │                  │
│               │                              │                  │
│               │  GET  /health                │                  │
│               │  GET  /api/dst/siret/{siret}  │  ← public       │
│               │  POST /match                  │  ← interne      │
│               │  POST /match/batch            │  ← interne      │
│               └──────────┬───────────────────┘                  │
│                          │ asyncpg (pool 2-10)                  │
│                          ▼                                      │
│               ┌──────────────────────┐                          │
│               │  PostgreSQL 16       │                          │
│               │  port 5433           │                          │
│               │                      │                          │
│               │  etablissements      │  16.7M lignes            │
│               │  siret_opco          │  3.49M lignes            │
│               │  idcc_libelles       │  490 lignes              │
│               └──────────────────────┘                          │
└─────────────────────────────────────────────────────────────────┘
```

### Composants

| Composant | Rôle | Localisation |
|-----------|------|--------------|
| **FastAPI** | API HTTP asynchrone | `/opt/siret-matcher/api.py` |
| **PostgreSQL 16** | Base SIRENE + OPCO + IDCC | Port 5433, base `sirene` |
| **Traefik v3** | Reverse proxy HTTPS + TLS auto | Docker, port 80/443 |
| **systemd** | Gestion du service | `siret-matcher.service` |
| **asyncpg** | Pool de connexions PostgreSQL | Pool min=2, max=10 |
| **slowapi** | Rate limiting par IP | 30 req/min sur `/api/dst/` |

### Structure du projet

```
/opt/siret-matcher/
├── api.py                          # Point d'entrée FastAPI (tous les endpoints)
├── requirements.txt                # Dépendances Python
├── README.md                       # Guide de démarrage rapide
├── DOC_API_DST.md                  # Documentation API détaillée
├── DOCUMENTATION.md                # Ce fichier
├── siret_matcher/                  # Package principal
│   ├── __init__.py
│   ├── __main__.py                 # Entry point (python -m siret_matcher)
│   ├── cli.py                      # Interface CLI (Click) — CSV/XLSX/Google Sheets
│   ├── models.py                   # Dataclasses : Prospect, SireneResult
│   ├── matcher.py                  # Orchestrateur du pipeline (match_one, match_batch)
│   ├── normalizer.py               # Normalisation noms/adresses + variantes
│   ├── scoring.py                  # Algorithmes de scoring (Levenshtein, Jaro-Winkler…)
│   ├── db.py                       # Pool PostgreSQL async + requêtes SQL
│   ├── opco.py                     # Mapping NAF → OPCO, enseigne → OPCO
│   ├── dst_lookups.py              # Dictionnaires département→région, NAF→libellé
│   └── stages/                     # Pipeline de matching en 5 étapes
│       ├── __init__.py
│       ├── api_recherche.py        # Étapes 1-2 : API recherche-entreprises.gouv.fr
│       ├── address_match.py        # Étape 3 : Matching par adresse (BAN API)
│       ├── trigram_match.py        # Étape 4 : Fuzzy matching PostgreSQL (pg_trgm)
│       └── scraper.py              # Étape 5 : Scraping mentions légales
├── scripts/
│   ├── import_sirene.py            # Import base Sirene (16.7M établissements)
│   └── setup_system.sh             # Installation PostgreSQL + extensions
├── config/
│   ├── config.example.env          # Template de configuration
│   └── .env                        # Configuration active (git-ignored)
└── tests/
    └── test_matching.py            # Tests sur 19 garages d'Ajaccio
```

---

## 3. Installation et déploiement

### Prérequis

- Ubuntu 24.04 (ou 22.04)
- 2 vCPU, 4 Go RAM, 25 Go SSD minimum
- PostgreSQL 16+
- Python 3.11+

Recommandé : Hetzner CX22 (~4,50€/mois) ou OVH Starter (~6€/mois)

### Installation pas à pas

```bash
# 1. Cloner le projet
git clone <repo> && cd siret-matcher

# 2. Setup système (PostgreSQL + extensions pg_trgm + unaccent)
sudo bash scripts/setup_system.sh

# 3. Environnement Python
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Configuration
cp config/config.example.env config/.env
# Éditer config/.env avec vos paramètres

# 5. Importer la base Sirene (~20-30 min)
python scripts/import_sirene.py

# 6. Lancer l'API
python api.py
# ou via systemd :
sudo systemctl start siret-matcher
```

### Service systemd

```ini
# /etc/systemd/system/siret-matcher.service
[Unit]
Description=SIRET Matcher API
After=postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=postgres
WorkingDirectory=/opt/siret-matcher
ExecStart=/opt/siret-matcher/venv/bin/python /opt/siret-matcher/api.py
Restart=always
RestartSec=5
Environment=PYTHONPATH=/opt/siret-matcher

[Install]
WantedBy=multi-user.target
```

---

## 4. Base de données

### 4.1 Table `etablissements` — 16 715 895 lignes

Source : base SIRENE de l'INSEE (stock complet des établissements français).

| Colonne | Type | Nullable | Description |
|---------|------|----------|-------------|
| **siret** | `VARCHAR(14)` | `NOT NULL` (PK) | Identifiant SIRET à 14 chiffres |
| **siren** | `VARCHAR(9)` | `NOT NULL` | Identifiant SIREN à 9 chiffres |
| denomination | `VARCHAR(200)` | oui | Raison sociale |
| denomination_usuelle | `VARCHAR(200)` | oui | Nom commercial usuel |
| enseigne | `VARCHAR(200)` | oui | Enseigne de l'établissement |
| enseigne2 | `VARCHAR(200)` | oui | Enseigne secondaire |
| naf | `VARCHAR(10)` | oui | Code NAF/APE (ex: `10.71C`) |
| numero_voie | `VARCHAR(10)` | oui | Numéro dans la voie |
| type_voie | `VARCHAR(10)` | oui | Type de voie (RUE, AV, BD…) |
| voie | `VARCHAR(200)` | oui | Nom de la voie |
| code_postal | `VARCHAR(5)` | oui | Code postal |
| commune | `VARCHAR(100)` | oui | Nom de la commune |
| tranche_effectif | `VARCHAR(5)` | oui | Code tranche effectif INSEE |
| date_creation | `VARCHAR(20)` | oui | Date de création (YYYY-MM-DD) |
| etat_administratif | `VARCHAR(1)` | oui | `A` = actif, `F` = fermé |
| departement | `VARCHAR(3)` | oui | Code département (2-3 chiffres) |
| denomination_clean | `VARCHAR(200)` | oui | Dénomination normalisée (UPPER + unaccent, pour trigrams) |
| enseigne_clean | `VARCHAR(200)` | oui | Enseigne normalisée (pour trigrams) |
| voie_clean | `VARCHAR(200)` | oui | Voie normalisée (pour trigrams) |

#### Index

| Nom | Type | Colonnes | Usage |
|-----|------|----------|-------|
| `etablissements_pkey` | btree | `siret` | Lookup direct par SIRET |
| `idx_etab_siren` | btree | `siren` | Recherche par SIREN |
| `idx_etab_cp` | btree | `code_postal` | Filtrage géographique |
| `idx_etab_dept` | btree | `departement` | Fallback départemental |
| `idx_etab_etat` | btree | `etat_administratif` | Filtrage actif/fermé |
| `idx_etab_num_voie` | btree | `numero_voie` | Matching par adresse |
| `idx_etab_cp_etat` | btree | `code_postal, etat_administratif` | Requêtes combinées |
| `idx_etab_dept_etat` | btree | `departement, etat_administratif` | Requêtes combinées |
| `idx_etab_cp_num` | btree | `code_postal, numero_voie` | Matching par adresse |
| `idx_denom_trgm` | GIN | `denomination_clean` | Recherche fuzzy par nom |
| `idx_enseigne_trgm` | GIN | `enseigne_clean` | Recherche fuzzy par enseigne |
| `idx_voie_trgm` | GIN | `voie_clean` | Recherche fuzzy par adresse |

### 4.2 Table `siret_opco` — 3 490 284 lignes

Source : France Compétences (table officielle SIRET → OPCO).

| Colonne | Type | Nullable | Description |
|---------|------|----------|-------------|
| **siret** | `VARCHAR(14)` | `NOT NULL` (PK) | SIRET de l'établissement |
| idcc | `VARCHAR(10)` | oui | Code IDCC (convention collective) |
| opco_proprietaire | `VARCHAR(100)` | oui | OPCO propriétaire (prioritaire) |
| opco_gestion | `VARCHAR(100)` | oui | OPCO de gestion (fallback) |

### 4.3 Table `idcc_libelles` — 490 lignes

Source : DILA/KALI (Journal Officiel) + compléments manuels. Couverture : 97.9%.

| Colonne | Type | Nullable | Description |
|---------|------|----------|-------------|
| **idcc** | `VARCHAR(10)` | `NOT NULL` (PK) | Code IDCC (ex: `2596`) |
| libelle | `VARCHAR(500)` | `NOT NULL` | Intitulé complet de la convention |

### 4.4 Diagramme relationnel

```
etablissements (16.7M)
    siret (PK) ──────────┐
    siren                │
    denomination         │
    enseigne             │
    naf                  │
    code_postal          │
    commune              │
    tranche_effectif     │
    date_creation        │
    ...                  │
                         │
siret_opco (3.49M)       │
    siret (PK) ──────────┘  (1:1 LEFT JOIN)
    idcc ────────────┐
    opco_proprietaire│
    opco_gestion     │
                     │
idcc_libelles (490)  │
    idcc (PK) ───────┘  (1:1 LEFT JOIN)
    libelle
```

### 4.5 Codes tranche effectif

| Code | Effectif | Code | Effectif |
|------|----------|------|----------|
| `NN` | Non renseigné | `12` | 20-49 |
| `00` | 0 salarié | `21` | 50-99 |
| `01` | 1-2 | `22` | 100-199 |
| `02` | 3-5 | `31` | 200-249 |
| `03` | 6-9 | `32` | 250-499 |
| `11` | 10-19 | `41-53` | 500+ |

---

## 5. API — Endpoints

### 5.1 `GET /health`

Health check de l'API et de la connexion base de données.

**Accès** : interne uniquement (`localhost:8042`)
**Rate limit** : aucun

**Réponse succès (200)** :
```json
{
  "status": "ok",
  "etablissements_actifs": 16715895
}
```

**Réponse erreur (200)** :
```json
{
  "status": "error",
  "detail": "connection refused"
}
```

---

### 5.2 `GET /api/dst/siret/{siret}`

Lookup SIRET pour le simulateur dstcampus.fr. Recherche directe par clé primaire avec enrichissement complet (OPCO, convention collective, libellé NAF, région).

**Accès** : public via `https://api.srv910455.hstgr.cloud/api/dst/siret/{siret}`
**Rate limit** : 30 requêtes/minute par IP
**CORS** : autorisé pour `dstcampus.fr`, `www.dstcampus.fr`, `localhost:4321`, `localhost:3000`
**Temps de réponse** : ~15ms

#### Paramètre

| Param | Type | Validation | Description |
|-------|------|------------|-------------|
| `siret` | string (path) | `^\d{14}$` | SIRET à 14 chiffres |

#### Réponse — SIRET trouvé (200)

```json
{
  "found": true,
  "siret": "97980724500019",
  "siren": "979807245",
  "denomination": "CM COIFFURE CHABOT",
  "enseigne": "AVENUE 73",
  "code_naf": "96.02A",
  "libelle_naf": "Coiffure",
  "effectif_code": "02",
  "date_creation": "2023-09-06",
  "opco": "OPCO EP",
  "source_opco": "FRANCE_COMPETENCES",
  "idcc": "2596",
  "convention_collective": "Convention collective nationale de la coiffure…",
  "adresse": "70 RUE CHABOT CHARNY",
  "code_postal": "21000",
  "ville": "DIJON",
  "region": "Bourgogne-Franche-Comté"
}
```

| Champ | Type | Source | Description |
|-------|------|--------|-------------|
| `found` | bool | — | `true` si SIRET trouvé |
| `siret` | string | `etablissements.siret` | SIRET à 14 chiffres |
| `siren` | string | `etablissements.siren` | SIREN à 9 chiffres |
| `denomination` | string | `etablissements.denomination` | Raison sociale |
| `enseigne` | string | `enseigne` ou `denomination_usuelle` | Enseigne commerciale |
| `code_naf` | string | `etablissements.naf` | Code NAF/APE |
| `libelle_naf` | string | Dictionnaire Python (~350 codes) | Libellé du code NAF |
| `effectif_code` | string | `etablissements.tranche_effectif` | Code tranche effectif |
| `date_creation` | string | `etablissements.date_creation` | Date de création |
| `opco` | string | `siret_opco` ou fallback NAF | Nom de l'OPCO |
| `source_opco` | string | — | `FRANCE_COMPETENCES` ou `NAF` |
| `idcc` | string | `siret_opco.idcc` | Code IDCC |
| `convention_collective` | string | `idcc_libelles.libelle` | Intitulé convention |
| `adresse` | string | Concaténation `numero_voie + type_voie + voie` | Adresse |
| `code_postal` | string | `etablissements.code_postal` | Code postal |
| `ville` | string | `etablissements.commune` | Commune |
| `region` | string | Dictionnaire CP→département→région | Région administrative |

#### Réponse — Non trouvé (200)

```json
{
  "found": false,
  "siret": "00000000000000",
  "message": "SIRET non trouvé dans la base SIRENE"
}
```

#### Réponse — Format invalide (400)

```json
{
  "error": "Format SIRET invalide. Le SIRET doit contenir exactement 14 chiffres."
}
```

#### Réponse — Rate limit dépassé (429)

```json
{
  "error": "Trop de requêtes. Limite : 30 par minute."
}
```

#### Requête SQL sous-jacente

```sql
SELECT e.siret, e.siren, e.denomination, e.denomination_usuelle, e.enseigne,
       e.naf, e.numero_voie, e.type_voie, e.voie, e.code_postal, e.commune,
       e.tranche_effectif, e.date_creation, e.etat_administratif,
       o.opco_proprietaire, o.opco_gestion, o.idcc,
       il.libelle AS convention_libelle
FROM etablissements e
LEFT JOIN siret_opco o ON e.siret = o.siret
LEFT JOIN idcc_libelles il ON o.idcc = il.idcc
WHERE e.siret = $1
```

---

### 5.3 `POST /match`

Matching d'un prospect vers un SIRET via le pipeline à 5 étapes.

**Accès** : interne uniquement (`172.17.0.1:8042`)
**Rate limit** : aucun (semaphores internes)

#### Corps de la requête

```json
{
  "nom": "Boulangerie Du Village",
  "adresse": "12 rue de la Paix",
  "code_postal": "75002",
  "ville": "Paris",
  "telephone": "",
  "site_web": "",
  "email": "",
  "secteur_recherche": "",
  "place_id": "",
  "rating": "",
  "avis": ""
}
```

| Champ | Requis | Description |
|-------|--------|-------------|
| `nom` | **oui** | Nom de l'entreprise |
| `code_postal` | **oui** | Code postal (5 chiffres) |
| `adresse` | non | Adresse postale |
| `ville` | non | Commune |
| `telephone` | non | Numéro de téléphone |
| `site_web` | non | URL du site (utilisé pour le scraping) |
| `email` | non | Email |
| `secteur_recherche` | non | Secteur d'activité |
| `place_id` | non | Google Place ID |
| `rating` | non | Note Google Maps |
| `avis` | non | Nombre d'avis Google |

#### Réponse (200)

```json
{
  "nom": "Boulangerie Du Village",
  "siret": "12345678901234",
  "siren": "123456789",
  "denomination": "SARL BOULANGERIE DU VILLAGE",
  "enseigne": "Boulangerie Du Village",
  "naf": "10.71C",
  "effectif": "3-5",
  "date_creation": "2015-03-12",
  "dirigeant": "JEAN DUPONT",
  "score": 85.0,
  "methode": "TRIGRAM_FUZZY",
  "opco": "OCAPIAT",
  "convention_collective": "",
  "matched": true
}
```

| Champ | Type | Description |
|-------|------|-------------|
| `score` | float | Score de confiance (0-100). ≥65 = match fiable |
| `methode` | string | Méthode de matching utilisée |
| `matched` | bool | `true` si SIRET trouvé |
| `dirigeant` | string | Nom du dirigeant (via API externe) |

#### Valeurs possibles de `methode`

| Méthode | Description | Score typique |
|---------|-------------|---------------|
| `API_RECHERCHE_EXACT` | Match exact via API gouv | 80-100 |
| `API_RECHERCHE_PROBABLE` | Match probable via API gouv | 40-65 |
| `ADDRESS_MATCH` | Match par adresse physique | 90-95 |
| `TRIGRAM_FUZZY` | Match fuzzy par trigrams | 45-85 |
| `SCRAPER_VALIDATED` | SIRET trouvé par scraping, validé en base | 80 |
| `SCRAPER_UNVALIDATED` | SIRET trouvé par scraping, non validé | 65 |
| `RADIE` | SIRET trouvé mais établissement radié | 0 |
| `NON_TROUVE` | Aucun match | 0 |

---

### 5.4 `POST /match/batch`

Matching en lot de plusieurs prospects en parallèle.

**Accès** : interne uniquement
**Rate limit** : aucun

#### Corps de la requête

```json
{
  "prospects": [
    {"nom": "Boulangerie Du Village", "code_postal": "75002", "adresse": "12 rue de la Paix"},
    {"nom": "Garage Martin", "code_postal": "69001", "adresse": "5 rue de Lyon"}
  ],
  "concurrency": 5
}
```

| Champ | Requis | Type | Défaut | Description |
|-------|--------|------|--------|-------------|
| `prospects` | **oui** | array | — | Liste de prospects (même format que `/match`) |
| `concurrency` | non | int (1-20) | 5 | Parallélisme |

#### Réponse (200)

```json
{
  "total": 2,
  "matched": 1,
  "taux": "50%",
  "results": [
    {"nom": "Boulangerie Du Village", "siret": "123…", "matched": true, "…": "…"},
    {"nom": "Garage Martin", "matched": false, "…": "…"}
  ]
}
```

---

## 6. Pipeline de matching (5 étapes)

L'orchestrateur (`matcher.py`) exécute les 5 étapes séquentiellement. Chaque étape retourne immédiatement si le score est suffisant.

### Étape 1-2 : API Recherche d'Entreprises

**Fichier** : `siret_matcher/stages/api_recherche.py`
**Source** : `https://recherche-entreprises.api.gouv.fr/search` (API publique INSEE)

**Fonctionnement** :
1. Essai de toutes les variantes du nom avec le code postal (5 variantes max)
2. Si score < 65, essai avec le département
3. Scoring composite de chaque résultat (nom + géo + adresse)

**Seuils** :
- `seuil_exact = 65` : match certain → retour immédiat
- `seuil_probable = 40` : match probable → continue vers étape 3

**Codes retour** : `API_RECHERCHE_EXACT`, `API_RECHERCHE_PROBABLE`
**Semaphore** : 5 requêtes concurrentes

### Étape 3 : Matching par adresse

**Fichier** : `siret_matcher/stages/address_match.py`

**Fonctionnement** :
1. Extraction du numéro et nom de rue (regex)
2. Si manquant, géocodage via API BAN (`adresse.data.gouv.fr`)
3. Recherche en base locale par numéro + voie + code postal
4. Si plusieurs résultats, scoring par similarité de nom

**Requête SQL** :
```sql
SELECT * FROM etablissements
WHERE etat_administratif = 'A'
  AND code_postal = $1
  AND numero_voie = $2
  AND voie_clean % $3  -- opérateur trigram
ORDER BY similarity(voie_clean, $3) DESC
LIMIT 10
```

**Codes retour** : `ADDRESS_UNIQUE` (~95 points), `ADDRESS_MULTI` (≥50 points)

**Cas particulier** : Corse (20000-20200) — gestion des codes postaux alternatifs.

### Étape 4 : Fuzzy trigrams PostgreSQL

**Fichier** : `siret_matcher/stages/trigram_match.py`

**Fonctionnement** :
1. Recherche de toutes les variantes du nom via index GIN trigram (opérateur `%`)
2. Scoring multi-métriques (Levenshtein + Jaro-Winkler + token matching)
3. Bonus si résultat unique à cette adresse/CP

**Requête SQL** :
```sql
SELECT * FROM etablissements
WHERE etat_administratif = 'A'
  AND code_postal = $1
  AND (denomination_clean % $2 OR enseigne_clean % $2)
ORDER BY GREATEST(
    similarity(denomination_clean, $2),
    similarity(enseigne_clean, $2)
) DESC
LIMIT 10
```

**Seuil** : score ≥ 45
**Code retour** : `TRIGRAM_FUZZY`

### Étape 5 : Scraping mentions légales

**Fichier** : `siret_matcher/stages/scraper.py`

**Fonctionnement** :
1. Visite des pages légales standard du site web
2. Extraction du SIRET via regex
3. Validation du checksum Luhn
4. Vérification de l'existence en base locale

**Pages testées** : `/mentions-legales`, `/legal`, `/cgu`, `/cgv`, `/a-propos`, `/qui-sommes-nous`, `/about`, `/contact`, `/infos-legales`

**Patterns regex SIRET** :
```
SIRET[:.-]?\s*(\d{3}[\s.]?\d{3}[\s.]?\d{3}[\s.]?\d{5})   # Formaté
SIREN[:.-]?\s*(\d{3}[\s.]?\d{3}[\s.]?\d{3})               # SIREN seul
\b(\d{14})\b                                               # 14 chiffres bruts
```

**Codes retour** : `SCRAPE_MENTIONS_LEGALES` (score=80), `SCRAPE_NON_VALIDE` (score=65)
**Semaphore** : 10 requêtes concurrentes

---

## 7. Algorithmes de scoring

**Fichier** : `siret_matcher/scoring.py`

### Métriques de similarité de chaînes

| Métrique | Poids | Utilité |
|----------|-------|---------|
| **Levenshtein** | 0.30 | Distance d'édition normalisée |
| **Jaro-Winkler** | 0.20 | Emphase sur les préfixes (fautes de frappe) |
| **Token Sort Ratio** | 0.25 | Réordonne les mots ("PACHA LE" ↔ "LE PACHA") |
| **Token Set Ratio** | 0.15 | Ignore les sous-ensembles communs |
| **Partial Ratio** | variable | Meilleur sous-chaîne match |
| **Common Words** | 0.10 | Pourcentage de mots distinctifs en commun |

Bibliothèque utilisée : `rapidfuzz` (implémentation C++ rapide).

### Score composite par nom (0-50 points)

```python
sim = max(
    lev * 0.3 + jw * 0.2 + tsr * 0.25 + tset * 0.15 + cw * 0.1,  # Équilibré
    tset * 0.5 + pr * 0.3 + cw * 0.2,                              # Bias franchises
    pr * 0.4 + tsr * 0.3 + cw * 0.3,                               # Bias noms courts
)

≥ 0.85 → 50 pts | ≥ 0.70 → 40 pts | ≥ 0.55 → 30 pts
≥ 0.40 → 20 pts | ≥ 0.25 → 10 pts | < 0.25 → 0 pts
```

### Score géographique (0-30 points)

| Condition | Points |
|-----------|--------|
| Code postal identique | 30 |
| Même département | 15 |
| Même préfixe 2 chiffres | 15 |
| Différent | 0 |

### Score adresse (0-20 points)

| Condition | Points |
|-----------|--------|
| Numéro de rue identique | +12 |
| Similarité voie ≥ 0.7 | +8 |
| Similarité voie ≥ 0.5 | +4 |

### Composition totale (0-100)

```
Étapes 1-2 (API)   : nom(50) + géo(30) + adresse(20) = 0-100
Étape 3 (Adresse)   : géo(30) + adresse(20) + nom(optionnel) = 30-95 + bonus
Étape 4 (Trigram)   : nom(50) + géo(30) + adresse(20) + bonus(5) = 0-105
Étape 5 (Scraping)  : fixe 80 (validé) ou 65 (non validé)
```

---

## 8. Normalisation des données

**Fichier** : `siret_matcher/normalizer.py`

### Pipeline de nettoyage des noms

```
1. strip_accents()        → "Café" → "Cafe"
2. normalize_base()       → Majuscules, suppression caractères spéciaux
3. split_franchise()      → "SPEEDY - Local" → "Local"
4. extract_parentheses()  → "Name (Local)" → "Local"
5. remove_words()         → Suppression marques, formes juridiques, articles
6. clean_name()           → Nettoyage final
```

### Listes de mots filtrés

| Catégorie | Exemples | Nombre |
|-----------|----------|--------|
| **Franchises** | SPEEDY, NORAUTO, POINT S, EUROTYRE… | 24 |
| **Marques auto** | RENAULT, PEUGEOT, TOYOTA, BMW… | 27 |
| **Formes juridiques** | SARL, SAS, SASU, SA, EURL, SCI… | ~10 |
| **Articles** | LE, LA, LES, DU, DES, DE… | ~10 |
| **Mots génériques** | GARAGE, AUTO, PNEUS, SERVICE, MOTO… | ~15 |

### Génération de variantes

Pour `"Point S - Ajaccio (Auto Pneus Services Ajaccio)"` :

1. **Contenu des parenthèses** (priorité) : `AUTO PNEUS SERVICES`
2. **Nettoyé complet** : `POINT S AJACCIO AUTO PNEUS SERVICES`
3. **Partie franchise** : `AUTO PNEUS SERVICES AJACCIO`
4. **Mots distinctifs** : `AUTO PNEUS SERVICES`

Chaque étape du pipeline teste 4-5 variantes séquentiellement (arrêt dès score ≥ 65).

### Normalisation des adresses

```
"32 Avenue du Docteur Paul" → numero="32", voie="AV DOCTEUR PAUL"
```

**Abréviations** : RUE→R, AVENUE→AV, BOULEVARD→BD, IMPASSE→IMP, CHEMIN→CH, ROUTE→RTE, PLACE→PL, ALLEE→ALL

---

## 9. Scripts

### 9.1 `scripts/import_sirene.py` — Import base Sirene

Télécharge et importe le stock complet des établissements INSEE dans PostgreSQL.

#### Usage

```bash
python scripts/import_sirene.py                # Import complet
python scripts/import_sirene.py --update       # Re-télécharge + remplace
python scripts/import_sirene.py --skip-download  # Utilise le CSV local existant
python scripts/import_sirene.py --indexes-only   # Recrée uniquement les index
```

#### Étapes exécutées

| Étape | Durée estimée | Description |
|-------|---------------|-------------|
| 1. Téléchargement | 5-15 min | `StockEtablissement_utf8.zip` (~2 Go) depuis data.gouv.fr |
| 2. Décompression | 1-2 min | Extraction du CSV (~4 Go) |
| 3. Création table | < 1 s | `DROP` + `CREATE TABLE etablissements` |
| 4. Import CSV | 10-20 min | Streaming CSV → fichier nettoyé → `COPY` PostgreSQL |
| 5. Colonnes _clean | 5-10 min | `UPDATE` avec `UPPER(unaccent(...))` + nettoyage regex |
| 6. Index | 5-10 min | 8 btree + 3 GIN trigram |
| 7. Vérification | < 1 s | COUNT + test trigram |

**Total : ~25-40 minutes**

#### Colonnes extraites du CSV INSEE

Le script extrait 15 colonnes sur ~40 disponibles : `siret`, `siren`, `denominationUniteLegale`, `denominationUsuelle1UniteLegale`, `enseigne1Etablissement`, `enseigne2Etablissement`, `activitePrincipaleEtablissement`, `numeroVoieEtablissement`, `typeVoieEtablissement`, `libelleVoieEtablissement`, `codePostalEtablissement`, `libelleCommuneEtablissement`, `trancheEffectifsEtablissement`, `dateCreationEtablissement`, `etatAdministratifEtablissement`.

#### Colonnes calculées

- **departement** : déduit du code postal (gestion spéciale Corse 2A/2B et DOM 97x)
- **denomination_clean** : `UPPER(unaccent(denomination + denomination_usuelle))` + suppression caractères spéciaux
- **enseigne_clean** : `UPPER(unaccent(enseigne + enseigne2))` + suppression caractères spéciaux
- **voie_clean** : `UPPER(unaccent(voie))` + suppression caractères spéciaux

#### Configuration

Variables d'environnement (ou valeurs par défaut) :

| Variable | Défaut | Description |
|----------|--------|-------------|
| `DB_NAME` | `sirene` | Nom de la base |
| `DB_USER` | `sirene_user` | Utilisateur PostgreSQL |
| `DB_PASSWORD` | `sirene_pass` | Mot de passe |
| `DB_HOST` | `localhost` | Hôte PostgreSQL |
| `DB_PORT` | `5432` | Port PostgreSQL |

---

### 9.2 `scripts/setup_system.sh` — Installation système

Installe et configure PostgreSQL et les prérequis pour le projet.

#### Usage

```bash
sudo bash scripts/setup_system.sh
```

#### Actions effectuées

1. **Installation** de PostgreSQL 16 + contrib + libpq-dev
2. **Installation** de python3-venv, python3-dev, wget, unzip
3. **Démarrage** de PostgreSQL (systemd enable + start)
4. **Création** de l'utilisateur `sirene_user` (si inexistant)
5. **Création** de la base `sirene` (si inexistante)
6. **Activation** des extensions `pg_trgm` et `unaccent`
7. **Attribution** des privilèges à `sirene_user`
8. **Optimisation** PostgreSQL :
   - `work_mem = 256MB`
   - `maintenance_work_mem = 512MB`
   - `effective_cache_size = 2GB`
   - `pg_trgm.similarity_threshold = 0.2`
9. **Redémarrage** de PostgreSQL

---

## 10. CLI — Interface en ligne de commande

**Fichier** : `siret_matcher/cli.py`
**Framework** : Click

### Usage

```bash
# Depuis un CSV
python -m siret_matcher.cli prospects.csv -o enriched.csv

# Depuis un Excel
python -m siret_matcher.cli prospects.xlsx -o enriched.xlsx -v

# Depuis Google Sheets
python -m siret_matcher.cli --gsheet "1ABC...xyz" --sheet "Prospects"

# Options complètes
python -m siret_matcher.cli INPUT_FILE [OPTIONS]
```

### Options

| Option | Court | Défaut | Description |
|--------|-------|--------|-------------|
| `--output` | `-o` | `{input}_enriched.{ext}` | Fichier de sortie (CSV ou XLSX) |
| `--gsheet` | — | — | ID du Google Spreadsheet |
| `--sheet` | — | `Feuille 1` | Nom de la feuille Google Sheets |
| `--no-db` | — | `false` | Désactiver les étapes 3-4 (API + scraping uniquement) |
| `--concurrency` | `-c` | `5` | Nombre de matchs parallèles |
| `--verbose` | `-v` | `false` | Mode debug |
| `--limit` | `-n` | `0` | Limiter le nombre de prospects (0 = tous) |

### Colonnes d'entrée (auto-détectées)

| Champ | Noms acceptés | Obligatoire |
|-------|---------------|-------------|
| nom | `nom`, `name`, `raison_sociale`, `entreprise` | **oui** |
| adresse | `adresse`, `address`, `adresse_complete` | non |
| code_postal | `code_postal`, `cp`, `postal_code`, `zip` | **oui** |
| ville | `ville`, `city`, `commune` | non |
| departement | `departement`, `dept`, `department` | non |
| telephone | `telephone`, `tel`, `phone` | non |
| site_web | `site_web`, `website`, `url`, `site` | non |
| email | `email`, `mail`, `e-mail` | non |
| secteur | `secteur_recherche`, `secteur`, `type`, `category` | non |
| place_id | `place_id` | non |
| rating | `rating`, `note` | non |
| avis | `avis`, `reviews`, `nb_avis` | non |

### Colonnes de sortie ajoutées

```
siret, siren, denomination_sirene, enseigne_sirene, naf,
effectif, tranche_effectif_code, date_creation, dirigeant,
opco, source_opco, idcc, convention_collective,
score_confiance, methode_matching, statut_prospection
```

### Google Sheets

Nécessite un fichier `config/service_account.json` avec les credentials OAuth2 Google.

- **Import** : charge la feuille spécifiée en tant que DataFrame
- **Export** : crée une nouvelle feuille `{nom} Enrichi` avec les résultats

---

## 11. Enrichissement OPCO et conventions collectives

**Fichier** : `siret_matcher/opco.py`

### Stratégies de détection OPCO (par ordre de priorité)

#### 1. France Compétences (table `siret_opco`)
- **Source** : `source_opco = "FRANCE_COMPETENCES"`
- **Couverture** : 3.49M SIRET
- **Fiabilité** : 100% (donnée officielle)
- Utilise `opco_proprietaire` en priorité, puis `opco_gestion` en fallback

#### 2. Mapping par code NAF
- **Source** : `source_opco = "NAF"`
- **Couverture** : ~60 codes NAF → 11 OPCOs
- **Fiabilité** : ~80%

| Préfixe NAF | OPCO | Secteurs |
|-------------|------|----------|
| 01, 02, 03, 10, 11, 75 | OCAPIAT | Agriculture, pêche, agroalimentaire |
| 24-30, 33 | OPCO 2i | Industrie, chimie, métallurgie |
| 41, 42, 43 | CONSTRUCTYS | BTP |
| 45, 49-53 | OPCO Mobilités | Automobile, transports |
| 46, 47 | OPCOMMERCE | Commerce de gros et détail |
| 55, 56, 81, 85 | AKTO | Hôtellerie, restauration, services |
| 58-61, 90-93 | AFDAS | Culture, médias, télécoms |
| 62-66, 69-74, 78 | ATLAS | Numérique, conseil, finance |
| 68, 96 | OPCO EP | Immobilier, services à la personne |
| 86, 87, 88 | OPCO Santé | Santé, médico-social |

#### 3. Mapping par enseigne (endpoint `/match` uniquement)
- **Source** : `source_opco = "ENSEIGNE"`
- Matching du nom sur ~30 enseignes connues (Speedy, Midas, Renault…)

### Convention collective

```
IDCC trouvé dans siret_opco ?
  ├── OUI → Jointure sur idcc_libelles → libellé complet (490 IDCC, couverture 97.9%)
  └── NON → convention_collective = "" (vide)
```

---

## 12. Configuration

**Fichier** : `config/.env` (copié depuis `config/config.example.env`)

```env
# PostgreSQL
DB_HOST=localhost
DB_PORT=5432
DB_NAME=sirene
DB_USER=sirene_user
DB_PASSWORD=sirene_pass

# Rate limits
API_RECHERCHE_RPS=6              # Requêtes par seconde (API gouv)
API_INSEE_RPM=28                 # Requêtes par minute
SCRAPING_CONCURRENT=10           # Scrapes parallèles max

# Seuils de scoring (0-100)
SEUIL_EXACT=65                   # Seuil API (match certain)
SEUIL_PROBABLE=40                # Seuil API (match probable)
SEUIL_ADDRESS=50                 # Seuil matching adresse
SEUIL_TRIGRAM=45                 # Seuil matching trigram

# Google Sheets (optionnel)
# GOOGLE_SERVICE_ACCOUNT_FILE=config/service_account.json
```

---

## 13. Sécurité et rate limiting

### Rate limiting

| Endpoint | Limite | Implémentation |
|----------|--------|----------------|
| `GET /api/dst/siret/{siret}` | 30 req/min par IP | slowapi |
| `GET /health` | Aucune | — |
| `POST /match` | Aucune | Semaphores internes |
| `POST /match/batch` | Aucune | Semaphores internes |

### Semaphores internes

| Ressource | Limite | Raison |
|-----------|--------|--------|
| API Recherche d'Entreprises | 5 concurrents | Respect du rate limit API gouv |
| Base de données locale | 20 concurrents | Protection du pool asyncpg |
| Scraping web | 10 concurrents | Politesse crawling |

### Exposition réseau

| Endpoint | Réseau | URL |
|----------|--------|-----|
| `/api/dst/*` | Internet (HTTPS via Traefik) | `https://api.srv910455.hstgr.cloud/api/dst/siret/{siret}` |
| `/match`, `/match/batch` | Bridge Docker uniquement | `http://172.17.0.1:8042/match` |
| `/health` | Localhost + Docker | `http://localhost:8042/health` |

### Sécurité

- **Validation SIRET** : regex `^\d{14}$` — rejet immédiat (400) si non conforme
- **SQL** : requêtes paramétrées (`$1`) — aucune injection possible
- **Base** : accès lecture seule (aucun `INSERT/UPDATE/DELETE` sur les tables métier)
- **CORS** : origines whitelist uniquement
- **TLS** : certificat Let's Encrypt via Traefik (renouvellement automatique)

---

## 14. Infrastructure et réseau

### Traefik — Exposition publique

```yaml
# /root/traefik-dynamic/siret-api.yml
http:
  routers:
    siret-api:
      rule: "Host(`api.srv910455.hstgr.cloud`) && PathPrefix(`/api/dst/`)"
      entryPoints:
        - websecure
      service: siret-api
      tls:
        certResolver: mytlschallenge

  services:
    siret-api:
      loadBalancer:
        servers:
          - url: "http://host.docker.internal:8042"
```

- Seul le path `/api/dst/` est exposé publiquement
- `/match` et `/match/batch` restent accessibles uniquement via le bridge Docker
- TLS automatique via Let's Encrypt (ACME)

### CORS

```
Origines autorisées :
  - https://dstcampus.fr
  - https://www.dstcampus.fr
  - http://localhost:4321  (dev Astro)
  - http://localhost:3000  (dev local)

Méthodes : GET uniquement
Credentials : non
Headers : tous acceptés
```

---

## 15. Performance et capacité

### Temps de réponse

| Opération | Temps |
|-----------|-------|
| Lookup SIRET (`/api/dst/siret/{siret}`) | ~15 ms |
| Match unique (`/match`) | 2-5 secondes |
| Batch 100 prospects (10 concurrents) | ~30-50 secondes |
| Capacité journalière (24h, 5 concurrents) | ~43 000 prospects |

### Performance base de données

| Requête | Temps |
|---------|-------|
| Lookup SIRET (PK btree) | < 1 ms |
| Recherche par adresse (CP + numéro btree + voie trigram) | 10-50 ms |
| Recherche fuzzy trigram (GIN) | 50-200 ms |

### Ressources

| Composant | Mémoire |
|-----------|---------|
| Processus Python | 150-300 Mo |
| PostgreSQL (shared buffers + cache) | 2-4 Go |
| Pool connexions asyncpg | 2-10 connexions |

---

## 16. Tests

**Fichier** : `tests/test_matching.py`

### Dataset de test

19 garages à Ajaccio (Corse, codes 20090/20167) — choisis pour leur complexité :

- Apostrophes : `Cors'Auto`
- Franchises : `Eurotyre - Garage 2A Pneus`
- Parenthèses : `Point S - Ajaccio (Auto Pneus Services Ajaccio)`
- Noms non distinctifs : `Speedy` (match par adresse uniquement)

### Exécution

```bash
cd /opt/siret-matcher
source venv/bin/activate
python -m pytest tests/ -v
```

### Tests couverts

1. **Normalisation** : validation de la normalisation des 19 noms
2. **Cas critiques** : apostrophes, franchises, parenthèses
3. **Pipeline complet** : matching bout en bout (nécessite PostgreSQL)

**Taux de matching attendu** : 75-90% sur les 19 établissements.

---

## 17. Dépendances

**Fichier** : `requirements.txt`

| Package | Version | Usage |
|---------|---------|-------|
| `httpx` | ≥0.27 | Client HTTP async (API gouv, BAN, scraping) |
| `asyncpg` | ≥0.29 | Driver PostgreSQL async (bas niveau, performant) |
| `rapidfuzz` | ≥3.6 | Similarité de chaînes (Levenshtein, Jaro-Winkler) |
| `pandas` | ≥2.1 | I/O CSV et Excel |
| `python-dotenv` | ≥1.0 | Chargement config `.env` |
| `beautifulsoup4` | ≥4.12 | Parsing HTML (scraping) |
| `gspread` | ≥6.0 | API Google Sheets |
| `google-auth` | ≥2.27 | Authentification OAuth2 |
| `tqdm` | ≥4.66 | Barres de progression |
| `click` | ≥8.1 | Framework CLI |
| `fastapi` | — | Framework web API |
| `uvicorn` | — | Serveur ASGI |
| `slowapi` | — | Rate limiting par IP |
| `pydantic` | — | Validation des données |

---

## 18. Exemples d'intégration

### cURL — Lookup SIRET

```bash
# Lookup simple
curl -s "https://api.srv910455.hstgr.cloud/api/dst/siret/97980724500019"

# Test format invalide
curl -s "https://api.srv910455.hstgr.cloud/api/dst/siret/123"
```

### cURL — Matching interne

```bash
# Match unique
curl -X POST http://172.17.0.1:8042/match \
  -H "Content-Type: application/json" \
  -d '{"nom":"Boulangerie Martin","adresse":"12 rue de la Paix","code_postal":"75002","ville":"Paris"}'

# Match batch
curl -X POST http://172.17.0.1:8042/match/batch \
  -H "Content-Type: application/json" \
  -d '{"prospects":[{"nom":"Restaurant A","code_postal":"75001"},{"nom":"Restaurant B","code_postal":"75002"}],"concurrency":5}'
```

### JavaScript (dstcampus.fr)

```javascript
async function lookupSiret(siret) {
  const res = await fetch(`https://api.srv910455.hstgr.cloud/api/dst/siret/${siret}`);
  if (res.status === 400) throw new Error((await res.json()).error);
  if (res.status === 429) throw new Error("Rate limit dépassé");
  const data = await res.json();
  return data.found ? data : null;
}
```

### n8n (workflow Docker)

```json
{
  "method": "POST",
  "url": "http://172.17.0.1:8042/match",
  "headers": {"Content-Type": "application/json"},
  "body": {
    "nom": "{{ $json.nom }}",
    "adresse": "{{ $json.adresse }}",
    "code_postal": "{{ $json.code_postal }}",
    "ville": "{{ $json.ville }}"
  }
}
```

### CLI — Fichier CSV

```bash
cd /opt/siret-matcher && source venv/bin/activate
python -m siret_matcher.cli prospects.csv -o enriched.csv -v
```

### CLI — Google Sheets

```bash
python -m siret_matcher.cli --gsheet "1AbCdEfGhIjKlMnOpQrStUvWxYz" --sheet "Prospects"
# → Crée une feuille "Prospects Enrichi" avec les résultats
```

---

## 19. Administration

### Commandes courantes

```bash
# Statut du service
sudo systemctl status siret-matcher

# Redémarrer
sudo systemctl restart siret-matcher

# Logs en temps réel
journalctl -u siret-matcher -f --no-pager

# Health check
curl -s http://localhost:8042/health | python3 -m json.tool
```

### Connexion à la base

```bash
sudo -u postgres psql -p 5433 -d sirene
```

### Mise à jour de la base Sirene

La base est mise à jour mensuellement par l'INSEE.

```bash
python scripts/import_sirene.py --update
```

### Gestion des conventions collectives (IDCC)

```sql
-- Ajouter un IDCC
INSERT INTO idcc_libelles VALUES ('1234', 'Nouvelle convention')
ON CONFLICT (idcc) DO UPDATE SET libelle = EXCLUDED.libelle;

-- IDCC manquants les plus fréquents
SELECT o.idcc, COUNT(*) as nb
FROM siret_opco o
LEFT JOIN idcc_libelles il ON o.idcc = il.idcc
WHERE o.idcc IS NOT NULL AND o.idcc <> '' AND il.idcc IS NULL
GROUP BY o.idcc ORDER BY nb DESC LIMIT 20;
```

### Vérification de l'état de la base

```sql
-- Nombre d'établissements
SELECT COUNT(*) FROM etablissements;
SELECT COUNT(*) FROM etablissements WHERE etat_administratif = 'A';

-- Taille de la base
SELECT pg_size_pretty(pg_total_relation_size('etablissements'));

-- Couverture OPCO
SELECT COUNT(*) FROM siret_opco;

-- Couverture IDCC
SELECT COUNT(*) FROM idcc_libelles;
```
