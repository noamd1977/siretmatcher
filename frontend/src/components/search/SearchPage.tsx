import { useState, useCallback, useEffect, useRef } from 'react';
import { SearchBar } from './SearchBar';
import { Filters } from './Filters';
import { ResultsTable } from './ResultsTable';
import { Pagination } from './Pagination';
import { Facets } from './Facets';
import { EtablissementCard } from '../etablissement/EtablissementCard';
import { Spinner } from '../common/Spinner';
import { useSearch } from '../../hooks/useSearch';
import type { SearchFilters, SearchRequest } from '../../types/api';
import { Menu, X } from 'lucide-react';

const DEFAULT_FILTERS: SearchFilters = {};
const LIMIT = 50;

export function SearchPage() {
  const [query, setQuery] = useState('');
  const [filters, setFilters] = useState<SearchFilters>(DEFAULT_FILTERS);
  const [offset, setOffset] = useState(0);
  const [selectedSiret, setSelectedSiret] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Debounce filters changes
  const [debouncedFilters, setDebouncedFilters] = useState(filters);
  const timerRef = useRef<ReturnType<typeof setTimeout>>(null);

  useEffect(() => {
    timerRef.current = setTimeout(() => setDebouncedFilters(filters), 500);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [filters]);

  // Reset offset on filter/query change
  useEffect(() => {
    setOffset(0);
  }, [query, debouncedFilters]);

  // Build search params
  const hasAnyCriteria =
    !!query ||
    !!debouncedFilters.departements?.length ||
    !!debouncedFilters.idcc ||
    !!debouncedFilters.naf_prefix;

  const searchParams: SearchRequest = {
    q: query || undefined,
    filters: debouncedFilters,
    sort: query ? 'relevance' : 'denomination',
    limit: LIMIT,
    offset,
  };

  const { data, isLoading, isFetching } = useSearch(searchParams, hasAnyCriteria);

  const handleSearch = useCallback((q: string) => {
    setQuery(q);
  }, []);

  const handleSelectSiret = useCallback((siret: string) => {
    setSelectedSiret(siret);
  }, []);

  const handleResetFilters = useCallback(() => {
    setFilters(DEFAULT_FILTERS);
    setQuery('');
  }, []);

  return (
    <div className="mx-auto max-w-screen-2xl px-4 py-6 lg:px-6">
      {/* Mobile sidebar toggle */}
      <button
        onClick={() => setSidebarOpen(true)}
        className="mb-4 inline-flex items-center gap-2 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 lg:hidden"
      >
        <Menu size={16} /> Filtres
      </button>

      <div className="flex gap-6">
        {/* Sidebar - desktop */}
        <div className="hidden w-72 shrink-0 lg:block">
          <Filters filters={filters} onChange={setFilters} onReset={handleResetFilters} />
          {data?.facets && <div className="mt-4"><Facets facets={data.facets} /></div>}
        </div>

        {/* Sidebar - mobile drawer */}
        {sidebarOpen && (
          <div className="fixed inset-0 z-40 lg:hidden">
            <div className="absolute inset-0 bg-black/30" onClick={() => setSidebarOpen(false)} />
            <div className="absolute inset-y-0 left-0 w-80 overflow-y-auto bg-white p-4 shadow-xl">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-sm font-semibold text-gray-700">Filtres</h2>
                <button onClick={() => setSidebarOpen(false)} className="text-gray-400 hover:text-gray-600">
                  <X size={20} />
                </button>
              </div>
              <Filters filters={filters} onChange={setFilters} onReset={handleResetFilters} />
              {data?.facets && <div className="mt-4"><Facets facets={data.facets} /></div>}
            </div>
          </div>
        )}

        {/* Main content */}
        <div className="min-w-0 flex-1">
          <SearchBar onSearch={handleSearch} onSelectSiret={handleSelectSiret} />

          <div className="mt-5">
            {!hasAnyCriteria && (
              <div className="rounded-lg border border-dashed border-gray-300 bg-white py-16 text-center">
                <p className="text-gray-500">
                  Saisissez un terme de recherche ou sélectionnez des filtres pour commencer.
                </p>
              </div>
            )}

            {hasAnyCriteria && isLoading && (
              <div className="flex justify-center py-16">
                <Spinner className="h-8 w-8" />
              </div>
            )}

            {hasAnyCriteria && data && (
              <>
                <div className="relative">
                  {isFetching && !isLoading && (
                    <div className="absolute right-2 top-3">
                      <Spinner className="h-4 w-4" />
                    </div>
                  )}
                  <ResultsTable
                    results={data.results}
                    total={data.total}
                    onSelect={handleSelectSiret}
                  />
                </div>
                <Pagination
                  total={data.total}
                  limit={LIMIT}
                  offset={offset}
                  onChange={setOffset}
                />
              </>
            )}
          </div>
        </div>
      </div>

      {/* Modal */}
      {selectedSiret && (
        <EtablissementCard
          siret={selectedSiret}
          onClose={() => setSelectedSiret(null)}
        />
      )}
    </div>
  );
}
