# SIRET Matcher API — Documentation complète

> **Version** : 2.0
> **Serveur** : `srv910455.hstgr.cloud`
> **Port interne** : 8042
> **Runtime** : FastAPI + Uvicorn + asyncpg
> **Base de données** : PostgreSQL 16, port 5433, base `sirene`

---

## Table des matières

1. [Architecture générale](#1-architecture-générale)
2. [Infrastructure et réseau](#2-infrastructure-et-réseau)
3. [Modèle de données](#3-modèle-de-données)
4. [Endpoints](#4-endpoints)
5. [Logique d'enrichissement](#5-logique-denrichissement)
6. [Sécurité et rate limiting](#6-sécurité-et-rate-limiting)
7. [Dictionnaires applicatifs](#7-dictionnaires-applicatifs)
8. [Administration](#8-administration)
9. [Exemples d'intégration](#9-exemples-dintégration)

---

## 1. Architecture générale

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
│               │  GET  /api/dst/siret/{siret}  │  ← nouveau      │
│               │  POST /match                  │  ← existant     │
│               │  POST /match/batch            │  ← existant     │
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

---

## 2. Infrastructure et réseau

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

- **Binding** : `0.0.0.0:8042` (toutes les interfaces)
- **Utilisateur** : `postgres` (accès direct à la base via socket Unix)

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

- Seul le path `/api/dst/` est exposé publiquement via Traefik
- Les endpoints `/match` et `/match/batch` restent accessibles uniquement via le bridge Docker (`172.17.0.1:8042`)
- TLS automatique via Let's Encrypt (ACME TLS challenge)

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

## 3. Modèle de données

### 3.1 Table `etablissements` — 16 715 895 lignes

Source : base SIRENE de l'INSEE (stock complet des établissements français).

| Colonne | Type | Nullable | Description |
|---------|------|----------|-------------|
| **siret** | `text` | `NOT NULL` (PK) | Identifiant SIRET à 14 chiffres |
| **siren** | `text` | `NOT NULL` | Identifiant SIREN à 9 chiffres (entreprise) |
| denomination | `text` | oui | Raison sociale de l'entreprise |
| denomination_usuelle | `text` | oui | Nom commercial usuel |
| enseigne | `text` | oui | Enseigne de l'établissement |
| enseigne2 | `text` | oui | Enseigne secondaire |
| naf | `text` | oui | Code NAF/APE (ex: `10.71C`) |
| numero_voie | `text` | oui | Numéro dans la voie |
| type_voie | `text` | oui | Type de voie (RUE, AV, BD…) |
| voie | `text` | oui | Nom de la voie |
| code_postal | `text` | oui | Code postal à 5 chiffres |
| commune | `text` | oui | Nom de la commune |
| tranche_effectif | `text` | oui | Code tranche effectif INSEE |
| date_creation | `text` | oui | Date de création (YYYY-MM-DD) |
| etat_administratif | `text` | oui | `A` = actif, `F` = fermé |
| departement | `text` | oui | Code département (2-3 chiffres) |
| denomination_clean | `text` | oui | Dénomination normalisée (pour trigrams) |
| enseigne_clean | `text` | oui | Enseigne normalisée (pour trigrams) |
| voie_clean | `text` | oui | Voie normalisée (pour trigrams) |

**Index** :

| Nom | Type | Colonnes | Usage |
|-----|------|----------|-------|
| `etablissements_pkey` | btree | `siret` | Lookup direct par SIRET |
| `idx_siren` | btree | `siren` | Recherche par SIREN |
| `idx_cp` | btree | `code_postal` | Filtrage géographique |
| `idx_cp_num` | btree | `code_postal, numero_voie` | Matching par adresse |
| `idx_dept` | btree | `departement` | Fallback départemental |
| `idx_denom_trgm` | GIN | `denomination_clean` | Recherche fuzzy par nom |
| `idx_enseigne_trgm` | GIN | `enseigne_clean` | Recherche fuzzy par enseigne |
| `idx_voie_trgm` | GIN | `voie_clean` | Recherche fuzzy par adresse |

### 3.2 Table `siret_opco` — 3 490 284 lignes

Source : France Compétences (table de correspondance SIRET → OPCO officiel).

| Colonne | Type | Nullable | Description |
|---------|------|----------|-------------|
| **siret** | `text` | `NOT NULL` (PK) | SIRET de l'établissement |
| idcc | `text` | oui | Code IDCC (convention collective) |
| opco_proprietaire | `text` | oui | OPCO propriétaire (prioritaire) |
| opco_gestion | `text` | oui | OPCO de gestion (fallback) |

**Index** : `siret_opco_pkey` (btree sur `siret`), `idx_opco_siret` (btree sur `siret`).

### 3.3 Table `idcc_libelles` — 490 lignes

Source : DILA/KALI (Journal Officiel) + compléments manuels. Couverture : 97.9% des SIRET ayant un IDCC.

| Colonne | Type | Nullable | Description |
|---------|------|----------|-------------|
| **idcc** | `text` | `NOT NULL` (PK) | Code IDCC (ex: `1486`, `2596`) |
| libelle | `text` | `NOT NULL` | Intitulé complet de la convention collective |

### 3.4 Codes tranche effectif

| Code | Effectif | Code | Effectif |
|------|----------|------|----------|
| `NN` | Non renseigné | `12` | 20-49 |
| `00` | 0 salarié | `21` | 50-99 |
| `01` | 1-2 | `22` | 100-199 |
| `02` | 3-5 | `31` | 200-249 |
| `03` | 6-9 | `32` | 250-499 |
| `11` | 10-19 | `41` | 500-999 |
| | | `42` | 1 000-1 999 |
| | | `51` | 2 000-4 999 |
| | | `52` | 5 000-9 999 |
| | | `53` | 10 000+ |

### 3.5 Diagramme relationnel

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

---

## 4. Endpoints

### 4.1 `GET /health`

Health check de l'API et de la connexion base de données.

**Accès** : interne uniquement (`localhost:8042` ou `172.17.0.1:8042`)
**Rate limit** : aucun

**Réponse succès** :
```json
{
  "status": "ok",
  "etablissements_actifs": 16715895
}
```

**Réponse erreur** :
```json
{
  "status": "error",
  "detail": "connection refused"
}
```

---

### 4.2 `GET /api/dst/siret/{siret}`

Lookup SIRET pour le simulateur dstcampus.fr. Recherche directe par clé primaire avec enrichissement OPCO, convention collective, libellé NAF et région.

**Accès** : public via `https://api.srv910455.hstgr.cloud/api/dst/siret/{siret}`
**Rate limit** : 30 requêtes/minute par IP
**CORS** : autorisé pour `dstcampus.fr`
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
  "convention_collective": "Convention collective nationale de la coiffure et des professions connexes du 10 juillet 2006.",
  "adresse": "70 RUE CHABOT CHARNY",
  "code_postal": "21000",
  "ville": "DIJON",
  "region": "Bourgogne-Franche-Comté"
}
```

| Champ | Type | Source | Description |
|-------|------|--------|-------------|
| `found` | bool | — | Toujours `true` si SIRET trouvé |
| `siret` | string | `etablissements.siret` | SIRET à 14 chiffres |
| `siren` | string | `etablissements.siren` | SIREN à 9 chiffres |
| `denomination` | string | `etablissements.denomination` | Raison sociale |
| `enseigne` | string | `etablissements.enseigne` ou `denomination_usuelle` | Enseigne commerciale |
| `code_naf` | string | `etablissements.naf` | Code NAF/APE (ex: `96.02A`) |
| `libelle_naf` | string | Dictionnaire Python (~350 codes) | Libellé du code NAF |
| `effectif_code` | string | `etablissements.tranche_effectif` | Code tranche effectif INSEE |
| `date_creation` | string | `etablissements.date_creation` | Date de création (YYYY-MM-DD) |
| `opco` | string | `siret_opco` ou fallback NAF | Nom de l'OPCO |
| `source_opco` | string | — | `FRANCE_COMPETENCES` ou `NAF` |
| `idcc` | string | `siret_opco.idcc` | Code IDCC (peut être vide) |
| `convention_collective` | string | `idcc_libelles.libelle` | Intitulé de la convention collective |
| `adresse` | string | Concaténation `numero_voie` + `type_voie` + `voie` | Adresse postale |
| `code_postal` | string | `etablissements.code_postal` | Code postal |
| `ville` | string | `etablissements.commune` | Commune |
| `region` | string | Dictionnaire Python (CP → département → région) | Région administrative |

#### Réponse — SIRET non trouvé (200)

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

#### Requête SQL exécutée

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

### 4.3 `POST /match`

Matching d'un prospect Google Maps vers un SIRET via pipeline à 5 étapes. Utilisé par le workflow n8n de prospection.

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
| `nom` | oui | Nom de l'entreprise/prospect |
| `code_postal` | oui | Code postal |
| `adresse` | non | Adresse postale |
| `ville` | non | Commune |
| `telephone` | non | Numéro de téléphone |
| `site_web` | non | URL du site (pour scraping SIRET) |
| `email` | non | Email |
| `secteur_recherche` | non | Secteur d'activité |
| `place_id` | non | Google Place ID |
| `rating` | non | Note Google Maps |
| `avis` | non | Nombre d'avis Google |

#### Réponse

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

| Champ | Description |
|-------|-------------|
| `score` | Score de confiance (0-100). ≥65 = match fiable |
| `methode` | Méthode de matching utilisée (voir pipeline ci-dessous) |
| `matched` | `true` si un SIRET a été trouvé |
| `dirigeant` | Nom du dirigeant (via API externe) |

#### Pipeline de matching (5 étapes)

```
Étape 1-2 : API Recherche d'Entreprises (api.gouv.fr)
    → Recherche par nom + code postal, puis nom + département
    → Score ≥ 65 → match exact, on s'arrête
    → Score ≥ 40 → match probable, on continue

Étape 3 : Address Match (base locale)
    → Géocodage via API BAN (adresse.data.gouv.fr)
    → Recherche par numéro + voie + code postal dans la base
    → 1 seul résultat = match haute confiance (~95)

Étape 4 : Trigram Fuzzy (base locale, pg_trgm)
    → Recherche par similarité sur denomination_clean et enseigne_clean
    → Score ≥ 45 → match accepté

Étape 5 : Scraping site web
    → Recherche de SIRET dans les pages légales du site
    → Validation du SIRET trouvé en base
    → Score 80 (validé) ou 65 (non validé)

Aucun match → score 0, matched = false
```

**Valeurs possibles de `methode`** :

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

### 4.4 `POST /match/batch`

Matching en lot de plusieurs prospects.

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

| Champ | Requis | Description |
|-------|--------|-------------|
| `prospects` | oui | Liste de prospects (même format que `/match`) |
| `concurrency` | non | Parallélisme (1-20, défaut : 5) |

#### Réponse

```json
{
  "total": 2,
  "matched": 1,
  "taux": "50%",
  "results": [
    {"nom": "Boulangerie Du Village", "siret": "123...", "matched": true, ...},
    {"nom": "Garage Martin", "matched": false, ...}
  ]
}
```

---

## 5. Logique d'enrichissement

### 5.1 Détermination de l'OPCO

Trois sources, par ordre de priorité :

```
1. France Compétences (table siret_opco)
   → source_opco = "FRANCE_COMPETENCES"
   → 3.49M SIRET couverts
   → Donnée officielle, fiable à 100%

2. Fallback par code NAF (dictionnaire Python)
   → source_opco = "NAF"
   → Mapping des 2 premiers chiffres du code NAF vers un OPCO
   → Couverture : ~60 codes NAF → 11 OPCOs
   → Fiabilité : ~80% (un même code NAF peut relever de plusieurs OPCOs)

3. Fallback par enseigne (endpoint /match uniquement)
   → source_opco = "ENSEIGNE"
   → Matching du nom sur ~30 enseignes connues (Speedy, Midas, Renault…)
   → Utilisé uniquement par le pipeline de prospection
```

### 5.2 Convention collective

```
IDCC trouvé dans siret_opco ?
  ├── OUI → Jointure sur idcc_libelles → libellé complet
  │         490 IDCC référencés, couverture 97.9%
  └── NON → convention_collective = "" (vide)
```

### 5.3 Libellé NAF

Dictionnaire Python `NAF_LIBELLES` dans `siret_matcher/dst_lookups.py`.
~350 codes NAF les plus courants. Si le code est absent, `libelle_naf` = `""`.

### 5.4 Région

Dérivée du code postal via le dictionnaire `DEPT_TO_REGION` :

```
Code postal → 2 premiers chiffres (ou 3 pour DOM : 971, 972, 973, 974, 976)
            → département
            → région administrative (13 régions métropolitaines + 5 DOM)
```

### 5.5 Adresse

Concaténation des champs : `numero_voie` + `type_voie` + `voie`.

Exemple : `"70"` + `"RUE"` + `"CHABOT CHARNY"` → `"70 RUE CHABOT CHARNY"`

### 5.6 Enseigne

Priorité : `enseigne` > `denomination_usuelle` > `""` (vide).

---

## 6. Sécurité et rate limiting

### Rate limiting

| Endpoint | Limite | Scope |
|----------|--------|-------|
| `GET /api/dst/siret/{siret}` | 30 req/min | Par adresse IP |
| `GET /health` | Aucune | — |
| `POST /match` | Aucune (semaphores internes) | — |
| `POST /match/batch` | Aucune (semaphores internes) | — |

Implémenté via **slowapi** (basé sur `limits`). Au-delà de la limite, retourne HTTP 429.

### Semaphores internes (pipeline /match)

| Ressource | Limite | Raison |
|-----------|--------|--------|
| API Recherche d'Entreprises | 5 concurrents | Respect du rate limit API gouv |
| Base de données locale | 20 concurrents | Protection du pool asyncpg |
| Scraping web | 10 concurrents | Politesse crawling |

### CORS

Seules les origines listées sont autorisées. Les requêtes depuis d'autres domaines sont bloquées par le navigateur (pas de header `Access-Control-Allow-Origin`).

### Exposition réseau

| Endpoint | Réseau | Accès |
|----------|--------|-------|
| `/api/dst/*` | Internet (via Traefik HTTPS) | `https://api.srv910455.hstgr.cloud/api/dst/siret/{siret}` |
| `/match`, `/match/batch` | Bridge Docker uniquement | `http://172.17.0.1:8042/match` |
| `/health` | Localhost + bridge Docker | `http://localhost:8042/health` |

### Validation des entrées

- **SIRET** : regex `^\d{14}$` — rejet immédiat (400) si non conforme
- **Requêtes SQL** : paramètres préparés (`$1`) — aucune injection possible
- **Base** : accès lecture seule (aucun `INSERT`, `UPDATE`, `DELETE` sur les tables métier)

---

## 7. Dictionnaires applicatifs

### 7.1 NAF → OPCO (`siret_matcher/opco.py`)

Mapping des 2 premiers chiffres du code NAF vers l'OPCO de rattachement probable.

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

### 7.2 Département → Région (`siret_matcher/dst_lookups.py`)

101 entrées : 96 départements métropolitains (dont 2A, 2B pour la Corse) + 5 DOM.

### 7.3 NAF → Libellé (`siret_matcher/dst_lookups.py`)

~350 codes NAF avec leur libellé en français. Couvre l'essentiel des codes rencontrés en pratique.

### 7.4 IDCC → Convention collective (table `idcc_libelles`)

490 entrées en base de données. Sources :
- **407 entrées** : fichier officiel DILA/KALI (Journal Officiel, `echanges.dila.gouv.fr`)
- **83 entrées** : ajouts manuels (conventions régionales métallurgie, agriculture, codes spéciaux)

---

## 8. Administration

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

### Ajouter/modifier un libellé IDCC

```sql
-- Ajouter un nouvel IDCC
INSERT INTO idcc_libelles VALUES ('1234', 'Nouvelle convention collective');

-- Modifier un libellé existant
UPDATE idcc_libelles SET libelle = 'Nouveau libellé' WHERE idcc = '1234';

-- Ajout avec upsert (insert ou update)
INSERT INTO idcc_libelles VALUES ('1234', 'Convention XYZ')
ON CONFLICT (idcc) DO UPDATE SET libelle = EXCLUDED.libelle;
```

### Vérifier la couverture IDCC

```sql
-- IDCC en base sans libellé, triés par fréquence
SELECT o.idcc, COUNT(*) as nb
FROM siret_opco o
LEFT JOIN idcc_libelles il ON o.idcc = il.idcc
WHERE o.idcc IS NOT NULL AND o.idcc <> '' AND il.idcc IS NULL
GROUP BY o.idcc
ORDER BY nb DESC
LIMIT 20;
```

### Fichiers du projet

```
/opt/siret-matcher/
├── api.py                          # Point d'entrée FastAPI (tous les endpoints)
├── config/.env                     # Configuration base de données
├── siret_matcher/
│   ├── db.py                       # Pool asyncpg et requêtes SQL
│   ├── models.py                   # Dataclasses Prospect / SireneResult
│   ├── matcher.py                  # Pipeline de matching 5 étapes
│   ├── opco.py                     # Mappings NAF→OPCO, enseigne→OPCO
│   ├── dst_lookups.py              # Dictionnaires région, NAF libellés
│   ├── normalizer.py               # Normalisation noms/adresses
│   ├── scoring.py                  # Algorithmes de scoring
│   └── stages/
│       ├── api_recherche.py        # Étapes 1-2 : API gouv
│       ├── address_match.py        # Étape 3 : matching adresse
│       ├── trigram_match.py        # Étape 4 : fuzzy matching
│       └── scraper.py              # Étape 5 : scraping web
└── /root/traefik-dynamic/
    └── siret-api.yml               # Routeur Traefik pour exposition publique
```

---

## 9. Exemples d'intégration

### 9.1 JavaScript client (dstcampus.fr)

```javascript
async function lookupSiret(siret) {
  const response = await fetch(
    `https://api.srv910455.hstgr.cloud/api/dst/siret/${siret}`
  );

  if (response.status === 400) {
    const data = await response.json();
    throw new Error(data.error); // Format SIRET invalide
  }

  if (response.status === 429) {
    throw new Error("Trop de requêtes, veuillez réessayer dans quelques secondes.");
  }

  const data = await response.json();

  if (!data.found) {
    return null; // SIRET non trouvé
  }

  return {
    siret: data.siret,
    siren: data.siren,
    nom: data.denomination,
    enseigne: data.enseigne,
    activite: data.libelle_naf,
    codeNaf: data.code_naf,
    effectif: data.effectif_code,
    dateCreation: data.date_creation,
    opco: data.opco,
    convention: data.convention_collective,
    idcc: data.idcc,
    adresse: `${data.adresse}, ${data.code_postal} ${data.ville}`,
    region: data.region,
  };
}

// Utilisation
const entreprise = await lookupSiret("97980724500019");
if (entreprise) {
  console.log(`${entreprise.nom} — OPCO: ${entreprise.opco}`);
}
```

### 9.2 cURL

```bash
# Lookup simple
curl -s "https://api.srv910455.hstgr.cloud/api/dst/siret/97980724500019"

# Avec headers CORS (simule un navigateur)
curl -s -H "Origin: https://dstcampus.fr" \
  "https://api.srv910455.hstgr.cloud/api/dst/siret/97980724500019"

# Test de validité
curl -s "https://api.srv910455.hstgr.cloud/api/dst/siret/123"
# → {"error": "Format SIRET invalide..."}
```

### 9.3 n8n (endpoint /match existant)

```json
{
  "method": "POST",
  "url": "http://172.17.0.1:8042/match",
  "headers": {"Content-Type": "application/json"},
  "body": {
    "nom": "Boulangerie Du Village",
    "adresse": "12 rue de la Paix",
    "code_postal": "75002",
    "ville": "Paris"
  }
}
```
