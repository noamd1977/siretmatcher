import { useState, useRef, useEffect, useCallback } from 'react';
import { Search } from 'lucide-react';
import { useAutocomplete } from '../../hooks/useAutocomplete';
import type { AutocompleteItem } from '../../types/api';

interface SearchBarProps {
  onSearch: (q: string) => void;
  onSelectSiret: (siret: string) => void;
}

export function SearchBar({ onSearch, onSelectSiret }: SearchBarProps) {
  const [input, setInput] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);

  // Debounce 300ms
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(input.trim()), 300);
    return () => clearTimeout(t);
  }, [input]);

  const { data: suggestions } = useAutocomplete(debouncedQ);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter') {
        setOpen(false);
        onSearch(input.trim());
      }
      if (e.key === 'Escape') setOpen(false);
    },
    [input, onSearch],
  );

  const handleSelect = useCallback(
    (item: AutocompleteItem) => {
      setOpen(false);
      onSelectSiret(item.siret);
    },
    [onSelectSiret],
  );

  const showDropdown = open && debouncedQ.length >= 2 && suggestions && suggestions.length > 0;

  return (
    <div ref={wrapperRef} className="relative w-full">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={20} />
        <input
          type="text"
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          placeholder="Rechercher une entreprise (nom, activité, ville...)"
          className="w-full rounded-lg border border-gray-300 bg-white py-3 pl-10 pr-4 text-sm shadow-sm transition focus:border-blue-500 focus:ring-2 focus:ring-blue-200 focus:outline-none"
        />
      </div>

      {showDropdown && (
        <ul className="absolute z-50 mt-1 max-h-80 w-full overflow-auto rounded-lg border border-gray-200 bg-white shadow-lg">
          {suggestions!.map((item) => (
            <li
              key={item.siret}
              onMouseDown={() => handleSelect(item)}
              className="flex cursor-pointer items-center justify-between px-4 py-2.5 hover:bg-blue-50"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-gray-900">
                  {item.denomination || '—'}
                </p>
                <p className="text-xs text-gray-500">
                  {item.commune} {item.code_postal && `(${item.code_postal})`}
                </p>
              </div>
              {item.naf && (
                <span className="ml-3 shrink-0 rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
                  {item.naf}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
