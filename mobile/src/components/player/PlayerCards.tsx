import { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Pressable } from '../Pressable';

import type { PlayerPage } from '../../lib/api';
import { colors } from '../../lib/theme';

const RED = '#E8826F';
const AMBER = '#E2B655';
const TONE = { easy: colors.live, avg: AMBER, tough: RED } as const;

// Role on the team: the top three at his position group by share of the work, then snap share, his share and who's out.
export function RoleCard({ role }: { role: NonNullable<PlayerPage['role']> }) {
  return (
    <View style={styles.card}>
      <Text style={styles.title}>Role on the team</Text>
      <View style={styles.chips}>
        {role.chips.map((c) => (
          <View key={`${c.slot}-${c.name}`} style={[styles.chip, c.me && styles.chipMe]}>
            <Text style={styles.chipName} numberOfLines={1}>{c.slot} · {c.name}</Text>
            <Text style={styles.chipShare}>{c.share}%</Text>
          </View>
        ))}
      </View>
      <View>
        {role.rows.map((r, i) => (
          <View key={r.label} style={[styles.kv, i === role.rows.length - 1 && styles.last]}>
            <Text style={styles.kvLabel}>{r.label}</Text>
            <Text style={styles.kvValue} numberOfLines={1}>{r.value}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

// Rest of season: every game left, coloured by how that defense treats his position; playoff weeks outlined in gold.
export function RestOfSeasonCard({ rest, pos }: { rest: NonNullable<PlayerPage['rest_of_season']>; pos: string }) {
  const hasPlayoffs = rest.weeks.some((w) => w.playoff);
  return (
    <View style={styles.card}>
      <View style={styles.head}>
        <Text style={styles.title}>Rest of season</Text>
        {hasPlayoffs ? <Text style={styles.meta}>gold = playoffs</Text> : null}
      </View>
      <View style={styles.grid}>
        {rest.weeks.map((w) => (
          <View key={w.week} style={[styles.op, w.playoff && styles.opPlayoff]}>
            <Text style={styles.opWeek}>W{w.week}</Text>
            <Text style={[styles.opTeam, { color: w.tone ? TONE[w.tone] : colors.muted }]}>{w.home ? '' : '@'}{w.opp}</Text>
          </View>
        ))}
      </View>
      <View style={styles.legend}>
        {(['easy', 'avg', 'tough'] as const).map((t) => (
          <View key={t} style={styles.key}>
            <View style={[styles.dot, { backgroundColor: TONE[t] }]} />
            <Text style={styles.keyText}>{t === 'easy' ? `easy vs ${pos}s` : t === 'avg' ? 'average' : 'tough'}</Text>
          </View>
        ))}
      </View>
      {rest.playoff_rank && rest.teams && hasPlayoffs ? (
        <Text style={styles.small}>Playoff schedule ranks {ordinal(rest.playoff_rank)} easiest of {rest.teams} for {pos}s.</Text>
      ) : null}
    </View>
  );
}

// Past seasons: one line a season, newest first; four shown, the rest behind "All N seasons".
export function PastSeasonsCard({ past, pos }: { past: NonNullable<PlayerPage['past_seasons']>; pos: string }) {
  const [all, setAll] = useState(false);
  const rows = all ? past.seasons : past.seasons.slice(0, 4);
  return (
    <View style={styles.card}>
      <Text style={styles.title}>Past seasons</Text>
      <View style={styles.tr}>
        <Text style={[styles.th, styles.first]}>YEAR</Text>
        <Text style={styles.th}>G</Text>
        {past.columns.map((c) => <Text key={c.key} style={styles.th}>{c.label}</Text>)}
        <Text style={styles.th}>PPG</Text>
        <Text style={styles.th}>{pos}</Text>
      </View>
      {rows.map((s) => (
        <View key={s.season} style={[styles.tr, styles.trBody]}>
          <Text style={[styles.td, styles.first]}>{s.season}</Text>
          <Text style={styles.td}>{s.games}</Text>
          {past.columns.map((c) => <Text key={c.key} style={styles.td}>{fmt(s[c.key])}</Text>)}
          <Text style={styles.td}>{s.ppg != null ? s.ppg.toFixed(1) : '—'}</Text>
          <Text style={styles.td}>{s.finish ? `#${s.finish}` : '—'}</Text>
        </View>
      ))}
      <Text style={styles.note}>PPG in PPR points · {pos} = his finish at the position</Text>
      {past.seasons.length > 4 ? (
        <Pressable onPress={() => setAll(!all)} hitSlop={8}>
          <Text style={styles.link}>{all ? 'Show fewer ›' : `All ${past.seasons.length} seasons ›`}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

function fmt(v: number | null | undefined): string {
  if (v == null) return '—';
  return v >= 1000 ? v.toLocaleString('en-US') : String(v);
}

function ordinal(n: number): string {
  const s = n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th';
  return `${n}${s}`;
}

const styles = StyleSheet.create({
  card: { backgroundColor: colors.card, borderRadius: 16, padding: 16, gap: 10 },
  head: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' },
  title: { color: colors.text, fontSize: 15, fontWeight: '700' },
  meta: { color: colors.muted, fontSize: 12 },
  small: { color: colors.muted, fontSize: 12 },
  note: { color: '#8E8676', fontSize: 11 },
  link: { color: colors.gold, fontSize: 13, fontWeight: '700' },
  chips: { flexDirection: 'row', gap: 6 },
  chip: { flex: 1, backgroundColor: colors.row, borderRadius: 10, padding: 8, gap: 2 },
  chipMe: { borderWidth: 1.5, borderColor: colors.gold },
  chipName: { color: colors.text, fontSize: 12, fontWeight: '700' },
  chipShare: { color: colors.muted, fontSize: 12 },
  kv: { flexDirection: 'row', justifyContent: 'space-between', gap: 10, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: colors.line },
  last: { borderBottomWidth: 0 },
  kvLabel: { color: '#8E8676', fontSize: 13 },
  kvValue: { color: colors.text, fontSize: 13, fontWeight: '600', flexShrink: 1, textAlign: 'right' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  op: { width: '23.5%', backgroundColor: colors.row, borderRadius: 10, paddingVertical: 7, alignItems: 'center', borderWidth: 1.5, borderColor: 'transparent' },
  opPlayoff: { borderColor: colors.gold },
  opWeek: { color: '#8E8676', fontSize: 10, fontWeight: '700', letterSpacing: 0.5 },
  opTeam: { fontSize: 14, fontWeight: '800' },
  legend: { flexDirection: 'row', gap: 12, flexWrap: 'wrap' },
  key: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  keyText: { color: '#8E8676', fontSize: 11 },
  tr: { flexDirection: 'row', alignItems: 'center' },
  trBody: { borderTopWidth: 1, borderTopColor: colors.line, paddingVertical: 7 },
  th: { flex: 1, color: '#8E8676', fontSize: 10, fontWeight: '700', letterSpacing: 0.6, textAlign: 'right' },
  td: { flex: 1, color: colors.text, fontSize: 13, textAlign: 'right' },
  first: { flex: 1.2, textAlign: 'left' },
});
