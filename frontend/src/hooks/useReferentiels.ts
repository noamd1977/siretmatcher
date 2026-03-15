import { useQuery } from '@tanstack/react-query';
import { getRegions, getIdcc, getOpco } from '../api/client';

export function useRegions() {
  return useQuery({ queryKey: ['regions'], queryFn: getRegions });
}

export function useIdcc() {
  return useQuery({ queryKey: ['idcc'], queryFn: getIdcc });
}

export function useOpco() {
  return useQuery({ queryKey: ['opco'], queryFn: getOpco });
}
