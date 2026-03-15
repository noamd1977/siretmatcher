import type { LeadScoreResponse } from '../../types/api';

interface Props {
  lead: LeadScoreResponse;
  showScore?: boolean;
}

const QUAL_STYLES = {
  hot: 'bg-red-100 text-red-800 border-red-200',
  warm: 'bg-amber-100 text-amber-800 border-amber-200',
  cold: 'bg-blue-100 text-blue-700 border-blue-200',
};

const QUAL_LABELS = { hot: 'Hot', warm: 'Warm', cold: 'Cold' };

export function LeadScoreBadge({ lead, showScore = true }: Props) {
  const style = QUAL_STYLES[lead.qualification as keyof typeof QUAL_STYLES] || QUAL_STYLES.cold;
  const label = QUAL_LABELS[lead.qualification as keyof typeof QUAL_LABELS] || lead.qualification;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold ${style}`}>
      {label}
      {showScore && <span className="font-mono">{lead.total}</span>}
    </span>
  );
}

const CRITERIA_LABELS: Record<string, string> = {
  taille: 'Taille',
  opco: 'OPCO',
  secteur: 'Secteur',
  localisation: 'Localisation',
  anciennete: 'Ancienneté',
  completude: 'Complétude',
};

const CRITERIA_MAX: Record<string, number> = {
  taille: 30, opco: 25, secteur: 15, localisation: 10, anciennete: 10, completude: 10,
};

export function LeadScoreDetail({ lead }: { lead: LeadScoreResponse }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <LeadScoreBadge lead={lead} />
        <span className="text-sm text-gray-500">{lead.total}/100</span>
      </div>
      <div className="space-y-1.5">
        {Object.entries(lead.details).map(([key, val]) => {
          const max = CRITERIA_MAX[key] || 30;
          const pct = Math.round((val / max) * 100);
          return (
            <div key={key} className="flex items-center gap-2 text-xs">
              <span className="w-24 text-gray-500">{CRITERIA_LABELS[key] || key}</span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-200">
                <div
                  className="h-full rounded-full bg-blue-500 transition-all"
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span className="w-10 text-right font-mono text-gray-600">{val}/{max}</span>
            </div>
          );
        })}
      </div>
      {lead.recommendations.length > 0 && (
        <ul className="space-y-0.5 text-xs text-gray-600">
          {lead.recommendations.map((r, i) => (
            <li key={i} className="flex items-start gap-1">
              <span className="mt-0.5 text-amber-500">•</span> {r}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
