"""
Tests de non-régression du pipeline de matching.

Ce fichier charge le jeu de données de référence et vérifie que le pipeline
maintient un taux de matching acceptable. C'est le gardien de la qualité
du matching lors des refactors v3.

Seuils de non-régression :
- easy       : >= 90% de matchs corrects (bon SIRET)
- medium     : >= 60% de matchs (bon SIRET ou match avec score > min_score)
- hard       : >= 30% de matchs
- impossible : 100% NON_TROUVE
- Global     : >= 70% de matchs corrects
"""
import json
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.regression,
    pytest.mark.slow,
    pytest.mark.asyncio(loop_scope="session"),
]

THRESHOLDS = {
    "easy": 0.90,
    "medium": 0.60,
    "hard": 0.30,
    "impossible": 1.00,
}
GLOBAL_THRESHOLD = 0.70

DATA_PATH = Path(__file__).parent / "data" / "prospects_reference.json"


def _load_reference():
    with open(DATA_PATH) as f:
        return json.load(f)


def _check_result(prospect, result_data):
    """Vérifie un résultat contre les attentes. Retourne (success, detail)."""
    expected = prospect["expected"]

    if not expected["should_match"]:
        ok = result_data["matched"] is False
        return ok, "correctly not matched" if ok else f"unexpected match: {result_data.get('siret')}"

    if not result_data["matched"]:
        return False, "not matched (expected match)"

    # Accepter le match si même SIRET ou même SIREN (même entité, établissement différent)
    got_siret = result_data.get("siret", "")
    exp_siret = expected["siret"]
    siret_ok = got_siret == exp_siret or got_siret[:9] == exp_siret[:9]
    score_ok = True
    if "min_score" in expected:
        score_ok = result_data.get("score", 0) >= expected["min_score"]

    if siret_ok and score_ok:
        return True, f"SIRET OK, score={result_data.get('score', 0)}"

    if siret_ok and not score_ok:
        return False, f"SIRET OK but score {result_data.get('score', 0)} < {expected['min_score']}"

    return False, f"wrong SIRET: got {result_data.get('siret')} expected {expected['siret']}"


async def test_pipeline_regression(api_client):
    """Test de non-régression du pipeline de matching."""
    reference = _load_reference()

    # Résultats par catégorie
    results_by_cat = {cat: [] for cat in THRESHOLDS}
    details = []

    for prospect in reference:
        cat = prospect["difficulty"]
        inp = prospect["input"]

        payload = {
            "nom": inp["nom"],
            "adresse": inp.get("adresse", ""),
            "code_postal": inp.get("code_postal", ""),
            "ville": inp.get("ville", ""),
        }

        resp = await api_client.post("/match", json=payload)
        assert resp.status_code == 200, f"{prospect['id']}: status {resp.status_code}"
        data = resp.json()

        success, detail = _check_result(prospect, data)
        results_by_cat[cat].append(success)
        details.append(f"{'OK' if success else 'KO'} {prospect['id']} ({cat}) {inp['nom'][:30]:30s} — {detail}")

    # Affichage du rapport
    report_lines = ["\n=== Pipeline Regression Report ==="]
    all_ok = True
    total_success = 0
    total_count = 0

    for cat, threshold in THRESHOLDS.items():
        cat_results = results_by_cat[cat]
        if not cat_results:
            continue
        successes = sum(cat_results)
        count = len(cat_results)
        rate = successes / count
        total_success += successes
        total_count += count
        passed = rate >= threshold
        icon = "PASS" if passed else "FAIL"
        report_lines.append(
            f"  {cat:12s}: {successes:2d}/{count:2d} ({rate*100:5.1f}%) "
            f"— seuil {threshold*100:.0f}% {icon}"
        )
        if not passed:
            all_ok = False

    global_rate = total_success / total_count if total_count else 0
    global_passed = global_rate >= GLOBAL_THRESHOLD
    report_lines.append(
        f"  {'GLOBAL':12s}: {total_success:2d}/{total_count:2d} ({global_rate*100:5.1f}%) "
        f"— seuil {GLOBAL_THRESHOLD*100:.0f}% {'PASS' if global_passed else 'FAIL'}"
    )
    if not global_passed:
        all_ok = False

    report_lines.append("\n--- Détail ---")
    for line in details:
        report_lines.append(f"  {line}")

    report = "\n".join(report_lines)
    print(report)

    assert all_ok and global_passed, f"Regression thresholds not met\n{report}"
