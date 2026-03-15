import type { SearchResultItem } from '../../types/api';
import { Badge } from '../common/Badge';
import { formatSiret, formatEffectif } from '../../utils/formatters';

interface ResultRowProps {
  item: SearchResultItem;
  onClick: () => void;
}

function effectifVariant(code: string): 'green' | 'orange' | 'blue' | 'gray' {
  const big = ['21', '22', '31', '32', '41', '42', '51', '52', '53'];
  const mid = ['11', '12'];
  if (big.includes(code)) return 'blue';
  if (mid.includes(code)) return 'orange';
  return 'gray';
}

export function ResultRow({ item, onClick }: ResultRowProps) {
  return (
    <tr
      onClick={onClick}
      className="cursor-pointer border-b border-gray-100 transition hover:bg-blue-50/60"
    >
      <td className="px-4 py-3">
        <p className="text-sm font-medium text-gray-900">{item.denomination || '—'}</p>
        {item.enseigne && item.enseigne !== item.denomination && (
          <p className="text-xs text-gray-500">{item.enseigne}</p>
        )}
      </td>
      <td className="px-4 py-3 font-mono text-xs text-gray-600">{formatSiret(item.siret)}</td>
      <td className="px-4 py-3 text-sm text-gray-700">
        {item.adresse.commune}
        {item.adresse.code_postal && (
          <span className="ml-1 text-xs text-gray-400">({item.adresse.code_postal})</span>
        )}
      </td>
      <td className="px-4 py-3">
        {item.naf.code && (
          <span className="text-xs text-gray-600" title={item.naf.libelle}>
            {item.naf.code}
          </span>
        )}
      </td>
      <td className="px-4 py-3">
        {item.effectif.code && (
          <Badge variant={effectifVariant(item.effectif.code)}>
            {formatEffectif(item.effectif)}
          </Badge>
        )}
      </td>
      <td className="px-4 py-3">
        {item.opco && <Badge variant="purple">{item.opco}</Badge>}
      </td>
    </tr>
  );
}
