import type { SearchFacets } from '../../types/api';
import { tailleLabel } from '../../utils/formatters';

interface FacetsProps {
  facets: SearchFacets;
}

export function Facets({ facets }: FacetsProps) {
  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      {/* Tailles */}
      {Object.keys(facets.tailles).length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold text-gray-500 uppercase">Par taille</h4>
          <div className="space-y-1">
            {Object.entries(facets.tailles).map(([key, count]) => (
              <div key={key} className="flex items-center justify-between text-sm">
                <span className="text-gray-700">{tailleLabel(key)}</span>
                <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">
                  {count.toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Top NAF */}
      {facets.top_naf.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold text-gray-500 uppercase">Top activités</h4>
          <div className="space-y-1">
            {facets.top_naf.slice(0, 5).map((n) => (
              <div key={n.code} className="flex items-center justify-between text-sm">
                <span className="min-w-0 truncate text-gray-700" title={n.libelle}>
                  {n.code} {n.libelle && `— ${n.libelle}`}
                </span>
                <span className="ml-2 shrink-0 rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">
                  {n.count.toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
