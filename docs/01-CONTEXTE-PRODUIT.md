# SIRET Matcher — Contexte produit & fonctionnel

> Document de transfert de compétences — Mars 2026
> Public : Équipe Scrum v3

---

## 1. Qu'est-ce que SIRET Matcher ?

SIRET Matcher est un **service d'enrichissement automatique de prospects** à partir de données publiques françaises. Il prend en entrée des informations partielles sur une entreprise (nom, adresse, ville…) et retourne ses **identifiants légaux** (SIRET, SIREN), son **OPCO**, son **IDCC** (convention collective) et d'autres métadonnées issues du répertoire SIRENE de l'INSEE.

### Le problème qu'il résout

Quand un commercial ou un CRM récupère des prospects (ex. via Google Maps), il dispose du nom de l'entreprise et de son adresse, mais **pas de son SIRET**. Or, pour qualifier un prospect dans le secteur de la formation professionnelle, il faut connaître :

- Le **SIRET** (identification légale)
- L'**OPCO** (organisme qui finance la formation — détermine si le prospect est éligible)
- L'**IDCC** (convention collective — détermine les droits à la formation)
- La **taille** de l'entreprise (tranche d'effectif)

SIRET Matcher automatise ce processus de recherche qui, manuellement, prendrait plusieurs minutes par prospect.

---

## 2. Qui utilise SIRET Matcher ?

### 2.1 DST Campus (client principal)

- **URL** : https://dstcampus.fr
- **Usage** : Un simulateur web permet à un visiteur de saisir son numéro SIRET pour obtenir instantanément les informations de son entreprise (OPCO, convention collective, effectif…).
- **Endpoint consommé** : `GET /api/dst/siret/{siret}`
- **Flux** : Le navigateur du visiteur appelle directement l'API via `fetch()` (d'où la nécessité du CORS).

### 2.2 Workflows n8n (enrichissement batch)

- **Usage** : Des workflows n8n appellent l'API pour enrichir automatiquement des listes de prospects extraites de Google Maps ou d'autres sources.
- **Endpoints consommés** : `POST /match` et `POST /match/batch`

### 2.3 CLI (usage ponctuel)

- **Usage** : Enrichissement en ligne de commande de fichiers CSV/XLSX ou Google Sheets.
- **Commande** : `python -m siret_matcher prospects.csv -o enriched.csv`

### 2.4 Interface de recherche (search)

- **Usage** : Recherche avancée de prospects par département, taille, IDCC, code NAF.
- **Endpoints** : `POST /search/prospects`, `GET /search/regions`, `GET /search/idcc`

---

## 3. Sources de données

| Source | Type | Contenu | Volume | Mise à jour |
|--------|------|---------|--------|-------------|
| **INSEE SIRENE** | BDD locale (PostgreSQL) | Tous les établissements actifs en France | 16.7M lignes | Import périodique (fichier stock INSEE) |
| **France Compétences** | BDD locale (PostgreSQL) | Mapping SIRET → OPCO + IDCC | 3.49M lignes | Import périodique |
| **API Recherche d'Entreprises** | API externe (gouv.fr) | Recherche textuelle d'entreprises | Temps réel | Maintenue par l'État |
| **API Base Adresse Nationale (BAN)** | API externe (gouv.fr) | Géocodage et normalisation d'adresses | Temps réel | Maintenue par l'État |
| **Dictionnaires statiques** | Fichier Python | IDCC→convention (495), NAF→libellé (732), dept→région (101) | Fixe | Manuel |

---

## 4. Fonctionnalités détaillées

### 4.1 Lookup SIRET direct (`GET /api/dst/siret/{siret}`)

Le cas le plus simple : l'utilisateur connaît déjà le SIRET.

```
Entrée : SIRET (14 chiffres)
   ↓
Recherche en BDD locale (etablissements + siret_opco + idcc_libelles)
   ↓
Sortie : dénomination, NAF, effectif, OPCO, IDCC, convention, adresse, région
```

**Enrichissements** :
- OPCO : d'abord via France Compétences, sinon par code NAF, sinon par enseigne
- Convention collective : via table idcc_libelles, sinon dictionnaire statique
- Libellé NAF : via dictionnaire statique (732 codes)
- Région : déduite du code postal

### 4.2 Matching intelligent (`POST /match`)

Le cas complexe : on ne connaît que le nom et l'adresse.

```
Entrée : nom, adresse, code_postal, ville [, telephone, site_web]
   ↓
Normalisation (nettoyage nom, génération de variantes, parsing adresse)
   ↓
Pipeline de 5 étapes séquentielles (s'arrête au premier match)
   ↓
Enrichissement OPCO / IDCC
   ↓
Sortie : SIRET trouvé + score de confiance (0-100) + méthode utilisée
```

### 4.3 Matching batch (`POST /match/batch`)

Identique au matching simple, mais pour une liste de prospects avec contrôle de concurrence (1 à 20 traitements parallèles).

### 4.4 Recherche avancée (`POST /search/prospects`)

Requête SQL dynamique avec filtres combinables :
- **Département** (obligatoire) — ex. : ["75", "92", "93"]
- **Taille** — MOINS_11, DE_11_A_49, PLUS_DE_50, TOUTES
- **IDCC** — code exact (ex. "1486")
- **NAF** — préfixe (ex. "62" pour tout le secteur informatique)

Résultats paginés (limit/offset) avec compteur total.

---

## 5. Le pipeline de matching en détail

Le matching est le coeur fonctionnel de l'application. Il s'exécute en **5 étapes séquentielles** — dès qu'une étape trouve un match avec un score suffisant, les suivantes sont ignorées.

### Étape 1-2 : Recherche via API gouvernementale

- Interroge l'API `recherche-entreprises.api.gouv.fr`
- Étape 1 : recherche par nom + code postal
- Étape 2 : recherche par nom + département (périmètre élargi)
- Teste jusqu'à 4 variantes du nom
- Seuil : ≥ 65 pts = match exact, ≥ 40 pts = match probable

### Étape 3 : Correspondance par adresse physique

- Géocode l'adresse via l'API BAN (Base Adresse Nationale)
- Cherche dans la BDD locale tous les établissements à cette adresse exacte
- Si un seul résultat : match quasi-certain (score ~95)
- Si plusieurs : départage par similarité du nom
- Particulièrement efficace pour les **franchises** (ex. "Point S" au 12 rue de la Gare)

### Étape 4 : Recherche floue par trigrammes

- Utilise l'extension PostgreSQL `pg_trgm` pour la similarité textuelle
- Capable de matcher "CORS AUTO" → "CORSE AUTOMOBILE"
- Seuil : ≥ 45 pts

### Étape 5 : Extraction depuis le site web

- Crawle les pages de mentions légales du site web du prospect
- Extrait le SIRET par expressions régulières
- Valide via l'algorithme de Luhn
- Vérifie l'existence en BDD
- Dernier recours, mais efficace quand le site web est renseigné

### Si aucune étape ne trouve de match

- Le prospect est marqué `NON_TROUVE` avec un score de 0
- Un OPCO de fallback est quand même attribué (par NAF ou enseigne)

---

## 6. Système de scoring

Chaque match reçoit un **score composite sur 100 points** :

| Dimension | Points max | Critères |
|-----------|-----------|----------|
| **Nom** | 50 | Combinaison pondérée de 5 algorithmes : Levenshtein (30%), Jaro-Winkler (20%), Token-Sort (25%), Token-Set (15%), Mots communs (10%) |
| **Géographie** | 30 | Code postal exact = 30, département exact = 15 |
| **Adresse** | 20 | Numéro exact = 12, similarité voie ≥ 0.7 = 8, ≥ 0.5 = 4 |
| **Bonus unicité** | +5 | Un seul établissement trouvé à cette adresse |

### Méthodes de matching retournées

| Méthode | Signification | Confiance |
|---------|--------------|-----------|
| `API_RECHERCHE_EXACT` | API gouv.fr, score ≥ 65 | Haute |
| `API_RECHERCHE_PROBABLE` | API gouv.fr, score 40-64 | Moyenne |
| `ADDRESS_UNIQUE` | Seul établissement à cette adresse | Très haute |
| `ADDRESS_MULTI` | Meilleur match parmi plusieurs à cette adresse | Haute |
| `TRIGRAM_FUZZY` | Recherche floue en BDD locale | Moyenne |
| `SCRAPE_MENTIONS_LEGALES` | Extrait du site web | Moyenne |
| `NON_TROUVE` | Aucun match | — |

---

## 7. Glossaire métier

| Terme | Définition |
|-------|-----------|
| **SIRET** | Identifiant unique d'un établissement (14 chiffres = SIREN 9 + NIC 5) |
| **SIREN** | Identifiant unique d'une entreprise (9 chiffres, commun à tous ses établissements) |
| **NAF / APE** | Code d'activité principale (ex. 62.02A = conseil en informatique) |
| **OPCO** | Opérateur de Compétences — organisme paritaire qui finance la formation professionnelle. Il y en a 11 en France. |
| **IDCC** | Identifiant de Convention Collective — code à 4 chiffres rattachant une entreprise à sa convention |
| **Convention collective** | Accord négocié entre syndicats et employeurs définissant les conditions de travail dans un secteur |
| **INSEE SIRENE** | Répertoire national des entreprises et établissements, géré par l'INSEE |
| **BAN** | Base Adresse Nationale — référentiel officiel des adresses en France |
| **France Compétences** | Autorité publique qui publie le mapping SIRET → OPCO |
| **Tranche d'effectif** | Code INSEE indiquant la fourchette de salariés (ex. "11" = 10-19 salariés) |
| **pg_trgm** | Extension PostgreSQL permettant la recherche floue par trigrammes |
