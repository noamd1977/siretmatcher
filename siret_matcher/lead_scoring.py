"""Scoring de qualification de lead pour la formation professionnelle.

Critères et pondération (total 100 pts) :
- Taille de l'entreprise (30 pts)
- OPCO éligible (25 pts)
- Secteur d'activité (15 pts)
- Localisation (10 pts)
- Ancienneté (10 pts)
- Complétude des données (10 pts)
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "scoring_config.json"

# ── Barèmes par défaut ──────────────────────────────────────────────────────

TAILLE_SCORES = {
    "NN": 5, "00": 0, "01": 5, "02": 8, "03": 12,
    "11": 18, "12": 22,
    "21": 26, "22": 28, "31": 29, "32": 30,
    "41": 30, "42": 30, "51": 30, "52": 30, "53": 30,
}

DEFAULT_SECTEURS_PRIORITAIRES = {
    "62": 15, "70": 15, "85": 15,
    "86": 12, "47": 10, "56": 10, "41": 10,
}

DEFAULT_CONFIG = {
    "secteurs_prioritaires": DEFAULT_SECTEURS_PRIORITAIRES,
    "departements_prioritaires": [],
    "seuil_hot": 70,
    "seuil_warm": 45,
    "secteur_default": 5,
}


def load_config() -> dict:
    """Charge la config de scoring depuis le fichier JSON."""
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                stored = json.load(f)
            merged = {**DEFAULT_CONFIG, **stored}
            return merged
    except Exception as e:
        logger.warning(f"Erreur chargement config scoring: {e}")
    return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    """Sauvegarde la config de scoring."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ── Modèle de résultat ──────────────────────────────────────────────────────


@dataclass
class LeadScore:
    total: int = 0
    qualification: str = "cold"
    details: dict = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)


# ── Fonctions de scoring par critère ────────────────────────────────────────


def _score_taille(tranche_code: str) -> int:
    if not tranche_code:
        return 5
    return TAILLE_SCORES.get(tranche_code, 5)


def _score_opco(opco: str, source_opco: str) -> int:
    if not opco:
        return 0
    source = (source_opco or "").upper()
    if source == "FRANCE_COMPETENCES":
        return 25
    if source == "NAF":
        return 15
    # Other sources (enseigne, etc.)
    return 10


def _score_secteur(naf: str, config: dict) -> int:
    if not naf:
        return config.get("secteur_default", 5)
    prefix = naf.replace(".", "")[:2]
    secteurs = config.get("secteurs_prioritaires", DEFAULT_SECTEURS_PRIORITAIRES)
    return secteurs.get(prefix, config.get("secteur_default", 5))


def _score_localisation(departement: str, config: dict) -> int:
    depts = config.get("departements_prioritaires") or []
    if not depts:
        return 10
    return 10 if departement in depts else 3


def _score_anciennete(date_creation: str) -> int:
    if not date_creation:
        return 3
    try:
        parts = date_creation.split("-")
        annee = int(parts[0])
        age = date.today().year - annee
        if age > 10:
            return 10
        if age >= 5:
            return 8
        if age >= 2:
            return 5
        return 2
    except (ValueError, IndexError):
        return 3


def _score_completude(data: dict) -> int:
    pts = 0
    if data.get("siret"):
        pts += 2
    if data.get("opco"):
        pts += 2
    if data.get("idcc"):
        pts += 2
    if data.get("dirigeant"):
        pts += 2
    if data.get("adresse_complete"):
        pts += 2
    return min(pts, 10)


def _build_recommendations(data: dict, details: dict, config: dict) -> list[str]:
    recs = []
    if details.get("opco", 0) == 0:
        recs.append("Vérifier l'OPCO manuellement — financement incertain")
    tranche = data.get("tranche_effectif", "")
    if tranche in ("21", "22", "31", "32", "41", "42", "51", "52", "53"):
        recs.append("Entreprise de taille significative — potentiel volume formation")
    naf = data.get("naf", "")
    prefix = naf.replace(".", "")[:2]
    secteurs = config.get("secteurs_prioritaires", DEFAULT_SECTEURS_PRIORITAIRES)
    if prefix in secteurs:
        labels = {
            "62": "informatique", "70": "conseil", "85": "enseignement",
            "86": "santé", "47": "commerce", "56": "restauration",
            "41": "construction",
        }
        recs.append(f"Secteur {labels.get(prefix, prefix)} — fort besoin en formation")
    if not data.get("dirigeant"):
        recs.append("Rechercher le contact décideur")
    age = _score_anciennete(data.get("date_creation", ""))
    if age <= 2:
        recs.append("Entreprise récente — besoins en montée en compétences")
    return recs


# ── Fonction principale ─────────────────────────────────────────────────────


def score_lead(etablissement: dict, config: dict | None = None) -> LeadScore:
    """Calcule le score de qualification d'un lead.

    Args:
        etablissement: dict avec les clés siret, naf, tranche_effectif,
            opco, source_opco, idcc, dirigeant, date_creation, departement,
            adresse_complete (bool)
        config: paramètres de scoring (secteurs prioritaires, zone géo, seuils)
    """
    if config is None:
        config = load_config()

    s_taille = _score_taille(etablissement.get("tranche_effectif", ""))
    s_opco = _score_opco(
        etablissement.get("opco", ""),
        etablissement.get("source_opco", ""),
    )
    s_secteur = _score_secteur(etablissement.get("naf", ""), config)
    s_loc = _score_localisation(etablissement.get("departement", ""), config)
    s_anc = _score_anciennete(etablissement.get("date_creation", ""))
    s_comp = _score_completude(etablissement)

    total = s_taille + s_opco + s_secteur + s_loc + s_anc + s_comp

    seuil_hot = config.get("seuil_hot", 70)
    seuil_warm = config.get("seuil_warm", 45)
    if total >= seuil_hot:
        qualification = "hot"
    elif total >= seuil_warm:
        qualification = "warm"
    else:
        qualification = "cold"

    details = {
        "taille": s_taille,
        "opco": s_opco,
        "secteur": s_secteur,
        "localisation": s_loc,
        "anciennete": s_anc,
        "completude": s_comp,
    }

    recommendations = _build_recommendations(etablissement, details, config)

    return LeadScore(
        total=total,
        qualification=qualification,
        details=details,
        recommendations=recommendations,
    )
