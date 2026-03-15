"""Modèles Pydantic v2 pour l'API v3.

Chaque champ porte une description pour générer un Swagger auto-documenté.
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Sous-modèles structurés ─────────────────────────────────────────────────


class NafInfo(BaseModel):
    code: str = Field(description="Code NAF (ex: 62.02A)")
    libelle: str = Field(default="", description="Libellé NAF")


class EffectifInfo(BaseModel):
    code: str = Field(description="Code tranche effectif INSEE (ex: 42)")
    libelle: str = Field(default="", description="Tranche en clair (ex: 1000-1999)")


class AdresseInfo(BaseModel):
    numero: Optional[str] = Field(default=None, description="Numéro de voie")
    voie: Optional[str] = Field(default=None, description="Libellé de voie")
    code_postal: Optional[str] = Field(default=None, description="Code postal")
    commune: Optional[str] = Field(default=None, description="Commune")
    departement: Optional[str] = Field(default=None, description="Code département")
    region: Optional[str] = Field(default=None, description="Nom de la région")


class OpcoInfo(BaseModel):
    nom: Optional[str] = Field(default=None, description="Nom de l'OPCO")
    source: Optional[str] = Field(default=None, description="Source (FRANCE_COMPETENCES, NAF)")


class IdccInfo(BaseModel):
    code: Optional[str] = Field(default=None, description="Code IDCC")
    libelle: Optional[str] = Field(default=None, description="Libellé de la convention collective")


# ── GET /api/v3/etablissements/{siret} ──────────────────────────────────────


class EtablissementResponse(BaseModel):
    """Fiche complète d'un établissement enrichi."""
    siret: str = Field(description="SIRET (14 chiffres)")
    siren: str = Field(description="SIREN (9 chiffres)")
    denomination: Optional[str] = Field(default=None, description="Raison sociale")
    enseigne: Optional[str] = Field(default=None, description="Enseigne ou dénomination usuelle")
    naf: NafInfo = Field(description="Code et libellé NAF")
    effectif: EffectifInfo = Field(description="Tranche d'effectif salarié")
    adresse: AdresseInfo = Field(description="Adresse de l'établissement")
    opco: OpcoInfo = Field(description="OPCO de rattachement")
    idcc: IdccInfo = Field(description="Convention collective")
    date_creation: Optional[str] = Field(default=None, description="Date de création (YYYY-MM-DD)")
    etat_administratif: Optional[str] = Field(default=None, description="A=actif, F=fermé")


# ── POST /api/v3/match ──────────────────────────────────────────────────────


class MatchRequest(BaseModel):
    """Requête de matching intelligent d'un prospect."""
    nom: str = Field(description="Nom de l'entreprise à matcher")
    adresse: str = Field(default="", description="Adresse postale")
    code_postal: str = Field(description="Code postal")
    ville: str = Field(default="", description="Ville")
    telephone: str = Field(default="", description="Téléphone")
    site_web: str = Field(default="", description="URL du site web")
    email: str = Field(default="", description="Email de contact")


class StageDebug(BaseModel):
    """Détail d'une étape du pipeline de matching (mode debug)."""
    name: str = Field(description="Nom de l'étape")
    found: bool = Field(description="Résultat trouvé à cette étape")
    score: Optional[float] = Field(default=None, description="Score de confiance")
    duration_ms: Optional[float] = Field(default=None, description="Durée en ms")


class MatchDebug(BaseModel):
    """Informations de debug du matching (header X-Debug: true)."""
    stages_tried: int = Field(description="Nombre d'étapes tentées")
    duration_ms: float = Field(description="Durée totale en ms")
    stages: list[StageDebug] = Field(default_factory=list, description="Détail par étape")


class MatchResponse(BaseModel):
    """Résultat du matching intelligent."""
    matched: bool = Field(description="True si un établissement a été trouvé")
    confidence: Optional[str] = Field(
        default=None,
        description="Niveau de confiance : high (≥65), medium (≥40), low (<40)"
    )
    score: float = Field(default=0.0, description="Score de confiance (0-100)")
    methode: Optional[str] = Field(default=None, description="Méthode de matching utilisée")
    etablissement: Optional[EtablissementResponse] = Field(
        default=None, description="Établissement trouvé (null si non trouvé)"
    )
    debug: Optional[MatchDebug] = Field(
        default=None, description="Infos debug (uniquement si header X-Debug: true)"
    )


# ── POST /api/v3/match/batch ────────────────────────────────────────────────


class BatchRequest(BaseModel):
    """Requête de matching en lot."""
    prospects: list[MatchRequest] = Field(description="Liste de prospects à matcher")
    concurrency: int = Field(default=5, ge=1, le=20, description="Parallélisme (1-20)")


class BatchResponse(BaseModel):
    """Résultat du matching en lot."""
    total: int = Field(description="Nombre total de prospects")
    matched: int = Field(description="Nombre de prospects matchés")
    not_found: int = Field(description="Nombre de prospects non trouvés")
    taux_matching: float = Field(description="Taux de matching (0.0 à 1.0)")
    duration_ms: float = Field(description="Durée totale en ms")
    results: list[MatchResponse] = Field(description="Résultats individuels")


# ── POST /api/v3/search ─────────────────────────────────────────────────────


class TailleEntreprise(str, Enum):
    MOINS_11 = "MOINS_11"
    DE_11_A_49 = "DE_11_A_49"
    PLUS_DE_50 = "PLUS_DE_50"
    TOUTES = "TOUTES"


class SearchFilters(BaseModel):
    """Filtres pour la recherche avancée."""
    departements: Optional[list[str]] = Field(
        default=None,
        description="Codes départements (ex: ['75', '92'])"
    )
    taille: TailleEntreprise = Field(
        default=TailleEntreprise.TOUTES,
        description="Filtre taille entreprise"
    )
    idcc: Optional[str] = Field(default=None, description="Code IDCC (ex: 1486)")
    naf_prefix: Optional[str] = Field(default=None, description="Préfixe NAF (ex: 62)")
    etat: str = Field(default="A", description="État administratif (A=actif, F=fermé)")


class SortField(str, Enum):
    RELEVANCE = "relevance"
    DENOMINATION = "denomination"
    CODE_POSTAL = "code_postal"


class SearchRequest(BaseModel):
    """Requête de recherche unifiée."""
    q: Optional[str] = Field(default=None, description="Recherche full-text")
    filters: SearchFilters = Field(default_factory=SearchFilters, description="Filtres")
    sort: SortField = Field(default=SortField.RELEVANCE, description="Tri")
    limit: int = Field(default=50, ge=1, le=5000, description="Nombre de résultats")
    offset: int = Field(default=0, ge=0, description="Offset pour pagination")


class SearchFacets(BaseModel):
    """Compteurs pour les filtres du frontend."""
    departements: dict[str, int] = Field(
        default_factory=dict, description="Compteur par département"
    )
    tailles: dict[str, int] = Field(
        default_factory=dict, description="Compteur par taille"
    )
    top_naf: list[dict] = Field(
        default_factory=list,
        description="Top codes NAF [{'code': ..., 'libelle': ..., 'count': ...}]"
    )


class SearchResultItem(BaseModel):
    """Un résultat de recherche."""
    siret: str = Field(description="SIRET")
    siren: str = Field(description="SIREN")
    denomination: Optional[str] = Field(default=None, description="Raison sociale")
    enseigne: Optional[str] = Field(default=None, description="Enseigne")
    naf: NafInfo = Field(description="Code et libellé NAF")
    effectif: EffectifInfo = Field(description="Tranche d'effectif")
    adresse: AdresseInfo = Field(description="Adresse")
    opco: Optional[str] = Field(default=None, description="Nom OPCO")
    idcc: IdccInfo = Field(description="Convention collective")
    date_creation: Optional[str] = Field(default=None, description="Date de création")


class SearchResponse(BaseModel):
    """Réponse de recherche paginée avec facets."""
    total: int = Field(description="Nombre total de résultats")
    results: list[SearchResultItem] = Field(description="Résultats de la page")
    facets: SearchFacets = Field(description="Compteurs pour les filtres")


# ── GET /api/v3/autocomplete ────────────────────────────────────────────────


class AutocompleteResult(BaseModel):
    """Résultat allégé pour l'autocomplétion."""
    siret: str = Field(description="SIRET")
    denomination: Optional[str] = Field(default=None, description="Raison sociale")
    commune: Optional[str] = Field(default=None, description="Commune")
    code_postal: Optional[str] = Field(default=None, description="Code postal")
    naf: Optional[str] = Field(default=None, description="Code NAF")


# ── GET /api/v3/referentiel/opco ────────────────────────────────────────────


class OpcoReferentiel(BaseModel):
    """Un OPCO dans le référentiel."""
    nom: str = Field(description="Nom de l'OPCO")
    secteurs: str = Field(default="", description="Secteurs d'activité couverts")
