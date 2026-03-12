"""Dataclasses pour les prospects et résultats Sirene."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Prospect:
    """Prospect issu de Google Maps."""
    nom: str
    adresse: str
    code_postal: str
    ville: str
    departement: str = ""
    telephone: str = ""
    site_web: str = ""
    email: str = ""
    secteur_recherche: str = ""
    place_id: str = ""
    rating: str = ""
    avis: str = ""
    # Champs calculés par le normalizer
    nom_clean: str = ""
    nom_parentheses: str = ""
    nom_variantes: list = field(default_factory=list)
    adresse_numero: str = ""
    adresse_voie: str = ""
    adresse_voie_clean: str = ""
    # Résultat du matching
    result: Optional["SireneResult"] = None

    def __post_init__(self):
        self.code_postal = str(self.code_postal).replace(".0", "").strip()
        if not self.departement:
            cp = self.code_postal
            if cp.startswith("97"):
                self.departement = cp[:3]
            elif cp.startswith("200") or cp.startswith("201"):
                self.departement = "2A" if int(cp) <= 20190 else "2B"
            elif cp.startswith("20"):
                self.departement = "2A"
            else:
                self.departement = cp[:2]


@dataclass
class SireneResult:
    """Résultat d'un matching Sirene."""
    siret: str = ""
    siren: str = ""
    denomination: str = ""
    enseigne: str = ""
    naf: str = ""
    libelle_naf: str = ""
    effectif: str = ""
    tranche_effectif_code: str = ""
    date_creation: str = ""
    dirigeant: str = ""
    code_postal: str = ""
    commune: str = ""
    numero_voie: str = ""
    voie: str = ""
    # Matching
    score: float = 0.0
    methode: str = ""
    # OPCO
    opco: str = ""
    source_opco: str = ""
    idcc: str = ""
    convention_collective: str = ""
