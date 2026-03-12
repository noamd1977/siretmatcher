"""
Modèles Pydantic pour les endpoints /search/*
Fichier séparé pour ne pas toucher à models.py existant.
"""
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class TailleEntreprise(str, Enum):
    """Filtre taille d'entreprise basé sur les codes tranche_effectif INSEE."""
    TPE = "TPE"           # 0-10 salariés
    PME = "PME"           # 11-249 salariés
    TPE_PME = "TPE_PME"   # 0-249 salariés
    TOUTES = "TOUTES"     # Pas de filtre effectif


# Mapping taille → codes tranche_effectif INSEE
TAILLE_CODES = {
    "TPE":     ["NN", "00", "01", "02", "03"],
    "PME":     ["11", "12", "21", "22", "31", "32"],
    "TPE_PME": ["NN", "00", "01", "02", "03", "11", "12", "21", "22", "31", "32"],
}

# Mapping Région → Départements
REGION_DEPARTEMENTS = {
    "ILE_DE_FRANCE": ["75", "77", "78", "91", "92", "93", "94", "95"],
    "AUVERGNE_RHONE_ALPES": ["01", "03", "07", "15", "26", "38", "42", "43", "63", "69", "73", "74"],
    "BOURGOGNE_FRANCHE_COMTE": ["21", "25", "39", "58", "70", "71", "89", "90"],
    "BRETAGNE": ["22", "29", "35", "56"],
    "CENTRE_VAL_DE_LOIRE": ["18", "28", "36", "37", "41", "45"],
    "CORSE": ["2A", "2B"],
    "GRAND_EST": ["08", "10", "51", "52", "54", "55", "57", "67", "68", "88"],
    "HAUTS_DE_FRANCE": ["02", "59", "60", "62", "80"],
    "NORMANDIE": ["14", "27", "50", "61", "76"],
    "NOUVELLE_AQUITAINE": ["16", "17", "19", "23", "24", "33", "40", "47", "64", "79", "86", "87"],
    "OCCITANIE": ["09", "11", "12", "30", "31", "32", "34", "46", "48", "65", "66", "81", "82"],
    "PAYS_DE_LA_LOIRE": ["44", "49", "53", "72", "85"],
    "PROVENCE_ALPES_COTE_AZUR": ["04", "05", "06", "13", "83", "84"],
    "DOM": ["971", "972", "973", "974", "976"],
}


class SearchProspectsRequest(BaseModel):
    """Requête de recherche de prospects."""
    departements: list[str] = Field(
        ...,
        description="Liste de codes départements (ex: ['75','92','93','94'])",
        min_length=1,
        max_length=101
    )
    taille: TailleEntreprise = Field(
        default=TailleEntreprise.TPE_PME,
        description="Filtre taille entreprise"
    )
    idcc: Optional[str] = Field(
        default=None,
        description="Code IDCC spécifique (ex: '1486' pour Syntec). Prioritaire sur naf."
    )
    naf: Optional[str] = Field(
        default=None,
        description="Préfixe NAF (ex: '62' pour informatique, '62.01' plus précis)"
    )
    section_naf: Optional[str] = Field(
        default=None,
        description="Section NAF lettre (ex: 'J' pour info-com). Filtre large."
    )
    limit: int = Field(default=500, ge=1, le=5000, description="Nb résultats par page")
    offset: int = Field(default=0, ge=0, description="Offset pour pagination")


class ProspectResult(BaseModel):
    """Un prospect retourné par la recherche."""
    siret: str
    siren: str
    denomination: Optional[str] = None
    denomination_usuelle: Optional[str] = None
    enseigne: Optional[str] = None
    naf: Optional[str] = None
    code_postal: Optional[str] = None
    commune: Optional[str] = None
    departement: Optional[str] = None
    tranche_effectif: Optional[str] = None
    date_creation: Optional[str] = None
    adresse: Optional[str] = None
    opco: Optional[str] = None
    idcc: Optional[str] = None
    convention_collective: Optional[str] = None


class SearchProspectsResponse(BaseModel):
    """Réponse paginée de la recherche de prospects."""
    total: int
    limit: int
    offset: int
    results: list[ProspectResult]
