# SIRET Matcher — Analyse critique & axes d'amélioration

> Auteur : Claude (IA), membre de l'équipe technique
> Date : Mars 2026
> Contexte : Préparation de la v3

---

## 1. Évaluation générale

Le projet est **fonctionnel et bien conçu** pour sa mission. L'architecture en pipeline séquentiel avec fallback est pertinente, le scoring composite est solide, et les choix techniques (FastAPI + asyncpg + pg_trgm) sont adaptés au volume de données (16.7M lignes). Le code est lisible et la séparation en modules (stages, scoring, normalizer) est cohérente.

Cependant, le projet a des caractéristiques typiques d'un **prototype devenu production** : pas de tests, pas de CI/CD, données d'état dans le code, et une dette technique qui s'accumule autour des dictionnaires statiques et du process d'import.

---

## 2. Points forts

| Aspect | Détail |
|--------|--------|
| **Pipeline de matching** | Architecture en 5 étapes avec fallback progressif — robuste et extensible |
| **Scoring composite** | Combinaison de 5 algorithmes de similarité — bien calibré pour le français |
| **Performance** | asyncpg + sémaphores + pg_trgm = matching rapide même en batch |
| **Normalisation** | Gestion fine des particularités françaises (franchises, formes juridiques, DOM-TOM, Corse) |
| **Enrichissement OPCO** | Triple fallback (France Compétences → NAF → enseigne) couvre la quasi-totalité des cas |

---

## 3. Problèmes identifiés

### 3.1 Absence totale de tests

**Impact : critique**

Il n'y a **aucun test unitaire, d'intégration, ou de bout en bout** dans le projet. Cela rend toute modification risquée : on ne peut pas vérifier qu'un changement dans le scoring, la normalisation, ou une étape de matching ne casse pas les cas existants.

**Recommandation** :
- Écrire des tests unitaires pour `normalizer.py` et `scoring.py` (fonctions pures, faciles à tester)
- Créer un jeu de données de référence (50-100 prospects avec le SIRET attendu) pour les tests d'intégration du pipeline
- Ajouter des tests API avec `httpx.AsyncClient` (TestClient FastAPI)

### 3.2 Dictionnaires statiques codés en dur

**Impact : élevé**

`dst_lookups.py` contient 1004 lignes de dictionnaires (495 IDCC, 732 NAF, 101 départements) directement dans le code Python. `opco.py` contient le mapping NAF→OPCO et 44 enseignes en dur. Ces données changent (nouveaux IDCC, fusions de conventions, nouvelles enseignes).

**Problèmes** :
- Toute mise à jour nécessite de modifier du code Python et redéployer
- Risque de désynchronisation entre les dictionnaires statiques et la table `idcc_libelles` (qui contient 490 entrées vs 495 dans le fichier)
- Pas de processus documenté pour mettre à jour ces données

**Recommandation** :
- Migrer ces dictionnaires en BDD ou en fichiers de config (JSON/CSV)
- Créer un script d'import automatique depuis les sources officielles (DILA pour les IDCC, INSEE pour les NAF)
- Supprimer la redondance entre `idcc_libelles` et `IDCC_TO_CONVENTION`

### 3.3 Pas de processus d'import automatisé

**Impact : élevé**

L'alimentation des tables `etablissements` et `siret_opco` est manuelle et non documentée. On ne sait pas :
- D'où télécharger les fichiers source exactement
- Quel script exécuter pour importer
- À quelle fréquence le faire
- Si l'import est incrémental ou full (probablement full vu la taille)

**Recommandation** :
- Créer un script d'import automatisé (`scripts/import_sirene.py`, `scripts/import_opco.py`)
- Documenter les sources et la procédure
- Idéalement, automatiser via cron ou un workflow n8n

### 3.4 Pas de CI/CD

**Impact : moyen**

Le déploiement est manuel : modifier le code sur le serveur, puis `systemctl restart`. Pas de git remote, pas de pipeline CI, pas de review.

**Recommandation** :
- Mettre en place un repo Git remote (GitHub/GitLab)
- CI minimale : linting + tests sur chaque push
- CD : déploiement automatique sur push vers main (simple rsync + restart via SSH)

### 3.5 Couplage monolithique

**Impact : moyen**

L'endpoint `/api/dst/siret/{siret}` (lookup simple) et le pipeline de matching (5 étapes) vivent dans le même process. Les endpoints `/search/*` aussi. Si le matching batch consomme toutes les ressources, le lookup DST est impacté.

**Recommandation** :
- À court terme : le rate limiting existant protège suffisamment
- À moyen terme : envisager de séparer le lookup DST (critique, temps réel) du matching batch (tolérant à la latence)

### 3.6 Logging insuffisant

**Impact : moyen**

Les logs existent mais sont basiques (INFO sur chaque requête). Il manque :
- Des métriques de matching (taux de succès par étape, scores moyens)
- Des alertes sur les erreurs récurrentes
- Le suivi de la latence des APIs externes

**Recommandation** :
- Ajouter des logs structurés (JSON) avec les métriques de chaque matching
- Exposer un endpoint `/metrics` (Prometheus) pour le monitoring
- Tracker le taux de succès par étape pour détecter les régressions

### 3.7 Gestion des erreurs des APIs externes

**Impact : moyen**

Les étapes 1-2 (API Recherche) et 3 (BAN) dépendent d'APIs gouvernementales. Si elles sont down ou lentes, le matching est dégradé silencieusement (l'étape échoue et on passe à la suivante). C'est le bon comportement, mais sans monitoring on ne détecte pas la dégradation.

**Recommandation** :
- Ajouter un circuit breaker sur les appels externes
- Logger les timeouts et erreurs des APIs externes
- Alerter si le taux de fallback augmente

### 3.8 Sécurité

**Impact : moyen**

- Le rate limiting (30/min) est correct mais basique (par IP — un reverse proxy Traefik relaie tout depuis la même IP interne `172.18.0.x`)
- Pas d'authentification sur les endpoints de matching (`/match`, `/match/batch`) — tout le monde peut y accéder
- Le scraper (étape 5) fait des requêtes vers des sites tiers sans vérification de sécurité poussée

**Recommandation** :
- Utiliser le header `X-Forwarded-For` pour le rate limiting (Traefik le transmet)
- Ajouter une API key pour les endpoints de matching
- Ajouter un timeout strict et une taille max de réponse pour le scraper

---

## 4. Axes d'amélioration pour la v3

### 4.1 Priorité haute

| # | Amélioration | Effort | Impact |
|---|-------------|--------|--------|
| 1 | **Écrire une suite de tests** (unitaires + intégration + jeu de données de référence) | 3-5 jours | Indispensable avant toute évolution |
| 2 | **Externaliser les dictionnaires statiques** en BDD ou fichiers de config | 2-3 jours | Maintenance simplifiée, données à jour |
| 3 | **Automatiser l'import SIRENE + OPCO** (scripts + documentation) | 3-5 jours | Autonomie de l'équipe, données fraîches |
| 4 | **Corriger le rate limiting** pour utiliser l'IP réelle du client (X-Forwarded-For) | 0.5 jour | Le rate limit actuel ne protège rien (tout vient de Traefik) |

### 4.2 Priorité moyenne

| # | Amélioration | Effort | Impact |
|---|-------------|--------|--------|
| 5 | **CI/CD** : repo Git distant, pipeline tests + deploy | 2-3 jours | Qualité, traçabilité |
| 6 | **Monitoring et métriques** : logs structurés, endpoint Prometheus | 2 jours | Visibilité sur la santé |
| 7 | **Authentification API** : API key sur /match et /match/batch | 1 jour | Sécurité |
| 8 | **Documentation API OpenAPI** : enrichir les descriptions FastAPI pour un Swagger complet | 1 jour | Onboarding développeurs |

### 4.3 Priorité basse (évolutions fonctionnelles)

| # | Amélioration | Effort | Impact |
|---|-------------|--------|--------|
| 9 | **Cache** : mettre en cache les résultats de lookup SIRET (Redis ou in-memory) | 1-2 jours | Réduction latence pour les SIRET récurrents |
| 10 | **Import incrémental** : utiliser les mises à jour quotidiennes INSEE au lieu du stock complet | 3-5 jours | Import plus rapide, données plus fraîches |
| 11 | **Webhook/callback** pour le matching batch (plutôt que réponse synchrone) | 2 jours | Support de gros volumes sans timeout |
| 12 | **Recherche multi-critères enrichie** : full-text search, autocomplétion | 3-5 jours | Meilleure UX de recherche |

---

## 5. Dette technique détaillée

### Code

| Fichier | Problème | Sévérité |
|---------|----------|----------|
| `dst_lookups.py` | 1004 lignes de données codées en dur dans du Python | Haute |
| `opco.py` | NAF_TO_OPCO et ENSEIGNE_TO_OPCO codés en dur | Haute |
| `api.py` | L'endpoint `/api/dst/siret/{siret}` fait 80 lignes avec du SQL inline — devrait être dans db.py | Moyenne |
| `normalizer.py` | Listes de franchises, marques auto, mots génériques codées en dur (ok pour l'instant, mais croissance inévitable) | Basse |
| `search_router.py` | Construction de requête SQL par concaténation de strings — fonctionne mais fragile | Moyenne |
| `scoring.py` | Seuils de scoring (65, 40, 45…) sont des magic numbers sans explication | Basse |

### Infrastructure

| Élément | Problème | Sévérité |
|---------|----------|----------|
| Déploiement | Pas de CI/CD, modification directe sur le serveur de prod | Haute |
| Backup | Pas de backup automatique de la BDD documenté | Haute |
| Monitoring | Aucun monitoring / alerting | Moyenne |
| Environnement | Un seul environnement (prod), pas de staging | Moyenne |
| Index BDD | Index redondants (`siret_opco_pkey` et `idx_opco_siret` couvrent la même colonne — 136+105 = 241 MB gaspillés) | Basse |

### Données

| Élément | Problème | Sévérité |
|---------|----------|----------|
| SIRENE | Processus d'import non documenté | Haute |
| France Compétences | Processus d'import non documenté | Haute |
| IDCC | Désynchronisation possible entre table (490) et fichier Python (495) | Moyenne |
| NAF libellés | 732 entrées dans le fichier Python, source non traçable | Basse |

---

## 6. Proposition d'architecture v3

Si la v3 implique une refonte significative, voici une architecture cible :

```
                          Internet
                             │
                             ▼
                    ┌─────────────────┐
                    │  Reverse Proxy   │  Traefik ou Nginx
                    │  + Rate Limiting │  (par IP réelle)
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     ┌──────────────┐ ┌───────────┐ ┌──────────────┐
     │  API Lookup   │ │ API Match │ │  API Search  │
     │  (temps réel) │ │ (batch)   │ │  (filtres)   │
     │  /api/dst/*   │ │ /match/*  │ │  /search/*   │
     └──────┬───────┘ └─────┬─────┘ └──────┬───────┘
              │              │              │
              └──────────────┼──────────────┘
                             │
                    ┌────────┴────────┐
                    │   PostgreSQL     │
                    │   + Redis cache  │
                    └─────────────────┘
                             │
                    ┌────────┴────────┐
                    │  Scripts import  │  cron quotidien
                    │  SIRENE + OPCO   │
                    └─────────────────┘
```

Points clés :
- **Séparation des concerns** : lookup temps réel vs batch vs search
- **Cache Redis** pour les SIRET fréquemment consultés
- **Import automatisé** des données SIRENE et OPCO
- **Dictionnaires en BDD** au lieu de fichiers Python
- **Tests + CI/CD** comme prérequis

---

## 7. Questions ouvertes pour l'équipe

1. **Quel est le SLA attendu** pour l'endpoint DST Campus ? (actuellement : pas de SLA, pas de monitoring)
2. **Quelle est la fréquence de mise à jour** souhaitée pour les données SIRENE ? (mensuel ? hebdo ? quotidien ?)
3. **Y a-t-il d'autres consommateurs** prévus pour l'API en v3 ?
4. **Le matching batch est-il encore nécessaire** ou les workflows n8n suffisent-ils ?
5. **Faut-il supporter d'autres pays** à terme ou uniquement la France ?
6. **Quel est le volume de requêtes actuel** sur l'endpoint DST ? (pas de métriques disponibles aujourd'hui)
