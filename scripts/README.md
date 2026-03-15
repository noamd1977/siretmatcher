# Scripts — SIRET Matcher

## `import_sirene.py` — Import de la base Sirene

### Prérequis

- **Python 3.12+** avec le virtualenv du projet activé
- **PostgreSQL 16** avec les extensions `pg_trgm` et `unaccent`
- **Accès BDD** : un user avec droits `CREATE TABLE`, `DROP TABLE` sur le schéma `public`
- **Espace disque** : ~15 Go pour le CSV décompressé + ~5 Go pour la table + ~3 Go pour les index
- **wget** installé (pour le téléchargement)

### Source des données

Fichier stock des établissements INSEE, mis à jour mensuellement :

```
https://files.data.gouv.fr/insee-sirene/StockEtablissement_utf8.csv.gz
```

- Compressé : ~2 Go (.gz)
- Décompressé : ~12-15 Go (.csv)
- ~40 millions de lignes (dont ~16.7M actifs)

### Usage

```bash
cd /opt/siret-matcher
source venv/bin/activate

# Import depuis un CSV local existant
python scripts/import_sirene.py

# Télécharger depuis data.gouv.fr puis importer
python scripts/import_sirene.py --download

# Spécifier un CSV custom (ex: extrait de test)
python scripts/import_sirene.py --csv /path/to/fichier.csv

# Recréer uniquement les index (sans réimporter)
python scripts/import_sirene.py --indexes-only
```

### Temps estimé (serveur 4 vCPU, SSD)

| Étape | Temps |
|---|---|
| Téléchargement (~2 Go) | 5-15 min |
| Décompression .gz | 2-5 min |
| Préparation CSV (colonnes dérivées) | 10-15 min |
| COPY dans PostgreSQL | 5-10 min |
| Création des index (11 index) | 15-30 min |
| **Total (sans téléchargement)** | **30-60 min** |

### Stratégie zero-downtime

L'import utilise une table temporaire pour éviter tout downtime :

1. `CREATE TABLE etablissements_new` — table de travail
2. `COPY` les données dans `etablissements_new`
3. Création des index sur `etablissements_new`
4. `RENAME` atomique : `etablissements` → `etablissements_old`, `etablissements_new` → `etablissements`
5. `DROP etablissements_old`

Si l'import échoue à n'importe quelle étape avant le swap, la table `etablissements` originale reste intacte.

### Colonnes dérivées

Calculées par le script Python (pas en SQL) pour être cohérentes avec le moteur de matching :

| Colonne | Source | Traitement |
|---|---|---|
| `departement` | `code_postal` | Extraction 2A/2B pour la Corse, 3 chiffres pour DOM-TOM |
| `denomination_clean` | `denomination` + `denomination_usuelle` | `normalize_base()` : majuscules, suppression accents et caractères spéciaux |
| `enseigne_clean` | `enseigne` + `enseigne2` | Idem |
| `voie_clean` | `voie` | `clean_voie()` : abréviations (RUE→R, AVE→AV...), suppression articles |

### Rollback

Si l'import a échoué **avant le swap** :

```bash
# La table originale est intacte, supprimer la table de travail
psql -h 127.0.0.1 -p 5433 -U sirene_user -d sirene \
  -c "DROP TABLE IF EXISTS etablissements_new;"
```

Si l'import a réussi mais pose problème **après le swap** : il n'y a pas de rollback automatique. Il faut relancer un import depuis le CSV précédent.

### Configuration

Variables d'environnement (ou valeurs par défaut depuis `config/.env`) :

| Variable | Défaut | Description |
|---|---|---|
| `DB_HOST` | `127.0.0.1` | Hôte PostgreSQL |
| `DB_PORT` | `5433` | Port PostgreSQL |
| `DB_NAME` | `sirene` | Nom de la base |
| `DB_USER` | `sirene_user` | Utilisateur |
| `DB_PASSWORD` | `sirene_pass` | Mot de passe |

---

## `import_opco.py` — Import de la table SIRET-OPCO

### Source des données

Table SIRET-OPCO publiée par **France Compétences**, mise à jour mensuellement :

- **Page dataset** : https://www.data.gouv.fr/fr/datasets/table-siret-opco/
- **URL directe** (janvier 2026) : https://static.data.gouv.fr/resources/table-siret-opco/20260108-123831/siro-202601.csv
- **Dictionnaire** : https://static.data.gouv.fr/resources/table-siret-opco/20250731-130402/dictionnaire-donnees-table-siro-v2au310725.pdf

**Format du fichier** :
- CSV, séparateur `|` (pipe), encoding UTF-8, avec header
- ~100 Mo, ~3.5M lignes
- Colonnes : `SIRET | IDCC | OPCO_PROPRIETAIRE | OPCO_GESTION`
- Licence Ouverte / Open Licence 2.0

### Usage

```bash
cd /opt/siret-matcher
source venv/bin/activate

# Import depuis un CSV local
python scripts/import_opco.py

# Télécharger depuis data.gouv.fr puis importer
python scripts/import_opco.py --download

# CSV custom
python scripts/import_opco.py --csv /path/to/siro.csv

# Recréer les index uniquement
python scripts/import_opco.py --indexes-only

# Synchroniser la table idcc_libelles (sans réimporter)
python scripts/import_opco.py --sync-idcc
```

### Nettoyage des données

Le script applique avant import :
- **Validation SIRET** : seuls les SIRET de exactement 14 chiffres sont retenus
- **Déduplication** : en cas de doublon, seule la première occurrence est gardée

### Temps estimé

| Étape | Temps |
|---|---|
| Téléchargement (~100 Mo) | < 1 min |
| Préparation CSV (validation + dédup) | 10-20s |
| COPY dans PostgreSQL | 30-60s |
| Création des index (3 index) | 1-3 min |
| **Total** | **2-5 min** |

### Stratégie zero-downtime

Identique à `import_sirene.py` : import dans `siret_opco_new`, création des index, swap atomique via `RENAME`.

### Table cible : `siret_opco`

| Colonne | Type | Description |
|---|---|---|
| `siret` | TEXT, PK | SIRET de l'établissement |
| `idcc` | TEXT | Code IDCC (convention collective) |
| `opco_proprietaire` | TEXT | OPCO propriétaire |
| `opco_gestion` | TEXT | OPCO de gestion |

### Index

| Nom | Type | Colonnes |
|---|---|---|
| `siret_opco_pkey` | PK / btree | `siret` |
| `idx_opco_idcc` | btree | `idcc` |
| `idx_siret_opco_idcc_siret` | btree | `idcc, siret` |

> **Note** : `idx_opco_siret` (btree sur siret seul) a été supprimé car redondant avec la PK.

### Synchronisation IDCC

Après chaque import, le script compare les IDCC de `siret_opco` avec :
1. La table `idcc_libelles` en BDD (490 entrées)
2. Le fichier `config/data/idcc_to_convention.json` (495 entrées)

Il affiche les IDCC manquants et propose les INSERT (sans les exécuter). Pour lancer uniquement cette étape : `--sync-idcc`.

### Fréquence recommandée

**Trimestrielle**. France Compétences publie mensuellement, mais les changements d'affectation OPCO sont rares. Un import trimestriel suffit pour maintenir la couverture à jour.

### Rollback

```bash
# Si l'import a échoué avant le swap
psql -h 127.0.0.1 -p 5433 -U sirene_user -d sirene \
  -c "DROP TABLE IF EXISTS siret_opco_new;"
```

### Notes

- L'URL du fichier change à chaque publication (le nom contient YYYYMM). Mettre à jour `OPCO_URL` dans le script ou copier l'URL depuis la page data.gouv.fr.
- Les 11 OPCO : OCAPIAT, OPCO2I, CONSTRUCTYS, OPCO MOBILITES, L'OPCOMMERCE, AKTO, AFDAS, ATLAS, OPCO EP, UNIFORMATION COHESION SOCIALE, OPCO SANTE
- ~120K lignes n'ont pas d'OPCO renseigné (champ vide)
