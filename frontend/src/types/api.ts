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

export interface DirigeantInfo {
  nom: string | null;
  prenom: string | null;
  fonction: string | null;
}

export interface NatureJuridiqueInfo {
  code: string | null;
  libelle: string | null;
}

export interface EntrepriseInfo {
  categorie: string | null;
  nature_juridique: NatureJuridiqueInfo;
  nombre_etablissements: number | null;
  effectif_total: string | null;
}

export interface FinancierInfo {
  chiffre_affaires: string | null;
  resultat_net: string | null;
  date_comptes: string | null;
  source: string | null;
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
  dirigeant: DirigeantInfo;
  entreprise: EntrepriseInfo;
  date_creation: string | null;
  etat_administratif?: string | null;
}

export interface EmailResultItem {
  email: string;
  confidence: string;
  source: string;
  domain_has_mx: boolean;
}

export interface EnrichResponse {
  siret: string;
  dirigeant: DirigeantInfo;
  financier: FinancierInfo;
  entreprise: EntrepriseInfo;
  emails: EmailResultItem[];
  enriched_at: string | null;
  sources: string[];
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

export interface LeadScoreResponse {
  total: number;
  qualification: string;
  details: Record<string, number>;
  recommendations: string[];
}

export interface MatchResponse {
  matched: boolean;
  confidence: string | null;
  score: number;
  methode: string | null;
  etablissement: Etablissement | null;
  lead_score: LeadScoreResponse | null;
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

// Async batch types
export interface AsyncBatchResponse {
  job_id: string;
  status: string;
  total: number;
  estimated_duration_seconds: number;
  status_url: string;
}

export interface BatchJobStatus {
  job_id: string;
  status: 'queued' | 'processing' | 'completed' | 'failed';
  progress: {
    total: number;
    processed: number;
    matched: number;
    not_found: number;
    percent: number;
  };
  created_at: string;
  started_at?: string;
  completed_at?: string;
  duration_seconds?: number;
  eta_seconds?: number;
  results_url?: string;
  download_csv_url?: string;
  error?: string;
}

export interface BatchResultsPage {
  job_id: string;
  total: number;
  offset: number;
  limit: number;
  results: BatchResultItem[];
}

export interface BatchResultItem {
  matched: boolean;
  score: number;
  methode: string;
  siret?: string;
  siren?: string;
  denomination?: string;
  prospect_nom?: string;
  error?: string;
}

// Webhook types
export interface WebhookInfo {
  id: string;
  name: string;
  url: string;
  events: string[];
  active: boolean;
  retry: number;
  timeout: number;
}

export interface WebhookLogEntry {
  webhook_id: string;
  event: string;
  status: string;
  status_code: number | null;
  error: string | null;
  timestamp: string;
  duration_ms: number;
}

export interface WebhookTestResult {
  webhook_id: string;
  status_code?: number;
  duration_ms?: number;
  success: boolean;
  error?: string;
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
