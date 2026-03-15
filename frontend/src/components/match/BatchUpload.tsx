import { useState, useCallback, useRef, useEffect } from 'react';
import { Upload, FileSpreadsheet, Download, Play, X, Clock, CheckCircle } from 'lucide-react';
import Papa from 'papaparse';
import * as XLSX from 'xlsx';
import { matchBatch, createAsyncBatch, getBatchStatus, getBatchCsvUrl } from '../../api/client';
import { Badge } from '../common/Badge';
import { LeadScoreBadge } from '../common/LeadScoreBadge';
import { Spinner } from '../common/Spinner';
import type { MatchRequest, BatchResponse, MatchResponse, BatchJobStatus } from '../../types/api';

// Column name aliases for auto-detection
const ALIASES: Record<string, string[]> = {
  nom: ['nom', 'raison_sociale', 'raison sociale', 'company_name', 'entreprise', 'name', 'societe', 'société'],
  adresse: ['adresse', 'address', 'rue', 'voie'],
  code_postal: ['code_postal', 'cp', 'postal_code', 'zip', 'code postal'],
  ville: ['ville', 'city', 'commune'],
  telephone: ['telephone', 'tel', 'phone', 'téléphone'],
  site_web: ['site_web', 'site', 'website', 'url', 'web'],
  email: ['email', 'mail', 'e-mail', 'courriel'],
};

interface ColumnMapping {
  nom: string;
  adresse: string;
  code_postal: string;
  ville: string;
  telephone: string;
  site_web: string;
  email: string;
}

type ParsedRow = Record<string, string>;

function autoDetect(columns: string[]): Partial<ColumnMapping> {
  const mapping: Partial<ColumnMapping> = {};
  const lower = columns.map((c) => c.toLowerCase().trim());

  for (const [field, aliases] of Object.entries(ALIASES)) {
    const idx = lower.findIndex((col) => aliases.includes(col));
    if (idx >= 0) {
      (mapping as Record<string, string>)[field] = columns[idx];
    }
  }
  return mapping;
}

const CHUNK_SIZE = 50;
const ASYNC_THRESHOLD = 50;

export function BatchUpload() {
  const fileRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [rows, setRows] = useState<ParsedRow[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [mapping, setMapping] = useState<ColumnMapping>({
    nom: '', adresse: '', code_postal: '', ville: '',
    telephone: '', site_web: '', email: '',
  });
  const [result, setResult] = useState<BatchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [fileName, setFileName] = useState('');

  // Async batch state
  const [asyncJobId, setAsyncJobId] = useState<string | null>(null);
  const [asyncStatus, setAsyncStatus] = useState<BatchJobStatus | null>(null);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const handleFile = useCallback((file: File) => {
    setResult(null);
    setAsyncJobId(null);
    setAsyncStatus(null);
    setFileName(file.name);
    const ext = file.name.split('.').pop()?.toLowerCase();

    if (ext === 'csv' || ext === 'tsv' || ext === 'txt') {
      Papa.parse(file, {
        header: true,
        skipEmptyLines: true,
        complete: (res) => {
          const data = res.data as ParsedRow[];
          const cols = res.meta.fields || [];
          setRows(data);
          setColumns(cols);
          const detected = autoDetect(cols);
          setMapping((m) => ({ ...m, ...detected }));
        },
      });
    } else {
      // XLSX
      const reader = new FileReader();
      reader.onload = (e) => {
        const wb = XLSX.read(e.target?.result, { type: 'array' });
        const sheet = wb.Sheets[wb.SheetNames[0]];
        const data = XLSX.utils.sheet_to_json<ParsedRow>(sheet, { defval: '' });
        const cols = data.length > 0 ? Object.keys(data[0]) : [];
        setRows(data);
        setColumns(cols);
        const detected = autoDetect(cols);
        setMapping((m) => ({ ...m, ...detected }));
      };
      reader.readAsArrayBuffer(file);
    }
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  const canRun = mapping.nom && mapping.code_postal && rows.length > 0;
  const useAsync = rows.length > ASYNC_THRESHOLD;

  const buildProspects = (): MatchRequest[] =>
    rows.map((r) => ({
      nom: r[mapping.nom] || '',
      adresse: mapping.adresse ? r[mapping.adresse] || '' : '',
      code_postal: r[mapping.code_postal] || '',
      ville: mapping.ville ? r[mapping.ville] || '' : '',
      telephone: mapping.telephone ? r[mapping.telephone] || '' : '',
      site_web: mapping.site_web ? r[mapping.site_web] || '' : '',
      email: mapping.email ? r[mapping.email] || '' : '',
    }));

  const handleRunSync = async () => {
    if (!canRun) return;
    setLoading(true);
    setProgress(0);
    setResult(null);

    const prospects = buildProspects();
    const allResults: MatchResponse[] = [];
    let totalMatched = 0;
    const t0 = performance.now();

    for (let i = 0; i < prospects.length; i += CHUNK_SIZE) {
      const chunk = prospects.slice(i, i + CHUNK_SIZE);
      try {
        const res = await matchBatch({ prospects: chunk, concurrency: 5 });
        allResults.push(...res.results);
        totalMatched += res.matched;
      } catch {
        allResults.push(
          ...chunk.map(() => ({
            matched: false,
            confidence: null,
            score: 0,
            methode: 'ERREUR',
            etablissement: null,
            lead_score: null,
            debug: null,
          })),
        );
      }
      setProgress(Math.min(100, Math.round(((i + chunk.length) / prospects.length) * 100)));
    }

    const duration = Math.round(performance.now() - t0);
    setResult({
      total: prospects.length,
      matched: totalMatched,
      not_found: prospects.length - totalMatched,
      taux_matching: prospects.length > 0 ? totalMatched / prospects.length : 0,
      duration_ms: duration,
      results: allResults,
    });
    setLoading(false);
  };

  const handleRunAsync = async () => {
    if (!canRun) return;
    setLoading(true);
    setProgress(0);
    setResult(null);
    setAsyncStatus(null);

    const prospects = buildProspects();

    try {
      const resp = await createAsyncBatch({ prospects, concurrency: 10 });
      setAsyncJobId(resp.job_id);

      // Start polling
      pollRef.current = setInterval(async () => {
        try {
          const status = await getBatchStatus(resp.job_id);
          setAsyncStatus(status);
          setProgress(status.progress.percent);

          if (status.status === 'completed' || status.status === 'failed') {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setLoading(false);
          }
        } catch {
          // ignore poll errors
        }
      }, 2000);
    } catch {
      setLoading(false);
    }
  };

  const handleRun = useAsync ? handleRunAsync : handleRunSync;

  const handleExport = () => {
    if (!result) return;
    const exportRows = rows.map((r, i) => {
      const mr = result.results[i];
      const etab = mr?.etablissement;
      return {
        ...r,
        siret: etab?.siret || '',
        siren: etab?.siren || '',
        denomination_sirene: etab?.denomination || '',
        naf: etab?.naf?.code || '',
        effectif: etab?.effectif?.libelle || '',
        opco: etab?.opco?.nom || '',
        idcc: etab?.idcc?.code || '',
        score: mr?.score || 0,
        methode: mr?.methode || '',
        matched: mr?.matched ? 'OUI' : 'NON',
        lead_score_total: mr?.lead_score?.total || '',
        lead_qualification: mr?.lead_score?.qualification || '',
        lead_recommendations: mr?.lead_score?.recommendations?.join(' | ') || '',
      };
    });
    const ws = XLSX.utils.json_to_sheet(exportRows);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Résultats');
    XLSX.writeFile(wb, 'resultats_matching.xlsx');
  };

  const handleReset = () => {
    setRows([]);
    setColumns([]);
    setResult(null);
    setAsyncJobId(null);
    setAsyncStatus(null);
    setFileName('');
    setProgress(0);
    if (pollRef.current) clearInterval(pollRef.current);
    if (fileRef.current) fileRef.current.value = '';
  };

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
      <h2 className="mb-4 text-lg font-bold text-gray-900">Matching par lot</h2>

      {rows.length === 0 ? (
        /* Upload zone */
        <div
          onDrop={handleDrop}
          onDragOver={(e) => e.preventDefault()}
          className="flex flex-col items-center justify-center rounded-lg border-2 border-dashed border-gray-300 bg-gray-50 py-12 transition hover:border-blue-400"
        >
          <Upload size={40} className="mb-3 text-gray-400" />
          <p className="text-sm text-gray-600">
            Glissez-déposez un fichier CSV ou XLSX, ou{' '}
            <button
              onClick={() => fileRef.current?.click()}
              className="font-medium text-blue-600 hover:underline"
            >
              parcourir
            </button>
          </p>
          <p className="mt-1 text-xs text-gray-400">CSV, XLSX — jusqu'a 100 000+ lignes</p>
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.xlsx,.xls,.tsv,.txt"
            className="hidden"
            onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
          />
        </div>
      ) : (
        <div className="space-y-5">
          {/* File info */}
          <div className="flex items-center justify-between rounded-lg bg-gray-50 px-4 py-2">
            <div className="flex items-center gap-2 text-sm text-gray-700">
              <FileSpreadsheet size={16} />
              <span className="font-medium">{fileName}</span>
              <span className="text-gray-400">— {rows.length.toLocaleString()} lignes, {columns.length} colonnes</span>
              {useAsync && (
                <Badge variant="purple">Mode file d'attente</Badge>
              )}
            </div>
            <button onClick={handleReset} className="text-gray-400 hover:text-gray-600">
              <X size={16} />
            </button>
          </div>

          {/* Column mapping */}
          <div>
            <h3 className="mb-2 text-sm font-semibold text-gray-700">Mapping des colonnes</h3>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {(Object.keys(mapping) as (keyof ColumnMapping)[]).map((field) => (
                <div key={field}>
                  <label className="mb-1 block text-xs font-medium text-gray-500">
                    {field} {(field === 'nom' || field === 'code_postal') && '*'}
                  </label>
                  <select
                    value={mapping[field]}
                    onChange={(e) => setMapping((m) => ({ ...m, [field]: e.target.value }))}
                    className="w-full rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-200 focus:outline-none"
                  >
                    <option value="">— ignorer —</option>
                    {columns.map((c) => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
          </div>

          {/* Run button + progress */}
          <div className="flex items-center gap-4">
            <button
              onClick={handleRun}
              disabled={!canRun || loading}
              className="inline-flex items-center gap-2 rounded-lg bg-[#1e3a5f] px-5 py-2.5 text-sm font-medium text-white transition hover:bg-[#16304f] disabled:opacity-40"
            >
              {loading ? <Spinner className="h-4 w-4" /> : <Play size={16} />}
              {useAsync ? `Lancer en file d'attente (${rows.length.toLocaleString()})` : `Lancer le matching (${rows.length})`}
            </button>

            {loading && (
              <div className="flex flex-1 items-center gap-3">
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-gray-200">
                  <div
                    className="h-full rounded-full bg-blue-600 transition-all"
                    style={{ width: `${progress}%` }}
                  />
                </div>
                <span className="text-sm text-gray-500">{progress}%</span>
              </div>
            )}
          </div>

          {/* Async job status */}
          {asyncStatus && (
            <div className="rounded-lg border border-blue-100 bg-blue-50/50 p-4">
              <div className="flex items-center gap-2 text-sm">
                {asyncStatus.status === 'completed' ? (
                  <CheckCircle size={16} className="text-green-500" />
                ) : asyncStatus.status === 'failed' ? (
                  <X size={16} className="text-red-500" />
                ) : (
                  <Clock size={16} className="text-blue-500" />
                )}
                <span className="font-medium text-gray-900">
                  Job {asyncJobId?.slice(0, 8)}
                </span>
                <Badge variant={
                  asyncStatus.status === 'completed' ? 'green' :
                  asyncStatus.status === 'failed' ? 'orange' :
                  asyncStatus.status === 'processing' ? 'blue' : 'gray'
                }>
                  {asyncStatus.status}
                </Badge>
              </div>
              <div className="mt-2 grid grid-cols-4 gap-3 text-xs text-gray-600">
                <div>Total: {asyncStatus.progress.total.toLocaleString()}</div>
                <div>Traites: {asyncStatus.progress.processed.toLocaleString()}</div>
                <div>Matches: {asyncStatus.progress.matched.toLocaleString()}</div>
                <div>Non trouves: {asyncStatus.progress.not_found.toLocaleString()}</div>
              </div>
              {asyncStatus.eta_seconds && asyncStatus.status === 'processing' && (
                <p className="mt-1 text-xs text-gray-400">
                  ETA: ~{Math.round(asyncStatus.eta_seconds)}s
                </p>
              )}
              {asyncStatus.status === 'completed' && asyncStatus.duration_seconds && (
                <p className="mt-1 text-xs text-gray-400">
                  Termine en {asyncStatus.duration_seconds.toFixed(1)}s —
                  Taux: {asyncStatus.progress.total > 0
                    ? ((asyncStatus.progress.matched / asyncStatus.progress.total) * 100).toFixed(0)
                    : 0}%
                </p>
              )}
              {asyncStatus.status === 'completed' && asyncJobId && (
                <div className="mt-3 flex gap-2">
                  <a
                    href={getBatchCsvUrl(asyncJobId)}
                    className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-50"
                  >
                    <Download size={14} /> Telecharger CSV
                  </a>
                </div>
              )}
              {asyncStatus.status === 'failed' && asyncStatus.error && (
                <p className="mt-2 text-xs text-red-600">{asyncStatus.error}</p>
              )}
            </div>
          )}

          {/* Sync results */}
          {result && (
            <div className="space-y-4">
              {/* Summary */}
              <div className="flex flex-wrap items-center gap-3 rounded-lg bg-gray-50 px-4 py-3">
                <Badge variant="green">{result.matched} matches</Badge>
                <Badge variant="gray">{result.not_found} non trouves</Badge>
                <span className="text-sm text-gray-500">
                  Taux : {(result.taux_matching * 100).toFixed(0)}% — {(result.duration_ms / 1000).toFixed(1)}s
                </span>
                <button
                  onClick={handleExport}
                  className="ml-auto inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-50"
                >
                  <Download size={14} /> Exporter XLSX
                </button>
              </div>

              {/* Table */}
              <div className="max-h-96 overflow-auto rounded-lg border border-gray-200">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-gray-50 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                    <tr>
                      <th className="px-3 py-2">#</th>
                      <th className="px-3 py-2">Nom</th>
                      <th className="px-3 py-2">Statut</th>
                      <th className="px-3 py-2">SIRET trouve</th>
                      <th className="px-3 py-2">Score</th>
                      <th className="px-3 py-2">Methode</th>
                      <th className="px-3 py-2">Lead</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.results.map((mr, i) => (
                      <tr key={i} className="border-b border-gray-100">
                        <td className="px-3 py-2 text-gray-400">{i + 1}</td>
                        <td className="px-3 py-2 font-medium text-gray-900">
                          {rows[i]?.[mapping.nom] || '—'}
                        </td>
                        <td className="px-3 py-2">
                          {mr.matched ? (
                            <Badge variant="green">OK</Badge>
                          ) : (
                            <Badge variant="gray">—</Badge>
                          )}
                        </td>
                        <td className="px-3 py-2 font-mono text-xs">
                          {mr.etablissement?.siret || ''}
                        </td>
                        <td className="px-3 py-2">{mr.score > 0 ? `${mr.score}%` : ''}</td>
                        <td className="px-3 py-2 text-xs text-gray-500">{mr.methode}</td>
                        <td className="px-3 py-2">
                          {mr.lead_score && <LeadScoreBadge lead={mr.lead_score} />}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
