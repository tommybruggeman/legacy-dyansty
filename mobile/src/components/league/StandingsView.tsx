import { router } from 'expo-router';
import { Fragment } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { ErrorState, Loading } from '../ScreenState';
import { type Standings, type StandingsRow, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

// Built from the phone Standings design: rank badges for the top 3, movement
// arrows and a playoff line after 4th. Tapping an owner opens their team page.
export function StandingsView() {
  const { data, error, loading, reload } = useApi<Standings>('/standings');

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  return (
    <>
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
              <Row row={row} onPress={row.team_id ? () => router.push(`/team/${row.team_id}`) : undefined} />
            </Fragment>
          ))}
        </View>
      )}
    </>
  );
}

function Row({ row, onPress }: { row: StandingsRow; onPress?: () => void }) {
  const move = row.prev_rank ? row.prev_rank - row.rank : 0;

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

function PlayoffLine() {
  return (
    <View style={styles.playoff}>
      <Text style={styles.playoffText}>PLAYOFF LINE</Text>
    </View>
  );
}

const styles = StyleSheet.create({
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
  playoff: { height: 0, zIndex: 1, borderTopWidth: 2, borderStyle: 'dashed', borderColor: colors.gold, alignItems: 'flex-end' },
  playoffText: {
    position: 'absolute', top: -10, right: 10, backgroundColor: colors.bg, color: colors.gold,
    fontSize: 10, fontWeight: '800', letterSpacing: 1, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 10, overflow: 'hidden',
  },
  note: { color: colors.muted, fontSize: 13, textAlign: 'center', marginTop: 14 },
});
