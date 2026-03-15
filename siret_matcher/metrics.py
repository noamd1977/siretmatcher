"""Métriques Prometheus pour le monitoring du SIRET Matcher."""

from prometheus_client import Counter, Histogram, Gauge

# Compteurs de requêtes par endpoint
REQUEST_COUNT = Counter(
    "siret_matcher_requests_total",
    "Total de requêtes reçues",
    ["endpoint", "method", "status"]
)

# Durée des requêtes
REQUEST_DURATION = Histogram(
    "siret_matcher_request_duration_seconds",
    "Durée des requêtes en secondes",
    ["endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

# Matching
MATCH_TOTAL = Counter(
    "siret_matcher_match_total",
    "Total de matchings effectués",
    ["result"]  # "matched" ou "not_found"
)

MATCH_SCORE = Histogram(
    "siret_matcher_match_score",
    "Distribution des scores de matching",
    buckets=[0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
)

MATCH_METHOD = Counter(
    "siret_matcher_match_method_total",
    "Matchings par méthode",
    ["method"]  # API_RECHERCHE_EXACT, TRIGRAM_FUZZY, etc.
)

MATCH_STAGES_TRIED = Histogram(
    "siret_matcher_match_stages_tried",
    "Nombre d'étapes tentées avant match",
    buckets=[1, 2, 3, 4, 5]
)

# DST Lookup
DST_LOOKUP_TOTAL = Counter(
    "siret_matcher_dst_lookup_total",
    "Total de lookups DST",
    ["found"]  # "true" ou "false"
)

# APIs externes
EXTERNAL_API_DURATION = Histogram(
    "siret_matcher_external_api_seconds",
    "Durée des appels aux APIs externes",
    ["api"],  # "recherche_entreprises", "ban"
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

EXTERNAL_API_ERRORS = Counter(
    "siret_matcher_external_api_errors_total",
    "Erreurs sur les APIs externes",
    ["api", "error_type"]  # timeout, http_error, connection_error
)

# BDD
DB_POOL_SIZE = Gauge(
    "siret_matcher_db_pool_size",
    "Taille actuelle du pool de connexions"
)

# Données
ETABLISSEMENTS_COUNT = Gauge(
    "siret_matcher_etablissements_total",
    "Nombre d'établissements en BDD"
)

# Cache Redis
CACHE_HITS = Counter(
    "siret_matcher_cache_hits_total",
    "Cache hits"
)

CACHE_MISSES = Counter(
    "siret_matcher_cache_misses_total",
    "Cache misses"
)
