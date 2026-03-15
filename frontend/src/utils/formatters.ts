import type { AdresseInfo, EffectifInfo } from '../types/api';

export function formatAdresse(a: AdresseInfo | null | undefined): string {
  if (!a) return '';
  const parts = [a.numero, a.voie].filter(Boolean);
  const line1 = parts.join(' ');
  const line2 = [a.code_postal, a.commune].filter(Boolean).join(' ');
  return [line1, line2].filter(Boolean).join(', ');
}

export function formatEffectif(e: EffectifInfo | null | undefined): string {
  if (!e) return '';
  if (e.libelle) return e.libelle + ' sal.';
  return e.code || '';
}

export function tailleLabel(code: string): string {
  const map: Record<string, string> = {
    MOINS_11: '< 11',
    DE_11_A_49: '11-49',
    PLUS_DE_50: '50+',
    TOUTES: 'Toutes',
  };
  return map[code] || code;
}

export function formatSiret(siret: string): string {
  // 443 061 841 00047
  if (siret.length !== 14) return siret;
  return `${siret.slice(0, 3)} ${siret.slice(3, 6)} ${siret.slice(6, 9)} ${siret.slice(9)}`;
}

export function regionLabel(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/De /g, 'de ')
    .replace(/Et /g, 'et ');
}
