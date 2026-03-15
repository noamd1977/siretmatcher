import { useEffect, useState } from 'react';
import { Webhook, RefreshCw, Play, CheckCircle, XCircle, Clock, Info } from 'lucide-react';
import { getWebhooks, testWebhook, reloadWebhooks, getWebhookLog } from '../../api/client';
import { Badge } from '../common/Badge';
import { Spinner } from '../common/Spinner';
import type { WebhookInfo, WebhookLogEntry, WebhookTestResult } from '../../types/api';

export function IntegrationsPage() {
  const [webhooks, setWebhooks] = useState<WebhookInfo[]>([]);
  const [log, setLog] = useState<WebhookLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [testResults, setTestResults] = useState<Record<string, WebhookTestResult | null>>({});
  const [testingId, setTestingId] = useState<string | null>(null);

  const loadData = async () => {
    setLoading(true);
    setError('');
    try {
      const [wh, lg] = await Promise.all([getWebhooks(), getWebhookLog()]);
      setWebhooks(wh);
      setLog(lg);
    } catch {
      setError('Impossible de charger les webhooks. Verifiez votre API key.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadData(); }, []);

  const handleTest = async (id: string) => {
    setTestingId(id);
    setTestResults((prev) => ({ ...prev, [id]: null }));
    try {
      const result = await testWebhook(id);
      setTestResults((prev) => ({ ...prev, [id]: result }));
      // Refresh log
      const lg = await getWebhookLog();
      setLog(lg);
    } catch {
      setTestResults((prev) => ({ ...prev, [id]: { webhook_id: id, success: false, error: 'Erreur requete' } }));
    } finally {
      setTestingId(null);
    }
  };

  const handleReload = async () => {
    try {
      await reloadWebhooks();
      await loadData();
    } catch {
      setError('Erreur lors du rechargement.');
    }
  };

  return (
    <div className="mx-auto max-w-screen-xl px-6 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Webhook size={24} className="text-blue-600" />
          <h1 className="text-2xl font-bold text-gray-900">Integrations</h1>
        </div>
        <button
          onClick={handleReload}
          className="inline-flex items-center gap-2 rounded-md border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50"
        >
          <RefreshCw size={14} />
          Recharger la config
        </button>
      </div>

      {loading && (
        <div className="flex justify-center py-10"><Spinner /></div>
      )}

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>
      )}

      {!loading && !error && webhooks.length === 0 && (
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-8 text-center">
          <Info size={32} className="mx-auto mb-3 text-gray-400" />
          <h3 className="mb-2 font-semibold text-gray-700">Aucun webhook configure</h3>
          <p className="text-sm text-gray-500">
            Creez un fichier <code className="rounded bg-gray-200 px-1">config/webhooks.json</code> en
            vous basant sur <code className="rounded bg-gray-200 px-1">config/webhooks.example.json</code>.
          </p>
          <div className="mt-4 rounded-lg bg-gray-900 p-4 text-left text-xs text-green-400">
            <pre>{`{
  "webhooks": [
    {
      "id": "my-crm",
      "name": "Mon CRM",
      "url": "https://example.com/webhook",
      "events": ["match.success", "enrich.complete"],
      "active": true,
      "retry": 3,
      "timeout": 10
    }
  ]
}`}</pre>
          </div>
        </div>
      )}

      {!loading && webhooks.length > 0 && (
        <div className="space-y-4">
          {webhooks.map((wh) => {
            const result = testResults[wh.id];
            return (
              <div key={wh.id} className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
                <div className="flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="font-semibold text-gray-900">{wh.name}</h3>
                      <Badge variant={wh.active ? 'green' : 'gray'}>
                        {wh.active ? 'Actif' : 'Inactif'}
                      </Badge>
                    </div>
                    <p className="mt-1 font-mono text-xs text-gray-500">{wh.url}</p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {wh.events.map((ev) => (
                        <Badge key={ev} variant="blue">{ev}</Badge>
                      ))}
                    </div>
                    <p className="mt-2 text-xs text-gray-400">
                      Retry: {wh.retry} | Timeout: {wh.timeout}s
                    </p>
                  </div>
                  <div className="flex flex-col items-end gap-2">
                    <button
                      onClick={() => handleTest(wh.id)}
                      disabled={testingId === wh.id}
                      className="inline-flex items-center gap-1.5 rounded-md border border-blue-200 bg-blue-50 px-3 py-1.5 text-sm font-medium text-blue-700 transition hover:bg-blue-100 disabled:opacity-50"
                    >
                      {testingId === wh.id ? <Spinner className="h-3.5 w-3.5" /> : <Play size={14} />}
                      Tester
                    </button>
                    {result && (
                      <div className={`flex items-center gap-1 text-xs ${result.success ? 'text-green-600' : 'text-red-600'}`}>
                        {result.success ? <CheckCircle size={12} /> : <XCircle size={12} />}
                        {result.success ? `OK (${result.status_code}) - ${result.duration_ms}ms` : result.error || `Erreur ${result.status_code}`}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Log */}
      {log.length > 0 && (
        <div className="mt-8">
          <h2 className="mb-3 flex items-center gap-2 text-lg font-semibold text-gray-900">
            <Clock size={18} /> Derniers envois
          </h2>
          <div className="overflow-hidden rounded-lg border border-gray-200">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs font-medium uppercase text-gray-500">
                <tr>
                  <th className="px-4 py-2">Webhook</th>
                  <th className="px-4 py-2">Event</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Code</th>
                  <th className="px-4 py-2">Duree</th>
                  <th className="px-4 py-2">Date</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {log.slice(0, 20).map((entry, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-4 py-2 font-mono text-xs">{entry.webhook_id}</td>
                    <td className="px-4 py-2"><Badge variant="blue">{entry.event}</Badge></td>
                    <td className="px-4 py-2">
                      {entry.status === 'success'
                        ? <span className="flex items-center gap-1 text-green-600"><CheckCircle size={12} /> OK</span>
                        : <span className="flex items-center gap-1 text-red-600"><XCircle size={12} /> {entry.error || 'Erreur'}</span>
                      }
                    </td>
                    <td className="px-4 py-2 text-gray-600">{entry.status_code ?? '—'}</td>
                    <td className="px-4 py-2 text-gray-600">{entry.duration_ms ? `${entry.duration_ms}ms` : '—'}</td>
                    <td className="px-4 py-2 text-xs text-gray-400">{new Date(entry.timestamp).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
