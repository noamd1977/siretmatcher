import { useQuery } from '@tanstack/react-query';
import { X, Copy, ExternalLink, CheckCircle, Sparkles, Mail } from 'lucide-react';
import { useState } from 'react';
import { getEtablissement, enrichEtablissement } from '../../api/client';
import { Badge } from '../common/Badge';
import { Spinner } from '../common/Spinner';
import { formatAdresse, formatSiret, formatEffectif } from '../../utils/formatters';
import type { EnrichResponse } from '../../types/api';

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
  const [enrichData, setEnrichData] = useState<EnrichResponse | null>(null);
  const [enrichLoading, setEnrichLoading] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(siret);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleEnrich = async () => {
    setEnrichLoading(true);
    try {
      const data = await enrichEtablissement(siret);
      setEnrichData(data);
    } catch {
      // silent
    } finally {
      setEnrichLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 pt-10 pb-10" onClick={onClose}>
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
                <Field label="Effectif établ." value={formatEffectif(etab.effectif)} />
                <Field label="Date de création" value={etab.date_creation} />
                <Field label="Adresse" value={formatAdresse(etab.adresse)} />
                <Field label="Région" value={etab.adresse.region} />
              </div>

              {/* Dirigeant + Entreprise (from matching or enrichment) */}
              {(etab.dirigeant?.nom || etab.entreprise?.categorie) && (
                <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
                  <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Entreprise</h4>
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    {etab.dirigeant?.nom && (
                      <Field
                        label="Dirigeant"
                        value={`${etab.dirigeant.prenom || ''} ${etab.dirigeant.nom}`.trim() +
                          (etab.dirigeant.fonction ? ` (${etab.dirigeant.fonction})` : '')}
                      />
                    )}
                    {etab.entreprise?.categorie && (
                      <Field label="Catégorie" value={etab.entreprise.categorie} />
                    )}
                    {etab.entreprise?.nature_juridique?.libelle && (
                      <Field label="Forme juridique" value={etab.entreprise.nature_juridique.libelle} />
                    )}
                    {etab.entreprise?.nombre_etablissements && (
                      <Field label="Établissements" value={String(etab.entreprise.nombre_etablissements)} />
                    )}
                    {etab.entreprise?.effectif_total && (
                      <Field label="Effectif total" value={etab.entreprise.effectif_total} />
                    )}
                  </div>
                </div>
              )}

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
                    {etab.idcc.libelle && ` — ${etab.idcc.libelle.slice(0, 60)}…`}
                  </Badge>
                )}
              </div>

              {/* Enrichment section */}
              {!enrichData && (
                <button
                  onClick={handleEnrich}
                  disabled={enrichLoading}
                  className="inline-flex items-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm font-medium text-blue-700 transition hover:bg-blue-100 disabled:opacity-50"
                >
                  {enrichLoading ? <Spinner className="h-4 w-4" /> : <Sparkles size={16} />}
                  Enrichir (dirigeant, données financières…)
                </button>
              )}

              {enrichData && (
                <div className="rounded-lg border border-blue-100 bg-blue-50/50 p-4">
                  <h4 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-blue-600">
                    <Sparkles size={14} /> Données enrichies
                    <span className="font-normal normal-case text-blue-400">
                      — sources: {enrichData.sources.join(', ') || 'aucune'}
                    </span>
                  </h4>
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    {enrichData.dirigeant?.nom && (
                      <Field
                        label="Dirigeant"
                        value={`${enrichData.dirigeant.prenom || ''} ${enrichData.dirigeant.nom}`.trim() +
                          (enrichData.dirigeant.fonction ? ` (${enrichData.dirigeant.fonction})` : '')}
                      />
                    )}
                    {enrichData.entreprise?.categorie && (
                      <Field label="Catégorie" value={enrichData.entreprise.categorie} />
                    )}
                    {enrichData.entreprise?.nature_juridique?.libelle && (
                      <Field label="Forme juridique" value={enrichData.entreprise.nature_juridique.libelle} />
                    )}
                    {enrichData.financier?.chiffre_affaires && (
                      <Field label="Chiffre d'affaires" value={enrichData.financier.chiffre_affaires} />
                    )}
                    {enrichData.financier?.resultat_net && (
                      <Field label="Résultat net" value={enrichData.financier.resultat_net} />
                    )}
                    {enrichData.financier?.date_comptes && (
                      <Field label="Date comptes" value={enrichData.financier.date_comptes} />
                    )}
                  </div>

                  {/* Emails */}
                  {enrichData.emails && enrichData.emails.length > 0 && (
                    <div className="mt-3 border-t border-blue-100 pt-3">
                      <h5 className="mb-2 flex items-center gap-1 text-xs font-semibold text-blue-600">
                        <Mail size={12} /> Emails détectés
                      </h5>
                      <div className="space-y-1.5">
                        {enrichData.emails.map((em) => (
                          <div key={em.email} className="flex items-center gap-2 text-sm">
                            <span className={`inline-block h-2 w-2 rounded-full ${
                              em.confidence === 'verified' ? 'bg-green-500' :
                              em.confidence === 'probable' ? 'bg-amber-400' : 'bg-gray-400'
                            }`} />
                            <a href={`mailto:${em.email}`} className="text-blue-700 hover:underline">
                              {em.email}
                            </a>
                            <Badge variant={
                              em.confidence === 'verified' ? 'green' :
                              em.confidence === 'probable' ? 'orange' : 'gray'
                            }>
                              {em.confidence}
                            </Badge>
                            <button
                              onClick={() => { navigator.clipboard.writeText(em.email); }}
                              className="text-gray-400 hover:text-gray-600"
                              title="Copier"
                            >
                              <Copy size={12} />
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

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
