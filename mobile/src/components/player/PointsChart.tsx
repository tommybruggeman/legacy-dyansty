import { Pressable, StyleSheet, Text, View } from 'react-native';

import type { GameLogRow } from '../../lib/api';
import { colors } from '../../lib/theme';

const PLOT = 110;

// Points by week: solid bars for starts, outlined bars for bench weeks,
// OUT and BYE tags, and a dashed line at his average. Numbers sit above
// the bars inside the plot, so they never run into the card title.
export function PointsChart({ weeks, avg, onPress }: { weeks: GameLogRow[]; avg: number | null; onPress?: () => void }) {
  const shown = [...weeks].sort((a, b) => a.week - b.week).slice(-8);
  const best = Math.max(1, ...shown.map((w) => w.points ?? 0));
  const scale = (n: number) => Math.max(3, Math.round((n / best) * (PLOT - 18)));
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.card, pressed && { opacity: 0.85 }]}>
      <View style={styles.head}>
        <Text style={styles.title}>Points by week</Text>
        <Text style={styles.link}>Game log ›</Text>
      </View>
      <View style={styles.plot}>
        {avg ? <View style={[styles.avg, { bottom: scale(avg) }]} /> : null}
        <View style={styles.base} />
        <View style={styles.cols}>
          {shown.map((w) => {
            const played = w.points != null && (w.status === 'started' || w.status === 'bench' || w.status === 'free_agent');
            return (
              <View key={w.week} style={styles.col}>
                {played ? (
                  <>
                    <Text style={[styles.value, w.status === 'started' && styles.valueOn]}>{w.points!.toFixed(1)}</Text>
                    <View style={[styles.bar, { height: scale(w.points!) }, w.status === 'started' ? styles.barOn : styles.barOff]} />
                  </>
                ) : (
                  <Text style={[styles.tag, w.status === 'out' ? styles.tagOut : styles.tagBye]}>{w.status === 'out' ? 'OUT' : 'BYE'}</Text>
                )}
              </View>
            );
          })}
        </View>
      </View>
      <View style={styles.cols}>
        {shown.map((w) => (
          <View key={w.week} style={styles.col}>
            <Text style={styles.wk}>W{w.week}</Text>
            <Text style={styles.opp} numberOfLines={1}>{w.opp.replace('vs ', '').replace('at ', '@') || '—'}</Text>
          </View>
        ))}
      </View>
      <View style={styles.legend}>
        <View style={styles.key}><View style={[styles.swatch, styles.barOn]} /><Text style={styles.keyText}>Started</Text></View>
        <View style={styles.key}><View style={[styles.swatch, styles.barOff]} /><Text style={styles.keyText}>Bench</Text></View>
        {avg ? <View style={styles.key}><View style={styles.dash} /><Text style={styles.keyText}>Avg {avg.toFixed(1)}</Text></View> : null}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: colors.card, borderRadius: 16, padding: 16, gap: 10 },
  head: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' },
  title: { color: colors.text, fontSize: 15, fontWeight: '700' },
  link: { color: colors.gold, fontSize: 12, fontWeight: '700' },
  plot: { height: PLOT, marginTop: 4 },
  avg: { position: 'absolute', left: 0, right: 0, borderTopWidth: 1.5, borderColor: '#6F8F7E', borderStyle: 'dashed' },
  base: { position: 'absolute', left: 0, right: 0, bottom: 0, borderTopWidth: 1, borderColor: '#2C5140' },
  cols: { flexDirection: 'row', gap: 8 },
  col: { flex: 1, height: '100%', justifyContent: 'flex-end', alignItems: 'center' },
  value: { color: colors.muted, fontSize: 10, fontWeight: '700', marginBottom: 3, backgroundColor: colors.card, paddingHorizontal: 2, zIndex: 1 },
  valueOn: { color: colors.text },
  bar: { width: '100%', borderRadius: 5, borderBottomLeftRadius: 2, borderBottomRightRadius: 2, borderWidth: 1.5, borderColor: colors.gold },
  barOn: { backgroundColor: colors.gold },
  barOff: { backgroundColor: 'rgba(200,155,74,0.12)' },
  tag: { fontSize: 9, fontWeight: '800', borderRadius: 4, overflow: 'hidden', paddingHorizontal: 3, paddingVertical: 2, marginBottom: 4 },
  tagOut: { color: colors.bg, backgroundColor: '#E8826F' },
  tagBye: { color: colors.muted, borderWidth: 1, borderColor: '#3B6150' },
  wk: { color: colors.muted, fontSize: 10, textAlign: 'center' },
  opp: { color: colors.muted, fontSize: 10, textAlign: 'center' },
  legend: { flexDirection: 'row', gap: 14, alignItems: 'center', flexWrap: 'wrap' },
  key: { flexDirection: 'row', gap: 5, alignItems: 'center' },
  swatch: { width: 10, height: 10, borderRadius: 2, borderWidth: 1.5, borderColor: colors.gold },
  dash: { width: 14, borderTopWidth: 1.5, borderColor: '#6F8F7E', borderStyle: 'dashed' },
  keyText: { color: colors.muted, fontSize: 11 },
});
