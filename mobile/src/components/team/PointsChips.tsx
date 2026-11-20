import { useRef, useState } from 'react';
import { Animated, Dimensions, Easing, Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import type { TeamPoints } from '../../lib/api';
import { colors } from '../../lib/theme';
import { ordinal } from '../ui';

type Kind = 'pf' | 'pa';
const LABEL: Record<Kind, string> = { pf: 'PF', pa: 'PA' };
const SIDE = 16;

// The boxed "PF 4th" / "PA 2nd" chip at the end of the record and cap lines on a team page (Tommy, 2026-11-20).
// Rank 1 = the most points in the league. Tapping it grows a quick view of the team's season points out of the chip.
export function PointsChip({ kind, points }: { kind: Kind; points: TeamPoints }) {
  const ref = useRef<any>(null); // RN View instance (measureInWindow)
  const [at, setAt] = useState<{ top: number; originX: number } | null>(null);
  const grow = useRef(new Animated.Value(0)).current;
  const open = () => ref.current?.measureInWindow((x: number, y: number, w: number, h: number) => {
    setAt({ top: y + h + 6, originX: x + w / 2 - SIDE });
    grow.setValue(0);
    Animated.timing(grow, { toValue: 1, duration: 220, easing: Easing.out(Easing.cubic), useNativeDriver: true }).start();
  });
  const close = () => Animated.timing(grow, { toValue: 0, duration: 150, easing: Easing.in(Easing.cubic), useNativeDriver: true })
    .start(() => setAt(null));
  return (
    <>
      <Pressable ref={ref} onPress={open} hitSlop={6} style={({ pressed }) => [styles.chip, pressed && styles.pressed]}>
        <Text style={styles.chipKey}>{LABEL[kind]} <Text style={styles.chipRank}>{ordinal(points.ranks[kind])}</Text></Text>
      </Pressable>
      <Modal visible={!!at} transparent animationType="none" onRequestClose={close}>
        <Pressable style={StyleSheet.absoluteFill} onPress={close}>
          <Animated.View style={[StyleSheet.absoluteFill, styles.dim, { opacity: grow }]} />
        </Pressable>
        {at ? (
          <Animated.View
            style={[
              styles.card,
              { top: Math.min(at.top, Dimensions.get('window').height - 420) },
              {
                opacity: grow,
                transformOrigin: `${at.originX}px 0px`,
                transform: [{ scale: grow.interpolate({ inputRange: [0, 1], outputRange: [0.15, 1] }) }],
              },
            ]}
          >
            <PointsCard points={points} tapped={kind} />
          </Animated.View>
        ) : null}
      </Modal>
    </>
  );
}

function PointsCard({ points: p, tapped }: { points: TeamPoints; tapped: Kind }) {
  const r = p.ranks;
  const tiles: { value: string; label: string; rank?: number; on?: boolean }[] = [
    { value: fmt(p.pf), label: 'POINTS FOR', rank: r.pf, on: tapped === 'pf' },
    { value: fmt(p.pa), label: 'POINTS AGAINST', rank: r.pa, on: tapped === 'pa' },
    { value: one(p.pf_per_game), label: 'PER WEEK', rank: r.pf_per_game },
    { value: one(p.pa_per_game), label: 'AGAINST PER WEEK', rank: r.pa_per_game },
    { value: String(p.top5 ?? 0), label: 'TOP-5 WEEKS', rank: r.top5 },
    { value: `${p.vs_avg_wins}-${p.vs_avg_losses}`, label: 'RECORD VS LEAGUE AVG', rank: r.vs_avg_wins },
    { value: p.best_week ? one(p.best_week.score) : '—', label: p.best_week ? `BEST WEEK · WK ${p.best_week.week}` : 'BEST WEEK' },
    { value: p.worst_week ? one(p.worst_week.score) : '—', label: p.worst_week ? `WORST WEEK · WK ${p.worst_week.week}` : 'WORST WEEK' },
  ];
  return (
    <>
      <Text style={styles.eyebrow}>SEASON · {p.games} {p.games === 1 ? 'WEEK' : 'WEEKS'}</Text>
      <View style={styles.grid}>
        {tiles.map((t) => (
          <View key={t.label} style={[styles.tile, t.on && styles.tileOn]}>
            <Text style={styles.value} numberOfLines={1} adjustsFontSizeToFit>
              {t.value}{t.rank ? <Text style={styles.rank}>  {ordinal(t.rank)}</Text> : null}
            </Text>
            <Text style={styles.label} numberOfLines={2}>{t.label}</Text>
          </View>
        ))}
      </View>
      <Text style={styles.foot}>Rank: 1st = most in the league</Text>
    </>
  );
}

const one = (n: number | null | undefined) => (n == null ? '—' : n.toFixed(1));
const fmt = (n: number | null | undefined) => (n == null ? '—' : n.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 }));

const styles = StyleSheet.create({
  chip: { borderWidth: 1, borderColor: '#2F4738', borderRadius: 6, paddingVertical: 0, paddingHorizontal: 5, marginVertical: 1 },
  pressed: { opacity: 0.6 },
  chipKey: { color: colors.muted, fontSize: 11, lineHeight: 15 },
  chipRank: { color: colors.gold, fontWeight: '700' },
  dim: { backgroundColor: 'rgba(0,0,0,0.55)' },
  card: {
    position: 'absolute', left: SIDE, right: SIDE, backgroundColor: '#172C22', borderRadius: 18,
    borderWidth: 1.5, borderColor: colors.gold, padding: 14,
  },
  eyebrow: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 2 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 10 },
  tile: { width: '48.5%', flexGrow: 1, flexBasis: '45%', borderWidth: 1, borderColor: '#2A4033', borderRadius: 12, paddingVertical: 9, paddingHorizontal: 11 },
  tileOn: { borderColor: colors.gold },
  value: { color: colors.text, fontSize: 19, fontWeight: '800' },
  rank: { color: colors.gold, fontSize: 12, fontWeight: '700' },
  label: { color: colors.muted, fontSize: 9.5, fontWeight: '700', letterSpacing: 1.2, marginTop: 3 },
  foot: { color: colors.muted, fontSize: 11.5, textAlign: 'center', marginTop: 10 },
});
