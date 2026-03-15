import { useState } from 'react';
import { Send, Bug, AlertCircle, CheckCircle2, MinusCircle } from 'lucide-react';
import { matchSingle } from '../../api/client';
import { Badge } from '../common/Badge';
import { Spinner } from '../common/Spinner';
import { formatAdresse, formatSiret, formatEffectif } from '../../utils/formatters';
import { BatchUpload } from './BatchUpload';
import type { MatchResponse } from '../../types/api';

export function MatchPage() {
  const [form, setForm] = useState({
    nom: '',
    adresse: '',
    code_postal: '',
    ville: '',
    telephone: '',
    site_web: '',
    email: '',
  });
  const [debug, setDebug] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<MatchResponse | null>(null);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.nom || !form.code_postal) return;
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const res = await matchSingle(form, debug);
      setResult(res);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erreur inconnue';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const update = (field: string, value: string) =>
    setForm((f) => ({ ...f, [field]: value }));

  return (
    <div className="mx-auto max-w-screen-xl px-4 py-6 lg:px-6">
      <h1 className="mb-6 text-2xl font-bold text-gray-900">Matching manuel</h1>

      <div className="grid gap-8 lg:grid-cols-2">
        {/* Form */}
        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <FormField label="Nom *" value={form.nom} onChange={(v) => update('nom', v)} placeholder="Google France" />
              <FormField label="Code postal *" value={form.code_postal} onChange={(v) => update('code_postal', v)} placeholder="75009" />
              <FormField label="Adresse" value={form.adresse} onChange={(v) => update('adresse', v)} placeholder="8 rue de Londres" />
              <FormField label="Ville" value={form.ville} onChange={(v) => update('ville', v)} placeholder="Paris" />
              <FormField label="Téléphone" value={form.telephone} onChange={(v) => update('telephone', v)} placeholder="" />
              <FormField label="Site web" value={form.site_web} onChange={(v) => update('site_web', v)} placeholder="https://..." />
              <FormField label="Email" value={form.email} onChange={(v) => update('email', v)} placeholder="" className="sm:col-span-2" />
            </div>

            <div className="flex items-center justify-between border-t border-gray-100 pt-4">
              <label className="flex items-center gap-2 text-sm text-gray-600">
                <input
                  type="checkbox"
                  checked={debug}
                  onChange={(e) => setDebug(e.target.checked)}
                  className="rounded border-gray-300 text-blue-600"
                />
                <Bug size={14} /> Mode debug
              </label>
              <button
                type="submit"
                disabled={loading || !form.nom || !form.code_postal}
                className="inline-flex items-center gap-2 rounded-lg bg-[#1e3a5f] px-5 py-2.5 text-sm font-medium text-white transition hover:bg-[#16304f] disabled:opacity-40"
              >
                {loading ? <Spinner className="h-4 w-4" /> : <Send size={16} />}
                Rechercher le SIRET
              </button>
            </div>
          </form>
        </div>

        {/* Result */}
        <div>
          {error && (
            <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4">
              <AlertCircle size={20} className="mt-0.5 shrink-0 text-red-500" />
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}

          {result && <MatchResult result={result} />}
        </div>
      </div>

      {/* Batch section */}
      <div className="mt-10">
        <BatchUpload />
      </div>
    </div>
  );
}

function FormField({
  label,
  value,
  onChange,
  placeholder,
  className = '',
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <div className={className}>
      <label className="mb-1 block text-xs font-medium text-gray-600">{label}</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-200 focus:outline-none"
      />
    </div>
  );
}

function ConfidenceBadge({ confidence }: { confidence: string | null }) {
  if (confidence === 'high')
    return <Badge variant="green">Confiance haute</Badge>;
  if (confidence === 'medium')
    return <Badge variant="orange">Confiance moyenne</Badge>;
  return <Badge variant="gray">Confiance basse</Badge>;
}

function MatchResult({ result }: { result: MatchResponse }) {
  const etab = result.etablissement;

  if (!result.matched || !etab) {
    return (
      <div className="rounded-xl border border-orange-200 bg-orange-50 p-6">
        <div className="flex items-start gap-3">
          <MinusCircle size={24} className="mt-0.5 shrink-0 text-orange-500" />
          <div>
            <h3 className="font-semibold text-orange-800">Aucun établissement trouvé</h3>
            <ul className="mt-2 space-y-1 text-sm text-orange-700">
              <li>Vérifiez l'orthographe du nom</li>
              <li>Ajoutez ou corrigez l'adresse</li>
              <li>Essayez avec un nom plus court (ex: "Google" au lieu de "Google France SARL")</li>
            </ul>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Score header */}
      <div className="flex items-center gap-3 rounded-xl border border-green-200 bg-green-50 px-5 py-3">
        <CheckCircle2 size={22} className="text-green-600" />
        <div className="flex-1">
          <span className="text-sm font-semibold text-green-800">Établissement trouvé</span>
        </div>
        <ConfidenceBadge confidence={result.confidence} />
        <span className="rounded-full bg-white px-3 py-1 text-sm font-bold text-green-800 shadow-sm">
          {result.score}%
        </span>
      </div>

      {/* Card */}
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <h3 className="text-lg font-bold text-gray-900">{etab.denomination || '—'}</h3>
        {etab.enseigne && etab.enseigne !== etab.denomination && (
          <p className="text-sm text-gray-500">{etab.enseigne}</p>
        )}

        <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
          <Field label="SIRET" value={formatSiret(etab.siret)} />
          <Field label="SIREN" value={etab.siren} />
          <Field label="NAF" value={`${etab.naf.code} — ${etab.naf.libelle}`} />
          <Field label="Effectif" value={formatEffectif(etab.effectif)} />
          <Field label="Adresse" value={formatAdresse(etab.adresse)} />
          <Field label="Méthode" value={result.methode} />
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          {etab.opco.nom && (
            <Badge variant="purple">OPCO: {etab.opco.nom}</Badge>
          )}
          {etab.idcc.code && (
            <Badge variant="blue">IDCC {etab.idcc.code}</Badge>
          )}
        </div>
      </div>

      {/* Debug */}
      {result.debug && (
        <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
            Debug — {result.debug.stages_tried} étape(s), {result.debug.duration_ms}ms
          </h4>
          <div className="space-y-1">
            {result.debug.stages.map((s, i) => (
              <div key={i} className="flex items-center gap-2 text-xs text-gray-700">
                <span className={s.found ? 'text-green-600' : 'text-gray-400'}>
                  {s.found ? '✓' : '✗'}
                </span>
                <span className="font-mono">{s.name}</span>
                {s.score != null && <span>score={s.score}</span>}
                {s.duration_ms != null && <span className="text-gray-400">{s.duration_ms}ms</span>}
              </div>
            ))}
          </div>
        </div>
      )}
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
