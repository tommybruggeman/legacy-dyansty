import type { GameLogRow } from '../../lib/api';

// One game's NFL box score as two short lines, by position.
export function lineText(row: GameLogRow, pos: string): [string, string] {
  const l = row.line;
  if (row.status === 'bye') return ['No game', ''];
  if (!l) return row.status === 'out' ? ['Did not play', ''] : ['No NFL stats', ''];
  const td = (n: number) => (n ? ` · ${n} TD` : '');
  const rush = l.carries ? `${l.carries} car · ${l.rush_yds} yds${td(l.rush_td)}` : '';
  const rec = l.rec || l.targets ? `${l.rec} rec · ${l.rec_yds} yds${td(l.rec_td)}` : '';
  if (pos === 'QB' && l.att) {
    const pass = `${l.comp}/${l.att} · ${l.pass_yds} yds${td(l.pass_td ?? 0)}${l.ints ? ` · ${l.ints} INT` : ''}`;
    return [pass, rush];
  }
  if (pos === 'RB') return [rush || rec || 'No touches', rush ? rec : ''];
  return [rec || rush || 'No catches', rec ? rush : ''];
}

export const STATUS: Record<GameLogRow['status'], string> = {
  started: 'STARTED',
  bench: 'BENCH',
  out: 'OUT',
  bye: 'BYE',
  free_agent: 'NOT ROSTERED',
};

export const pts = (n: number | null | undefined) => (n == null ? '—' : n.toFixed(1));
