export interface NafInfo {
  code: string;
  libelle: string;
}

export interface EffectifInfo {
  code: string;
  libelle: string;
}

export interface AdresseInfo {
  numero: string | null;
  voie: string | null;
  code_postal: string | null;
  commune: string | null;
  departement: string | null;
  region: string | null;
}

export interface OpcoInfo {
  nom: string | null;
  source: string | null;
}

export interface IdccInfo {
  code: string | null;
  libelle: string | null;
}

export interface Etablissement {
  siret: string;
  siren: string;
  denomination: string | null;
  enseigne: string | null;
  naf: NafInfo;
  effectif: EffectifInfo;
  adresse: AdresseInfo;
  opco: OpcoInfo;
  idcc: IdccInfo;
  date_creation: string | null;
  etat_administratif?: string | null;
}

export interface SearchResultItem {
  siret: string;
  siren: string;
  denomination: string | null;
  enseigne: string | null;
  naf: NafInfo;
  effectif: EffectifInfo;
  adresse: AdresseInfo;
  opco: string | null;
  idcc: IdccInfo;
  date_creation: string | null;
}

export interface SearchFacets {
  departements: Record<string, number>;
  tailles: Record<string, number>;
  top_naf: { code: string; libelle: string; count: number }[];
}

export interface SearchFilters {
  departements?: string[];
  taille?: string;
  idcc?: string;
  naf_prefix?: string;
  etat?: string;
}

export interface SearchRequest {
  q?: string;
  filters: SearchFilters;
  sort?: string;
  limit: number;
  offset: number;
}

export interface SearchResponse {
  total: number;
  results: SearchResultItem[];
  facets: SearchFacets;
}

export interface AutocompleteItem {
  siret: string;
  denomination: string | null;
  commune: string | null;
  code_postal: string | null;
  naf: string | null;
}

export interface IdccReferentiel {
  idcc: string;
  libelle: string;
  count: number;
}

export interface OpcoReferentiel {
  nom: string;
  secteurs: string;
}

export type RegionsMap = Record<string, string[]>;

// Match types
export interface MatchRequest {
  nom: string;
  adresse?: string;
  code_postal: string;
  ville?: string;
  telephone?: string;
  site_web?: string;
  email?: string;
}

export interface StageDebug {
  name: string;
  found: boolean;
  score: number | null;
  duration_ms: number | null;
}

export interface MatchDebug {
  stages_tried: number;
  duration_ms: number;
  stages: StageDebug[];
}

export interface MatchResponse {
  matched: boolean;
  confidence: string | null;
  score: number;
  methode: string | null;
  etablissement: Etablissement | null;
  debug: MatchDebug | null;
}

export interface BatchRequest {
  prospects: MatchRequest[];
  concurrency: number;
}

export interface BatchResponse {
  total: number;
  matched: number;
  not_found: number;
  taux_matching: number;
  duration_ms: number;
  results: MatchResponse[];
}

// Stats types
export interface StatsResponse {
  matching: {
    total: number;
    matched: number;
    not_found: number;
    taux: number;
    avg_score: number;
    by_method: Record<string, number>;
  };
  dst_lookups: {
    total: number;
    found: number;
    not_found: number;
    cache_hit_rate: number;
  };
  system: {
    etablissements_actifs: number;
    siret_opco_count: number;
    db_pool_size: number;
    redis_connected: boolean;
    uptime_seconds: number;
  };
}
