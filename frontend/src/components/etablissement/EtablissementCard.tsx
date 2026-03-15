import { useQuery } from '@tanstack/react-query';
import { X, Copy, ExternalLink, CheckCircle } from 'lucide-react';
import { useState } from 'react';
import { getEtablissement } from '../../api/client';
import { Badge } from '../common/Badge';
import { Spinner } from '../common/Spinner';
import { formatAdresse, formatSiret, formatEffectif } from '../../utils/formatters';

interface Props {
  siret: string;
  onClose: () => void;
}

export function EtablissementCard({ siret, onClose }: Props) {
  const { data: etab, isLoading, error } = useQuery({
    queryKey: ['etablissement', siret],
    queryFn: () => getEtablissement(siret),
  });
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(siret);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 pt-20" onClick={onClose}>
      <div
        className="relative w-full max-w-2xl rounded-xl border border-gray-200 bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
          <h2 className="text-lg font-bold text-gray-900">Fiche établissement</h2>
          <button onClick={onClose} className="rounded-full p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600">
            <X size={20} />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5">
          {isLoading && (
            <div className="flex justify-center py-10">
              <Spinner />
            </div>
          )}
          {error && (
            <p className="py-10 text-center text-sm text-red-500">
              Erreur lors du chargement.
            </p>
          )}
          {etab && (
            <div className="space-y-5">
              {/* Denomination */}
              <div>
                <h3 className="text-xl font-bold text-gray-900">{etab.denomination || '—'}</h3>
                {etab.enseigne && etab.enseigne !== etab.denomination && (
                  <p className="text-sm text-gray-500">{etab.enseigne}</p>
                )}
              </div>

              {/* SIRET + copy */}
              <div className="flex items-center gap-3">
                <span className="font-mono text-sm text-gray-700">{formatSiret(etab.siret)}</span>
                <button
                  onClick={handleCopy}
                  className="inline-flex items-center gap-1 rounded-md border border-gray-200 px-2 py-1 text-xs text-gray-500 transition hover:bg-gray-50"
                >
                  {copied ? <CheckCircle size={14} className="text-green-500" /> : <Copy size={14} />}
                  {copied ? 'Copié' : 'Copier'}
                </button>
              </div>

              {/* Info grid */}
              <div className="grid grid-cols-2 gap-4 text-sm">
                <Field label="SIREN" value={etab.siren} />
                <Field label="NAF" value={`${etab.naf.code} — ${etab.naf.libelle}`} />
                <Field label="Effectif" value={formatEffectif(etab.effectif)} />
                <Field label="Date de création" value={etab.date_creation} />
                <Field label="Adresse" value={formatAdresse(etab.adresse)} />
                <Field label="Région" value={etab.adresse.region} />
              </div>

              {/* OPCO / IDCC */}
              <div className="flex flex-wrap gap-2">
                {etab.opco.nom && (
                  <Badge variant="purple">
                    OPCO: {etab.opco.nom} ({etab.opco.source})
                  </Badge>
                )}
                {etab.idcc.code && (
                  <Badge variant="blue">
                    IDCC {etab.idcc.code}
                    {etab.idcc.libelle && ` — ${etab.idcc.libelle.slice(0, 60)}...`}
                  </Badge>
                )}
              </div>

              {/* Links */}
              <div className="flex gap-3 border-t border-gray-100 pt-4">
                <a
                  href={`https://www.pappers.fr/entreprise/${etab.siren}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 rounded-md border border-gray-200 px-3 py-1.5 text-xs text-gray-600 transition hover:bg-gray-50"
                >
                  Pappers <ExternalLink size={12} />
                </a>
                <a
                  href={`https://www.societe.com/societe/-${etab.siren}.html`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 rounded-md border border-gray-200 px-3 py-1.5 text-xs text-gray-600 transition hover:bg-gray-50"
                >
                  Societe.com <ExternalLink size={12} />
                </a>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <dt className="text-xs font-medium text-gray-500">{label}</dt>
      <dd className="mt-0.5 text-gray-900">{value || '—'}</dd>
    </div>
  );
}
