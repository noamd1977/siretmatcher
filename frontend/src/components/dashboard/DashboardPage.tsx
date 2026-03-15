import { useQuery } from '@tanstack/react-query';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from 'recharts';
import {
  Activity, Database, Server, Wifi, WifiOff,
  Target, TrendingUp, Clock,
} from 'lucide-react';
import { getStats } from '../../api/client';
import { Spinner } from '../common/Spinner';

const PIE_COLORS = ['#22c55e', '#e5e7eb'];
const BAR_COLORS = [
  '#3b82f6', '#6366f1', '#8b5cf6', '#a855f7',
  '#ec4899', '#f59e0b', '#10b981', '#06b6d4',
];

export function DashboardPage() {
  const { data: stats, isLoading, error } = useQuery({
    queryKey: ['stats'],
    queryFn: getStats,
    refetchInterval: 30_000,
  });

  if (isLoading) {
    return (
      <div className="flex justify-center py-20">
        <Spinner className="h-8 w-8" />
      </div>
    );
  }

  if (error || !stats) {
    return (
      <div className="mx-auto max-w-screen-xl px-4 py-6">
        <p className="text-center text-red-500">Erreur lors du chargement des métriques.</p>
      </div>
    );
  }

  const m = stats.matching;
  const d = stats.dst_lookups;
  const s = stats.system;

  const methodData = Object.entries(m.by_method)
    .filter(([k]) => k !== 'NON_TROUVE')
    .map(([name, value]) => ({ name: shortMethodName(name), value, fullName: name }))
    .sort((a, b) => b.value - a.value);

  const pieData = [
    { name: 'Matchés', value: m.matched },
    { name: 'Non trouvés', value: m.not_found },
  ];

  const uptimeStr = formatUptime(s.uptime_seconds);

  return (
    <div className="mx-auto max-w-screen-xl px-4 py-6 lg:px-6">
      <h1 className="mb-6 text-2xl font-bold text-gray-900">Dashboard</h1>

      {/* KPI Cards */}
      <div className="mb-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard
          icon={<Target size={20} />}
          label="Taux de matching"
          value={m.total > 0 ? `${(m.taux * 100).toFixed(1)}%` : '—'}
          sub={`${m.matched.toLocaleString()} / ${m.total.toLocaleString()}`}
          color="green"
        />
        <KpiCard
          icon={<Activity size={20} />}
          label="Total matchings"
          value={m.total.toLocaleString()}
          sub={`${m.not_found.toLocaleString()} non trouvés`}
          color="blue"
        />
        <KpiCard
          icon={<TrendingUp size={20} />}
          label="Score moyen"
          value={m.avg_score > 0 ? `${m.avg_score}` : '—'}
          sub="sur 100"
          color="purple"
        />
        <KpiCard
          icon={<Clock size={20} />}
          label="Lookups DST"
          value={d.total.toLocaleString()}
          sub={`Cache hit: ${(d.cache_hit_rate * 100).toFixed(0)}%`}
          color="orange"
        />
      </div>

      {/* Charts */}
      <div className="mb-8 grid gap-6 lg:grid-cols-2">
        {/* Bar chart: methods */}
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="mb-4 text-sm font-semibold text-gray-700">Matchings par méthode</h3>
          {methodData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={methodData} layout="vertical" margin={{ left: 10, right: 20 }}>
                <XAxis type="number" fontSize={12} />
                <YAxis type="category" dataKey="name" width={100} fontSize={11} />
                <Tooltip
                  formatter={(value) => [Number(value).toLocaleString(), 'Matchings']}
                  labelFormatter={(_, payload) => payload?.[0]?.payload?.fullName || ''}
                />
                <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                  {methodData.map((_, i) => (
                    <Cell key={i} fill={BAR_COLORS[i % BAR_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-10 text-center text-sm text-gray-400">Aucune donnée de matching</p>
          )}
        </div>

        {/* Pie chart: matched vs not found */}
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="mb-4 text-sm font-semibold text-gray-700">Répartition des résultats</h3>
          {m.total > 0 ? (
            <div className="flex items-center justify-center">
              <ResponsiveContainer width="100%" height={280}>
                <PieChart>
                  <Pie
                    data={pieData}
                    cx="50%"
                    cy="50%"
                    innerRadius={60}
                    outerRadius={100}
                    paddingAngle={3}
                    dataKey="value"
                    label={({ name, percent }) => `${name} (${((percent ?? 0) * 100).toFixed(0)}%)`}
                    fontSize={12}
                  >
                    {pieData.map((_, i) => (
                      <Cell key={i} fill={PIE_COLORS[i]} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(value) => Number(value).toLocaleString()} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="py-10 text-center text-sm text-gray-400">Aucune donnée</p>
          )}
        </div>
      </div>

      {/* System cards */}
      <h2 className="mb-4 text-lg font-bold text-gray-900">Système</h2>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <SystemCard
          icon={<Database size={18} />}
          label="Établissements actifs"
          value={s.etablissements_actifs > 0 ? `${(s.etablissements_actifs / 1_000_000).toFixed(1)}M` : '—'}
        />
        <SystemCard
          icon={<Server size={18} />}
          label="SIRET-OPCO"
          value={s.siret_opco_count > 0 ? `${(s.siret_opco_count / 1_000_000).toFixed(1)}M` : '—'}
        />
        <SystemCard
          icon={s.redis_connected ? <Wifi size={18} /> : <WifiOff size={18} />}
          label="Redis"
          value={s.redis_connected ? 'Connecté' : 'Déconnecté'}
          valueColor={s.redis_connected ? 'text-green-600' : 'text-red-500'}
        />
        <SystemCard
          icon={<Clock size={18} />}
          label="Uptime"
          value={uptimeStr}
        />
      </div>

      {/* Evolution placeholder */}
      <div className="mt-8 rounded-xl border border-dashed border-gray-300 bg-gray-50 px-6 py-10 text-center">
        <p className="text-sm text-gray-400">
          Évolution dans le temps — bientôt disponible
        </p>
      </div>
    </div>
  );
}

function KpiCard({
  icon,
  label,
  value,
  sub,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub: string;
  color: 'green' | 'blue' | 'purple' | 'orange';
}) {
  const bgMap = {
    green: 'bg-green-50 text-green-600',
    blue: 'bg-blue-50 text-blue-600',
    purple: 'bg-purple-50 text-purple-600',
    orange: 'bg-orange-50 text-orange-600',
  };
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex items-center gap-3">
        <div className={`rounded-lg p-2 ${bgMap[color]}`}>{icon}</div>
        <div>
          <p className="text-xs font-medium text-gray-500">{label}</p>
          <p className="text-2xl font-bold text-gray-900">{value}</p>
          <p className="text-xs text-gray-400">{sub}</p>
        </div>
      </div>
    </div>
  );
}

function SystemCard({
  icon,
  label,
  value,
  valueColor = 'text-gray-900',
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  valueColor?: string;
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white px-4 py-3 shadow-sm">
      <div className="text-gray-400">{icon}</div>
      <div>
        <p className="text-xs text-gray-500">{label}</p>
        <p className={`text-sm font-semibold ${valueColor}`}>{value}</p>
      </div>
    </div>
  );
}

function shortMethodName(name: string): string {
  const map: Record<string, string> = {
    API_RECHERCHE_EXACT: 'API Exact',
    API_RECHERCHE_PROBABLE: 'API Probable',
    API_RECHERCHE_CP: 'API CP',
    ADDRESS_UNIQUE: 'Adresse',
    ADDRESS_BEST: 'Adresse Best',
    TRIGRAM_FUZZY: 'Trigramme',
    TRIGRAM_BEST: 'Trigramme Best',
    SCRAPE_MENTIONS_LEGALES: 'Scraping',
  };
  return map[name] || name;
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}min`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}min`;
  return `${Math.floor(seconds / 86400)}j ${Math.floor((seconds % 86400) / 3600)}h`;
}
