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
