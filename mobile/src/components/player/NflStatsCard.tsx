import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import type { PlayerPage } from '../../lib/api';
import { colors } from '../../lib/theme';

// "In the NFL" card: four stats per game or for the season, each with his rank among NFL players at his position.
type Ranked = NonNullable<PlayerPage['nfl_ranked']>;
type Season = NonNullable<PlayerPage['nfl_season']>;

const RED = '#E8826F';
const AMBER = '#E2B655';

function ordinal(n: number): string {
  const tail = n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th';
  return `${n}${tail}`;
}

function rankColor(rank: number, pool: number): string {
  const share = rank / pool;
  return share <= 1 / 3 ? colors.live : share <= 2 / 3 ? AMBER : RED;
}

function fmt(v: number, perGame: boolean): string {
  return perGame ? v.toFixed(1) : Math.round(v).toLocaleString('en-US');
}

export function NflStatsCard({ ranked, season, pos }: { ranked: Ranked; season: Season | null; pos: string }) {
  const [perGame, setPerGame] = useState(true);
  const pool = ranked.pool;
  return (
    <View style={styles.card}>
      <View style={styles.head}>
        <Text style={styles.label}>IN THE NFL · {ranked.games} GAME{ranked.games === 1 ? '' : 'S'}</Text>
        <View style={styles.seg}>
          {[true, false].map((pg) => (
            <Pressable key={String(pg)} onPress={() => setPerGame(pg)} hitSlop={6} style={[styles.segItem, perGame === pg && styles.segOn]}>
              <Text style={[styles.segText, perGame === pg && styles.segTextOn]}>{pg ? 'Per game' : 'Season'}</Text>
            </Pressable>
          ))}
        </View>
      </View>
      <View style={styles.row}>
        {ranked.stats.map((s) => {
          const rank = perGame ? s.rank_per_game : s.rank_total;
          return (
            <View key={s.key} style={styles.stat}>
              <Text style={styles.value}>{fmt(perGame ? s.per_game : s.total, perGame)}</Text>
              <Text style={styles.statLabel}>{s.label}</Text>
              {rank && pool ? <Text style={[styles.rank, { color: rankColor(rank, pool) }]}>{ordinal(rank)}</Text> : null}
            </View>
          );
        })}
      </View>
      {season?.snap_pct != null ? (
        <View style={styles.snapRow}>
          <Text style={styles.statLabel}>Snap share</Text>
          <View style={styles.snapTrack}><View style={[styles.snapFill, { width: `${Math.min(100, season.snap_pct)}%` }]} /></View>
          <Text style={styles.snapText}>{season.snap_pct}%{season.snap_trend === 'up' ? ' ▲' : season.snap_trend === 'down' ? ' ▼' : ''}</Text>
        </View>
      ) : null}
      {pool ? <Text style={styles.foot}>Ranks among {pool} {pos}s with {ranked.min_games}+ games.</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: colors.card, borderRadius: 16, padding: 16, gap: 12 },
  head: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  label: { color: colors.muted, fontSize: 10, fontWeight: '700', letterSpacing: 1 },
  seg: { flexDirection: 'row', backgroundColor: colors.row, borderRadius: 9, padding: 2, gap: 2 },
  segItem: { paddingHorizontal: 9, paddingVertical: 4, borderRadius: 7 },
  segOn: { backgroundColor: '#23392F' },
  segText: { color: '#8E8676', fontSize: 11, fontWeight: '700' },
  segTextOn: { color: colors.text },
  row: { flexDirection: 'row', gap: 6 },
  stat: { flex: 1 },
  value: { color: colors.text, fontSize: 19, fontWeight: '800' },
  statLabel: { color: colors.muted, fontSize: 11 },
  rank: { fontSize: 11, fontWeight: '700', marginTop: 2 },
  snapRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  snapTrack: { flex: 1, height: 8, borderRadius: 4, backgroundColor: '#1F3F31', overflow: 'hidden' },
  snapFill: { height: 8, backgroundColor: colors.live },
  snapText: { color: colors.text, fontSize: 12, fontWeight: '700' },
  foot: { color: '#8E8676', fontSize: 11 },
});
