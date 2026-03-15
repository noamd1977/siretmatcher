import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { searchEtablissements } from '../api/client';
import type { SearchRequest } from '../types/api';

export function useSearch(params: SearchRequest, enabled: boolean) {
  return useQuery({
    queryKey: ['search', params],
    queryFn: () => searchEtablissements(params),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}
