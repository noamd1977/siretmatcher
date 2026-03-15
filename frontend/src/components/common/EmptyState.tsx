import { SearchX } from 'lucide-react';

interface EmptyStateProps {
  title?: string;
  message?: string;
}

export function EmptyState({
  title = 'Aucun résultat',
  message = 'Modifiez vos critères de recherche.',
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-gray-400">
      <SearchX size={48} className="mb-4" />
      <p className="text-lg font-medium text-gray-600">{title}</p>
      <p className="text-sm">{message}</p>
    </div>
  );
}
