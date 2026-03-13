# SIRET Matcher — Architecture technique

> Document de transfert de compétences — Mars 2026
> Public : Équipe Scrum v3

---

## 1. Vue d'ensemble

```
                          Internet
                             │
                             ▼
                    ┌─────────────────┐
                    │    Traefik       │  Docker (ports 80/443)
                    │  Let's Encrypt   │  TLS auto-renew
                    └────────┬────────┘
                             │ HTTP (reverse proxy)
                             ▼
                    ┌─────────────────┐
                    │  FastAPI/Uvicorn │  systemd (port 8042)
                    │   api.py         │  user: postgres
                    └────────┬────────┘
                             │ asyncpg (socket Unix)
                             ▼
                    ┌─────────────────┐
                    │  PostgreSQL 16   │  port 5433
                    │  base: sirene    │  ~8.4 GB (données + index)
                    └─────────────────┘
```

**Stack** : Python 3.11 · FastAPI · asyncpg · PostgreSQL 16 (pg_trgm) · Traefik · systemd

---

## 2. Arborescence du projet

```
/opt/siret-matcher/
├── api.py                          # Point d'entrée FastAPI (311 lignes)
├── requirements.txt                # Dépendances Python
├── siret-matcher.service           # Fichier systemd
├── config/
│   └── .env                        # Config BDD (host, port, user)
├── venv/                           # Environnement virtuel Python
├── docs/                           # Documentation (ce dossier)
└── siret_matcher/                  # Package Python principal
    ├── __init__.py                 # Version 2.0.0
    ├── __main__.py                 # Entry point CLI
    ├── models.py                   # Dataclasses Prospect + SireneResult
    ├── matcher.py                  # Orchestrateur pipeline 5 étapes
    ├── normalizer.py               # Nettoyage texte + parsing adresse
    ├── scoring.py                  # Algorithmes de scoring composite
    ├── db.py                       # Couche d'accès PostgreSQL (asyncpg)
    ├── opco.py                     # Mappings NAF→OPCO, enseigne→OPCO
    ├── dst_lookups.py              # Dictionnaires IDCC, NAF, départements
    ├── cli.py                      # Interface ligne de commande (click)
    ├── search_router.py            # Router FastAPI /search/*
    ├── search_models.py            # Modèles Pydantic pour /search
    └── stages/                     # Les 5 étapes du matching
        ├── api_recherche.py        # Étapes 1-2 : API gouv.fr
        ├── address_match.py        # Étape 3 : BAN + BDD locale
        ├── trigram_match.py        # Étape 4 : pg_trgm fuzzy
        └── scraper.py              # Étape 5 : scraping web
```

---

## 3. Base de données

### 3.1 Serveur

| Paramètre | Valeur |
|-----------|--------|
| SGBD | PostgreSQL 16 |
| Port | 5433 |
| Base | `sirene` |
| Authentification | Peer (socket Unix `/run/postgresql`) |
| User | `postgres` |
| Extensions | `pg_trgm` (recherche floue par trigrammes) |

### 3.2 Tables

#### `etablissements` — 16.7M lignes, 5.1 GB

Répertoire SIRENE complet (tous les établissements actifs en France).

| Colonne | Type | Description |
|---------|------|-------------|
| `siret` | text PK | Identifiant unique établissement (14 chiffres) |
| `siren` | text NOT NULL | Identifiant entreprise (9 chiffres) |
| `denomination` | text | Raison sociale |
| `denomination_usuelle` | text | Nom d'usage |
| `enseigne` | text | Enseigne commerciale |
| `enseigne2` | text | Enseigne secondaire |
| `naf` | text | Code NAF/APE (ex. "62.02A") |
| `numero_voie` | text | Numéro de rue |
| `type_voie` | text | Type de voie (RUE, AV, BD…) |
| `voie` | text | Nom de la voie |
| `code_postal` | text | Code postal (5 chiffres) |
| `commune` | text | Nom de la commune |
| `tranche_effectif` | text | Code INSEE effectif salarié |
| `date_creation` | text | Date de création (YYYY-MM-DD) |
| `etat_administratif` | text | "A" (actif) ou "F" (fermé) |
| `departement` | text | Code département |
| `denomination_clean` | text | Nom nettoyé (pour matching) |
| `enseigne_clean` | text | Enseigne nettoyée (pour matching) |
| `voie_clean` | text | Voie nettoyée (pour matching) |

**Index** (total ~3 GB) :

| Index | Type | Colonnes | Taille | Usage |
|-------|------|----------|--------|-------|
| `etablissements_pkey` | btree | siret | 1006 MB | Lookup par SIRET |
| `idx_siren` | btree | siren | 473 MB | Lookup par SIREN |
| `idx_denom_trgm` | GIN | denomination_clean | 412 MB | Recherche floue nom |
| `idx_voie_trgm` | GIN | voie_clean | 385 MB | Recherche floue voie |
| `idx_enseigne_trgm` | GIN | enseigne_clean | 90 MB | Recherche floue enseigne |
| `idx_cp` | btree | code_postal | 112 MB | Filtrage par CP |
| `idx_cp_num` | btree | code_postal, numero_voie | 140 MB | Match adresse |
| `idx_dept` | btree | departement | 111 MB | Filtrage par département |
| `idx_etab_actif_siret_dept_eff` | btree | siret, dept, effectif (WHERE actif) | 648 MB | Search filtré |
| `idx_etab_dept_etat_effectif` | btree | dept, etat, effectif | 111 MB | Search filtré |
| `idx_etab_dept_etat_naf` | btree | dept, etat, naf | 116 MB | Search filtré NAF |

#### `siret_opco` — 3.49M lignes, 206 MB

Mapping SIRET → OPCO issu de France Compétences.

| Colonne | Type | Description |
|---------|------|-------------|
| `siret` | text PK | SIRET de l'établissement |
| `idcc` | text | Code IDCC (convention collective) |
| `opco_proprietaire` | text | OPCO propriétaire |
| `opco_gestion` | text | OPCO de gestion |

**Index** :

| Index | Colonnes | Taille |
|-------|----------|--------|
| `siret_opco_pkey` | siret | 136 MB |
| `idx_opco_siret` | siret | 105 MB |
| `idx_opco_idcc` | idcc | 23 MB |
| `idx_siret_opco_idcc_siret` | idcc, siret | 135 MB |

#### `idcc_libelles` — 490 lignes, 120 KB

Référentiel des conventions collectives.

| Colonne | Type | Description |
|---------|------|-------------|
| `idcc` | text PK | Code IDCC (4 chiffres, ex. "1486") |
| `libelle` | text NOT NULL | Intitulé complet de la convention |

---

## 4. API — Endpoints

### 4.1 `GET /api/dst/siret/{siret}`

Lookup direct d'un SIRET pour le simulateur DST Campus.

- **Rate limit** : 30 req/min
- **Routing Traefik** : `Host(api.srv910455.hstgr.cloud) && PathPrefix(/api/dst/)`
- **CORS** : `Access-Control-Allow-Origin: *`

**Requête** :
```
GET /api/dst/siret/44306184100047
```

**Réponse (200)** :
```json
{
  "found": true,
  "siret": "44306184100047",
  "siren": "443061841",
  "denomination": "GOOGLE FRANCE",
  "enseigne": "",
  "code_naf": "62.02A",
  "libelle_naf": "Conseil en systèmes et logiciels informatiques",
  "effectif_code": "42",
  "date_creation": "2011-05-13",
  "opco": "ATLAS",
  "source_opco": "FRANCE_COMPETENCES",
  "idcc": "1486",
  "convention_collective": "Convention collective nationale des bureaux d'études techniques…",
  "adresse": "8 RUE DE LONDRES",
  "code_postal": "75009",
  "ville": "PARIS",
  "region": "Île-de-France"
}
```

**Réponse (SIRET non trouvé)** :
```json
{ "found": false, "siret": "12345678901234" }
```

**Réponse (400)** :
```json
{ "detail": "SIRET invalide : doit contenir exactement 14 chiffres" }
```

### 4.2 `POST /match`

Matching intelligent d'un prospect unique.

**Requête** :
```json
{
  "nom": "Google France",
  "adresse": "8 rue de Londres",
  "code_postal": "75009",
  "ville": "Paris",
  "telephone": "",
  "site_web": "",
  "email": ""
}
```

**Réponse** :
```json
{
  "matched": true,
  "score": 85,
  "methode": "API_RECHERCHE_EXACT",
  "siret": "44306184100047",
  "siren": "443061841",
  "denomination": "GOOGLE FRANCE",
  "naf": "62.02A",
  "effectif": "250-499",
  "opco": "ATLAS",
  "source_opco": "FRANCE_COMPETENCES",
  "idcc": "1486",
  "convention_collective": "…",
  "adresse": "8 RUE DE LONDRES",
  "code_postal": "75009",
  "ville": "PARIS"
}
```

### 4.3 `POST /match/batch`

Matching de plusieurs prospects en parallèle.

**Requête** :
```json
{
  "prospects": [
    { "nom": "Google France", "code_postal": "75009", "ville": "Paris" },
    { "nom": "Microsoft France", "code_postal": "92130", "ville": "Issy-les-Moulineaux" }
  ],
  "concurrency": 5
}
```

**Réponse** :
```json
{
  "total": 2,
  "matched": 2,
  "taux": "100%",
  "results": [ ... ]
}
```

### 4.4 `POST /search/prospects`

Recherche avancée avec filtres.

**Requête** :
```json
{
  "departements": ["75", "92"],
  "taille": "PLUS_DE_50",
  "idcc": "1486",
  "naf": "62",
  "limit": 100,
  "offset": 0
}
```

### 4.5 `POST /search/prospects/count`

Identique mais retourne uniquement le compteur (optimisé).

### 4.6 `GET /search/regions`

Retourne le mapping régions → départements pour les formulaires de filtrage.

### 4.7 `GET /search/idcc`

Liste tous les IDCC avec libellé et nombre d'établissements associés.

### 4.8 `GET /health`

Vérifie la connectivité BDD et retourne le nombre d'établissements actifs.

---

## 5. Pipeline de matching — Implémentation

### 5.1 Normalisation (`normalizer.py`)

Avant toute recherche, le prospect est normalisé :

1. **Nom** : suppression accents, formes juridiques (SARL, SAS…), articles, mots génériques (GARAGE, AUTO…), noms de franchises connues (18 marques)
2. **Variantes** : jusqu'à 4 variantes générées (nom complet, contenu entre parenthèses, partie non-franchise, mots distinctifs)
3. **Adresse** : extraction n° + voie, abréviation types de voie (RUE→R, AVENUE→AV…)

### 5.2 Étape 1-2 : API Recherche (`stages/api_recherche.py`)

- **API** : `https://recherche-entreprises.api.gouv.fr/search`
- **Paramètres** : `q={variante}&code_postal={cp}` puis `q={variante}&departement={dept}`
- **Sémaphore** : max 5 appels simultanés
- **Scoring** : composite nom + géo + adresse
- **Seuils** : ≥ 65 → EXACT, ≥ 40 → PROBABLE

### 5.3 Étape 3 : Match adresse (`stages/address_match.py`)

- **API BAN** : `https://api-adresse.data.gouv.fr/search/` pour normaliser l'adresse
- **BDD** : `SELECT * WHERE numero_voie = $1 AND voie_clean % $2 AND code_postal = $3`
- **Sémaphore** : max 20 requêtes
- **Logique** : 1 résultat = score ~95, plusieurs = scoring par nom

### 5.4 Étape 4 : Fuzzy trigrammes (`stages/trigram_match.py`)

- **SQL** : `WHERE denomination_clean % $1 AND code_postal = $2` (opérateur pg_trgm `%`)
- **Sémaphore** : max 20 requêtes
- **Fallback** : si rien par CP, essai par département
- **Seuil** : ≥ 45

### 5.5 Étape 5 : Scraping (`stages/scraper.py`)

- **Pages crawlées** : `/mentions-legales`, `/legal`, `/cgu`, `/a-propos`, `/contact`, homepage
- **Extraction** : regex SIRET/SIREN + validation Luhn
- **Vérification** : existence du SIRET en BDD locale
- **Sémaphore** : max 10 scrapes

### 5.6 Concurrence et sémaphores

```
SEM_API    = asyncio.Semaphore(5)    # Appels API externes
SEM_DB     = asyncio.Semaphore(20)   # Requêtes PostgreSQL
SEM_SCRAPE = asyncio.Semaphore(10)   # Scraping web
```

Ces sémaphores protègent les ressources partagées lors du traitement batch.

---

## 6. Enrichissement OPCO

Trois niveaux de fallback, dans l'ordre :

| Priorité | Source | Couverture | Clé de lookup |
|----------|--------|------------|---------------|
| 1 | Table `siret_opco` (France Compétences) | 3.49M SIRET | SIRET exact |
| 2 | Dictionnaire `NAF_TO_OPCO` | Tous les NAF 2 chiffres | Préfixe NAF |
| 3 | Dictionnaire `ENSEIGNE_TO_OPCO` | 44 enseignes connues | Nom d'enseigne |

Les 11 OPCOs couverts : OCAPIAT, OPCO 2i, CONSTRUCTYS, OPCO Mobilités, OPCOMMERCE, AKTO, AFDAS, ATLAS, OPCO EP, OPCO Santé, Uniformation.

---

## 7. Dépendances Python

```
fastapi >= 0.104          # Framework HTTP async
uvicorn >= 0.24           # Serveur ASGI
asyncpg >= 0.29           # Driver PostgreSQL async
slowapi >= 0.1.5          # Rate limiting
httpx >= 0.27             # Client HTTP async (pour APIs externes)
rapidfuzz >= 3.6          # Algorithmes de similarité textuelle
pandas >= 2.1             # Lecture/écriture CSV/XLSX
python-dotenv >= 1.0      # Chargement .env
beautifulsoup4 >= 4.12    # Parsing HTML (scraping)
gspread >= 6.0            # Google Sheets API
google-auth >= 2.27       # Auth Google (pour gspread)
tqdm >= 4.66              # Barres de progression (CLI)
click >= 8.1              # Framework CLI
pydantic                  # Validation de données (inclus avec FastAPI)
```

---

## 8. APIs externes consommées

| API | URL | Usage | Authentification | Limites connues |
|-----|-----|-------|-----------------|-----------------|
| Recherche d'Entreprises | `recherche-entreprises.api.gouv.fr/search` | Recherche textuelle | Aucune (publique) | Non documentées, usage raisonnable |
| Base Adresse Nationale | `api-adresse.data.gouv.fr/search` | Géocodage adresses | Aucune (publique) | ~50 req/s recommandé |

---

## 9. Schéma de données — Modèles internes

### Prospect (entrée)

```python
@dataclass
class Prospect:
    # Champs renseignés par l'utilisateur
    nom: str
    adresse: str
    code_postal: str
    ville: str
    telephone: str = ""
    site_web: str = ""
    email: str = ""

    # Champs calculés par normalizer.py
    nom_clean: str = ""              # Nom nettoyé
    nom_variantes: list[str] = []    # Jusqu'à 4 variantes
    adresse_numero: str = ""         # N° extrait de l'adresse
    adresse_voie_clean: str = ""     # Voie nettoyée
    departement: str = ""            # Déduit du code postal

    # Résultat du matching
    result: SireneResult = None
```

### SireneResult (sortie)

```python
@dataclass
class SireneResult:
    siret: str
    siren: str
    denomination: str
    enseigne: str = ""
    naf: str = ""
    effectif: str = ""
    tranche_effectif_code: str = ""
    date_creation: str = ""
    dirigeant: str = ""
    code_postal: str = ""
    commune: str = ""
    numero_voie: str = ""
    voie: str = ""
    score: int = 0                    # 0-100
    methode: str = ""                 # API_RECHERCHE_EXACT, etc.
    opco: str = ""                    # Nom de l'OPCO
    source_opco: str = ""            # FRANCE_COMPETENCES, NAF, ENSEIGNE
    idcc: str = ""                   # Code IDCC
    convention_collective: str = ""  # Libellé complet
```
