import { useState, useMemo } from 'react';
import { RotateCcw, ChevronDown, ChevronUp } from 'lucide-react';
import { useRegions, useIdcc } from '../../hooks/useReferentiels';
import type { SearchFilters } from '../../types/api';
import { tailleLabel, regionLabel } from '../../utils/formatters';

interface FiltersProps {
  filters: SearchFilters;
  onChange: (filters: SearchFilters) => void;
  onReset: () => void;
}

const TAILLES = ['TOUTES', 'MOINS_11', 'DE_11_A_49', 'PLUS_DE_50'] as const;

export function Filters({ filters, onChange, onReset }: FiltersProps) {
  const { data: regions } = useRegions();
  const { data: idccList } = useIdcc();
  const [selectedRegion, setSelectedRegion] = useState('');
  const [idccSearch, setIdccSearch] = useState('');
  const [showDepts, setShowDepts] = useState(false);

  const filteredIdcc = useMemo(() => {
    if (!idccList) return [];
    if (!idccSearch) return idccList.slice(0, 30);
    const q = idccSearch.toLowerCase();
    return idccList.filter(
      (i) => i.idcc.includes(q) || i.libelle.toLowerCase().includes(q),
    ).slice(0, 30);
  }, [idccList, idccSearch]);

  const handleRegionChange = (region: string) => {
    setSelectedRegion(region);
    if (!region || !regions) {
      onChange({ ...filters, departements: undefined });
    } else {
      onChange({ ...filters, departements: regions[region] });
    }
  };

  const handleDeptToggle = (dept: string) => {
    const current = filters.departements || [];
    const next = current.includes(dept)
      ? current.filter((d) => d !== dept)
      : [...current, dept];
    onChange({ ...filters, departements: next.length > 0 ? next : undefined });
  };

  const deptList = selectedRegion && regions ? regions[selectedRegion] : [];

  return (
    <aside className="space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Filtres</h2>
        <button
          onClick={onReset}
          className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-700"
        >
          <RotateCcw size={12} /> Réinitialiser
        </button>
      </div>

      {/* Région */}
      <div>
        <label className="mb-1 block text-xs font-medium text-gray-600">Région</label>
        <select
          value={selectedRegion}
          onChange={(e) => handleRegionChange(e.target.value)}
          className="w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-200 focus:outline-none"
        >
          <option value="">Toutes les régions</option>
          {regions &&
            Object.keys(regions).map((r) => (
              <option key={r} value={r}>
                {regionLabel(r)}
              </option>
            ))}
        </select>
      </div>

      {/* Départements */}
      {deptList.length > 0 && (
        <div>
          <button
            onClick={() => setShowDepts(!showDepts)}
            className="mb-1 flex w-full items-center justify-between text-xs font-medium text-gray-600"
          >
            Départements ({filters.departements?.length || deptList.length})
            {showDepts ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
          {showDepts && (
            <div className="max-h-40 space-y-1 overflow-y-auto rounded border border-gray-200 bg-white p-2">
              {deptList.map((d) => (
                <label key={d} className="flex items-center gap-2 text-sm text-gray-700">
                  <input
                    type="checkbox"
                    checked={filters.departements?.includes(d) ?? false}
                    onChange={() => handleDeptToggle(d)}
                    className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  />
                  {d}
                </label>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Taille */}
      <div>
        <label className="mb-1 block text-xs font-medium text-gray-600">Taille</label>
        <div className="space-y-1">
          {TAILLES.map((t) => (
            <label key={t} className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="radio"
                name="taille"
                checked={(filters.taille || 'TOUTES') === t}
                onChange={() =>
                  onChange({ ...filters, taille: t === 'TOUTES' ? undefined : t })
                }
                className="text-blue-600 focus:ring-blue-500"
              />
              {tailleLabel(t)}
            </label>
          ))}
        </div>
      </div>

      {/* IDCC */}
      <div>
        <label className="mb-1 block text-xs font-medium text-gray-600">Convention collective</label>
        <input
          type="text"
          placeholder="Chercher IDCC..."
          value={idccSearch}
          onChange={(e) => setIdccSearch(e.target.value)}
          className="mb-1 w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-200 focus:outline-none"
        />
        <select
          value={filters.idcc || ''}
          onChange={(e) => onChange({ ...filters, idcc: e.target.value || undefined })}
          className="w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-200 focus:outline-none"
          size={5}
        >
          <option value="">Toutes</option>
          {filteredIdcc.map((i) => (
            <option key={i.idcc} value={i.idcc}>
              {i.idcc} — {i.libelle} ({i.count.toLocaleString()})
            </option>
          ))}
        </select>
      </div>

      {/* NAF */}
      <div>
        <label className="mb-1 block text-xs font-medium text-gray-600">Code NAF (préfixe)</label>
        <input
          type="text"
          placeholder="ex: 62, 85.32..."
          value={filters.naf_prefix || ''}
          onChange={(e) =>
            onChange({ ...filters, naf_prefix: e.target.value || undefined })
          }
          className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-200 focus:outline-none"
        />
      </div>
    </aside>
  );
}
