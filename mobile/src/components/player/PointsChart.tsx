import { useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Pressable } from '../Pressable';

import type { GameLogRow } from '../../lib/api';
import { colors } from '../../lib/theme';

const PLOT = 120;
const RED = '#E8826F';
const BOOM = '#4CC38A';
const SOLID = '#2C6B4C';

// Points by week: every bar shows its score, big weeks bright green, rough weeks red, and the picked game gold.
// Tap a bar to see that game's line in the callout; the gold button opens the full game log.
export function PointsChart({ weeks, avg, pos, onOpenLog }: { weeks: GameLogRow[]; avg: number | null; pos: string; onOpenLog: () => void }) {
  const shown = [...weeks].sort((a, b) => a.week - b.week).slice(-10);
  const played = (w: GameLogRow) => w.points != null && (w.status === 'started' || w.status === 'bench' || w.status === 'free_agent');
  const latest = [...shown].reverse().find(played)?.week ?? null;
  const [pick, setPick] = useState<number | null>(latest);
  useEffect(() => setPick(latest), [latest]);
  const best = Math.max(1, ...shown.map((w) => w.points ?? 0));
  const scale = (n: number) => Math.max(4, Math.round((n / best) * (PLOT - 22)));
  const sel = shown.find((w) => w.week === pick && played(w)) ?? null;
  const tone = (p: number) => (avg && p >= avg * 1.3 ? styles.boom : avg && p <= avg * 0.6 ? styles.bust : styles.solid);

  return (
    <View style={styles.card}>
      <View style={styles.head}>
        <Text style={styles.title}>Points by week</Text>
        {avg ? <Text style={styles.meta}>avg {avg.toFixed(1)}</Text> : null}
      </View>

      {sel ? (
        <Pressable onPress={onOpenLog} style={({ pressed }) => [styles.tip, pressed && { opacity: 0.8 }]}>
          <View style={{ flex: 1 }}>
            <Text style={styles.tipTitle} numberOfLines={1}>W{sel.week} {sel.opp} · {sel.points!.toFixed(1)} pts{sel.status === 'bench' ? ' (bench)' : ''}</Text>
            {statText(sel, pos) ? <Text style={styles.tipLine} numberOfLines={1}>{statText(sel, pos)}</Text> : null}
          </View>
          <Text style={styles.tipLink}>Box score ›</Text>
        </Pressable>
      ) : null}

      <View style={styles.plot}>
        {avg ? <View style={[styles.avg, { bottom: scale(avg) + 18 }]} /> : null}
        {shown.map((w) => (
          <Pressable key={w.week} style={styles.col} onPress={played(w) ? () => setPick(w.week) : undefined} hitSlop={4}>
            {played(w) ? (
              <>
                <Text style={[styles.value, w.week === pick && styles.valueOn]}>{Math.round(w.points!)}</Text>
                <View style={[styles.bar, { height: scale(w.points!) }, tone(w.points!), w.status === 'bench' && styles.bench, w.week === pick && styles.picked]} />
              </>
            ) : (
              <Text style={[styles.tag, w.status === 'out' ? styles.tagOut : styles.tagBye]}>{w.status === 'out' ? 'OUT' : 'BYE'}</Text>
            )}
            <Text style={styles.wk}>W{w.week}</Text>
          </Pressable>
        ))}
      </View>

      <Pressable onPress={onOpenLog} style={({ pressed }) => [styles.log, pressed && { opacity: 0.85 }]}>
        <View style={styles.logIcon}>
          <View style={styles.logLine} /><View style={styles.logLine} /><View style={styles.logLine} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={styles.logTitle}>Open full game log</Text>
          <Text style={styles.logSub}>Every game · {pos === 'QB' ? 'passing, rushing' : pos === 'RB' ? 'carries, targets, yards' : 'targets, yards'}, snaps, TDs</Text>
        </View>
        <Text style={styles.logArrow}>›</Text>
      </Pressable>
    </View>
  );
}

// "6/9 REC, 84 YDS, TD" style line for the callout.
function statText(g: GameLogRow, pos: string): string | null {
  const l = g.line;
  if (!l) return null;
  const td = (n: number) => (n > 1 ? `${n} TD` : n === 1 ? 'TD' : null);
  const parts: (string | null)[] = [];
  if (pos === 'QB') {
    parts.push(`${l.comp ?? 0}/${l.att ?? 0}`, `${l.pass_yds ?? 0} YDS`, td(l.pass_td ?? 0), l.ints ? (l.ints > 1 ? `${l.ints} INT` : 'INT') : null);
    if (l.rush_yds >= 15) parts.push(`${l.rush_yds} RUSH`);
  } else if (pos === 'RB') {
    parts.push(`${l.carries} CAR`, `${l.rush_yds} YDS`);
    if (l.targets) parts.push(`${l.rec}/${l.targets} REC`);
    parts.push(td(l.rush_td + l.rec_td));
  } else {
    parts.push(`${l.rec}/${l.targets} REC`, `${l.rec_yds} YDS`, td(l.rec_td + l.rush_td));
  }
  if (l.fum) parts.push(l.fum > 1 ? `${l.fum} FUM` : 'FUM');
  return parts.filter(Boolean).join(', ');
}

const styles = StyleSheet.create({
  card: { backgroundColor: colors.card, borderRadius: 16, padding: 16, gap: 10 },
  head: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' },
  title: { color: colors.text, fontSize: 15, fontWeight: '700' },
  meta: { color: colors.muted, fontSize: 12 },
  tip: { flexDirection: 'row', alignItems: 'center', gap: 10, backgroundColor: colors.row, borderWidth: 1, borderColor: 'rgba(200,155,74,0.45)', borderRadius: 12, paddingHorizontal: 12, paddingVertical: 9 },
  tipTitle: { color: colors.text, fontSize: 13, fontWeight: '700' },
  tipLine: { color: colors.muted, fontSize: 12, marginTop: 1 },
  tipLink: { color: colors.gold, fontSize: 12, fontWeight: '800' },
  plot: { height: PLOT + 18, flexDirection: 'row', gap: 5, alignItems: 'flex-end' },
  avg: { position: 'absolute', left: 0, right: 0, borderTopWidth: 1.5, borderColor: 'rgba(200,190,173,0.45)', borderStyle: 'dashed', zIndex: 2 },
  col: { flex: 1, height: '100%', justifyContent: 'flex-end', alignItems: 'center' },
  value: { color: colors.muted, fontSize: 10, fontWeight: '800', marginBottom: 3 },
  valueOn: { color: colors.text },
  bar: { width: '100%', borderTopLeftRadius: 6, borderTopRightRadius: 6, borderBottomLeftRadius: 2, borderBottomRightRadius: 2 },
  solid: { backgroundColor: SOLID },
  boom: { backgroundColor: BOOM },
  bust: { backgroundColor: 'rgba(232,130,111,0.75)' },
  bench: { opacity: 0.55 },
  picked: { backgroundColor: colors.gold, borderWidth: 2, borderColor: '#F0D08A' },
  tag: { fontSize: 9, fontWeight: '800', borderRadius: 4, overflow: 'hidden', paddingHorizontal: 3, paddingVertical: 2, marginBottom: 4 },
  tagOut: { color: colors.bg, backgroundColor: RED },
  tagBye: { color: colors.muted, borderWidth: 1, borderColor: '#3B6150' },
  wk: { color: '#8E8676', fontSize: 10, fontWeight: '600', marginTop: 5 },
  log: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: 'rgba(200,155,74,0.12)', borderWidth: 1, borderColor: 'rgba(200,155,74,0.5)', borderRadius: 14, paddingHorizontal: 14, paddingVertical: 11 },
  logIcon: { width: 30, height: 30, borderRadius: 8, backgroundColor: colors.gold, justifyContent: 'center', gap: 3, paddingHorizontal: 6 },
  logLine: { height: 3, borderRadius: 2, backgroundColor: colors.bg },
  logTitle: { color: colors.text, fontSize: 15, fontWeight: '700' },
  logSub: { color: colors.muted, fontSize: 12 },
  logArrow: { color: colors.gold, fontSize: 24, fontWeight: '700' },
});
