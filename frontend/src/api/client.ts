import axios from 'axios';
import { QueryClient } from '@tanstack/react-query';
import type {
  SearchRequest,
  SearchResponse,
  AutocompleteItem,
  Etablissement,
  RegionsMap,
  IdccReferentiel,
  OpcoReferentiel,
  MatchRequest,
  MatchResponse,
  BatchRequest,
  BatchResponse,
  StatsResponse,
  EnrichResponse,
  WebhookInfo,
  WebhookLogEntry,
  WebhookTestResult,
  AsyncBatchResponse,
  BatchJobStatus,
  BatchResultsPage,
} from '../types/api';

const api = axios.create({
  baseURL: '/api/v3',
  timeout: 10000,
});

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000,
      retry: 2,
    },
  },
});

export const searchEtablissements = (params: SearchRequest) =>
  api.post<SearchResponse>('/search', params).then((r) => r.data);

export const autocomplete = (q: string) =>
  api.get<AutocompleteItem[]>('/autocomplete', { params: { q, limit: 10 } }).then((r) => r.data);

export const getEtablissement = (siret: string) =>
  api.get<Etablissement>(`/etablissements/${siret}`).then((r) => r.data);

export const getRegions = () =>
  api.get<RegionsMap>('/referentiel/regions').then((r) => r.data);

export const getIdcc = () =>
  api.get<IdccReferentiel[]>('/referentiel/idcc').then((r) => r.data);

export const getOpco = () =>
  api.get<OpcoReferentiel[]>('/referentiel/opco').then((r) => r.data);

export const matchSingle = (data: MatchRequest, debug = false) =>
  api
    .post<MatchResponse>('/match', data, {
      headers: {
        ...(debug ? { 'X-Debug': 'true' } : {}),
        'X-API-Key': import.meta.env.VITE_API_KEY || '',
      },
    })
    .then((r) => r.data);

export const matchBatch = (data: BatchRequest) =>
  api
    .post<BatchResponse>('/match/batch', data, {
      headers: { 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
      timeout: 300_000,
    })
    .then((r) => r.data);

export const getStats = () =>
  api.get<StatsResponse>('/stats').then((r) => r.data);

export const enrichEtablissement = (siret: string) =>
  api.get<EnrichResponse>(`/etablissements/${siret}/enrich`).then((r) => r.data);

const apiKeyHeaders = { 'X-API-Key': import.meta.env.VITE_API_KEY || '' };

export const getWebhooks = () =>
  api.get<WebhookInfo[]>('/webhooks', { headers: apiKeyHeaders }).then((r) => r.data);

export const testWebhook = (id: string) =>
  api.post<WebhookTestResult>(`/webhooks/test/${id}`, null, { headers: apiKeyHeaders }).then((r) => r.data);

export const reloadWebhooks = () =>
  api.post<{ status: string; count: number }>('/webhooks/reload', null, { headers: apiKeyHeaders }).then((r) => r.data);

export const getWebhookLog = () =>
  api.get<WebhookLogEntry[]>('/webhooks/log', { headers: apiKeyHeaders }).then((r) => r.data);

export const createAsyncBatch = (data: { prospects: MatchRequest[]; concurrency?: number; callback_url?: string; webhook_events?: boolean }) =>
  api.post<AsyncBatchResponse>('/batch', data, { headers: apiKeyHeaders }).then((r) => r.data);

export const getBatchStatus = (jobId: string) =>
  api.get<BatchJobStatus>(`/batch/${jobId}`, { headers: apiKeyHeaders }).then((r) => r.data);

export const getBatchResults = (jobId: string, offset = 0, limit = 1000) =>
  api.get<BatchResultsPage>(`/batch/${jobId}/results`, { headers: apiKeyHeaders, params: { offset, limit } }).then((r) => r.data);

export const getBatchCsvUrl = (jobId: string) =>
  `/api/v3/batch/${jobId}/results.csv`;
