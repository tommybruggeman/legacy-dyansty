import { Fragment, useState } from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ErrorState, Loading } from '../../components/ScreenState';
import { type Standings, type StandingsRow, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

// Built from the phone Standings design: rank badges for the top 3, movement
// arrows, a playoff line after 4th, and a tap-to-expand stats row.
export default function League() {
  const insets = useSafeAreaInsets();
  const { data, error, loading, reload } = useApi<Standings>('/standings');
  const [open, setOpen] = useState<number | null>(null);
  const openId = open ?? data?.rows.find((r) => r.is_me)?.roster_id ?? null;

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={[styles.content, { paddingTop: insets.top + 12 }]}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={reload} tintColor={colors.gold} />}
    >
      <Text style={styles.title}>Standings</Text>
      {data.rows.length === 0 ? (
        <Text style={styles.note}>Standings start after week 1 is final.</Text>
      ) : (
        <View style={styles.table}>
          <View style={[styles.row, styles.header]}>
            <Text style={[styles.rankCol, styles.headText]}>#</Text>
            <Text style={[styles.nameCol, styles.headText]}>OWNER</Text>
            <Text style={[styles.num, styles.headText, styles.gold]}>SP</Text>
            <Text style={[styles.numWide, styles.headText]}>PF</Text>
            <Text style={[styles.num, styles.headText]}>W</Text>
            <Text style={[styles.num, styles.headText]}>T5</Text>
          </View>
          {data.rows.map((row) => (
            <Fragment key={row.roster_id}>
              {row.rank === data.playoff_teams + 1 ? <PlayoffLine /> : null}
              <Row row={row} expanded={row.roster_id === openId} onPress={() => setOpen(row.roster_id === openId ? -1 : row.roster_id)} />
            </Fragment>
          ))}
        </View>
      )}
      <Text style={styles.note}>Tap a row for more stats · through week {data.weeks_played}</Text>
    </ScrollView>
  );
}

function Row({ row, expanded, onPress }: { row: StandingsRow; expanded: boolean; onPress: () => void }) {
  const move = row.prev_rank ? row.prev_rank - row.rank : 0;
  const record = `${row.wins}-${row.losses}${row.ties ? `-${row.ties}` : ''}`;

  return (
    <Pressable onPress={onPress} style={[styles.rowWrap, row.is_me && styles.meRow]}>
      <View style={styles.row}>
        <View style={styles.rankCol}>
          <RankBadge rank={row.rank} />
        </View>
        <View style={[styles.nameCol, styles.nameLine]}>
          <Text style={[styles.name, row.rank === 1 && styles.gold]} numberOfLines={1}>{row.name}</Text>
          {move !== 0 ? (
            <Text style={[styles.move, { color: move > 0 ? colors.live : colors.danger }]}>
              {move > 0 ? '▲' : '▼'}{Math.abs(move)}
            </Text>
          ) : null}
        </View>
        <Text style={[styles.num, styles.cell, styles.gold, styles.bold]}>{row.standing_points}</Text>
        <Text style={[styles.numWide, styles.cell]}>{Math.round(row.pf)}</Text>
        <Text style={[styles.num, styles.cell]}>{row.wins}</Text>
        <Text style={[styles.num, styles.cell]}>{row.top5}</Text>
      </View>
      {expanded ? (
        <View style={styles.stats}>
          <Stat label="RECORD" value={record} />
          <Stat label="PF / GAME" value={row.pf_per_game.toFixed(1)} />
          <Stat label="PA" value={String(Math.round(row.pa))} />
          <Stat label="PA / GAME" value={row.pa_per_game.toFixed(1)} />
        </View>
      ) : null}
    </Pressable>
  );
}

function RankBadge({ rank }: { rank: number }) {
  const medal = [colors.gold, colors.silver, colors.bronze][rank - 1];
  if (!medal) return <Text style={styles.plainRank}>{rank}</Text>;
  return (
    <View style={[styles.badge, { backgroundColor: medal }]}>
      <Text style={styles.badgeText}>{rank}</Text>
    </View>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={styles.statValue}>{value}</Text>
    </View>
  );
}

function PlayoffLine() {
  return (
    <View style={styles.playoff}>
      <Text style={styles.playoffText}>PLAYOFF LINE</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 16, paddingBottom: 32 },
  title: { color: colors.text, fontSize: 26, fontWeight: '800', marginBottom: 14 },
  table: { borderWidth: 1, borderColor: colors.line, borderRadius: 18, overflow: 'hidden', backgroundColor: colors.row },
  header: { backgroundColor: colors.card, paddingVertical: 12 },
  headText: { color: colors.muted, fontSize: 12, fontWeight: '700', letterSpacing: 1 },
  rowWrap: { borderTopWidth: 1, borderTopColor: colors.card },
  meRow: { backgroundColor: '#1C2A1C', borderLeftWidth: 4, borderLeftColor: colors.gold },
  row: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 8, paddingVertical: 14 },
  rankCol: { width: 32, alignItems: 'center' },
  nameCol: { flex: 1, paddingLeft: 8 },
  nameLine: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  name: { color: colors.text, fontSize: 15, fontWeight: '700', flexShrink: 1 },
  move: { fontSize: 11, fontWeight: '700' },
  num: { width: 28, textAlign: 'center' },
  numWide: { width: 40, textAlign: 'center' },
  cell: { color: colors.text, fontSize: 15 },
  gold: { color: colors.gold },
  bold: { fontWeight: '800' },
  plainRank: { color: colors.muted, fontSize: 15, fontWeight: '700' },
  badge: { width: 26, height: 26, borderRadius: 13, alignItems: 'center', justifyContent: 'center' },
  badgeText: { color: colors.bg, fontWeight: '800', fontSize: 14 },
  stats: { flexDirection: 'row', gap: 6, paddingLeft: 44, paddingRight: 8, paddingBottom: 14 },
  stat: { flex: 1, backgroundColor: colors.card, borderRadius: 12, paddingVertical: 10, alignItems: 'center' },
  statLabel: { color: colors.muted, fontSize: 9, fontWeight: '700', letterSpacing: 0.8 },
  statValue: { color: colors.text, fontSize: 16, fontWeight: '800', marginTop: 4 },
  playoff: { height: 0, zIndex: 1, borderTopWidth: 2, borderStyle: 'dashed', borderColor: colors.gold, alignItems: 'flex-end' },
  playoffText: {
    position: 'absolute', top: -10, right: 10, backgroundColor: colors.bg, color: colors.gold,
    fontSize: 10, fontWeight: '800', letterSpacing: 1, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 10, overflow: 'hidden',
  },
  note: { color: colors.muted, fontSize: 13, textAlign: 'center', marginTop: 14 },
});
