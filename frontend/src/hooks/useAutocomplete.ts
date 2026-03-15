import { useQuery } from '@tanstack/react-query';
import { autocomplete } from '../api/client';

export function useAutocomplete(q: string) {
  return useQuery({
    queryKey: ['autocomplete', q],
    queryFn: () => autocomplete(q),
    enabled: q.length >= 2,
    staleTime: 60_000,
  });
}
