import type { SearchResultItem } from '../../types/api';
import { ResultRow } from './ResultRow';
import { EmptyState } from '../common/EmptyState';

interface ResultsTableProps {
  results: SearchResultItem[];
  total: number;
  onSelect: (siret: string) => void;
}

export function ResultsTable({ results, total, onSelect }: ResultsTableProps) {
  if (results.length === 0) {
    return <EmptyState />;
  }

  return (
    <div className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <div className="border-b border-gray-100 bg-gray-50 px-4 py-2.5">
        <span className="text-sm font-medium text-gray-700">
          {total.toLocaleString()} résultat{total > 1 ? 's' : ''}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
              <th className="px-4 py-3">Dénomination</th>
              <th className="px-4 py-3">SIRET</th>
              <th className="px-4 py-3">Commune</th>
              <th className="px-4 py-3">NAF</th>
              <th className="px-4 py-3">Effectif</th>
              <th className="px-4 py-3">OPCO</th>
            </tr>
          </thead>
          <tbody>
            {results.map((item) => (
              <ResultRow
                key={item.siret}
                item={item}
                onClick={() => onSelect(item.siret)}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
