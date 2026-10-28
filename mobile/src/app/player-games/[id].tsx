import { useLocalSearchParams } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';

import { Headshot } from '../../components/Pictures';
import { STATUS, lineText, pts } from '../../components/player/format';
import { ErrorState, Loading } from '../../components/ScreenState';
import { Back, Screen } from '../../components/ui';
import { type PlayerPage, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

export { ScreenError as ErrorBoundary } from '../../components/ScreenState';

// The full game log, opened from the player page's points chart: every
// week's NFL line and snap share next to his points in this league.
// It reads the same /players response, so it opens instantly from cache.
export default function PlayerGames() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, error, loading, reload } = useApi<PlayerPage>(`/players/${id}`);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const log = data.game_log ?? [];
  const played = log.filter((g) => g.points != null);
  const total = played.reduce((s, g) => s + (g.points ?? 0), 0);
  const tiles = data.nfl_season?.tiles ?? [];

  return (
    <Screen loading={loading} onRefresh={reload}>
      <Back label={data.name} />
      <View style={styles.stack}>
        <View style={styles.hero}>
          <Headshot playerId={data.player_id} pos={data.pos} team={data.nfl_team} size={56} />
          <View style={{ flex: 1 }}>
            <Text style={styles.eyebrow}>{data.pos} · {data.nfl_team} · {data.season}</Text>
            <Text style={styles.title}>Game log</Text>
          </View>
        </View>

        <View style={styles.table}>
          <View style={[styles.row, styles.headRow]}>
            <Text style={[styles.th, styles.colWeek]}>WEEK</Text>
            <Text style={[styles.th, styles.colLine]}>NFL LINE</Text>
            <Text style={[styles.th, styles.colPts, { color: colors.gold }]}>LEGACY</Text>
          </View>
          {log.map((g) => {
            const [main, sub] = lineText(g, data.pos);
            const snap = g.line?.snap_pct;
            const on = g.status === 'started';
            return (
              <View key={g.week} style={styles.row}>
                <View style={styles.colWeek}>
                  <Text style={styles.week}>W{g.week}</Text>
                  <Text style={styles.opp}>{g.status === 'bye' ? 'Bye' : g.opp}</Text>
                </View>
                <View style={styles.colLine}>
                  <Text style={styles.main}>{main}</Text>
                  {sub ? <Text style={styles.sub}>{sub}</Text> : null}
                  {snap != null ? (
                    <View style={styles.snap}>
                      <View style={styles.snapTrack}><View style={[styles.snapFill, { width: `${Math.min(100, snap)}%` }]} /></View>
                      <Text style={styles.snapText}>{snap}% snaps</Text>
                    </View>
                  ) : null}
                </View>
                <View style={styles.colPts}>
                  <Text style={[styles.points, !on && styles.pointsOff]}>{pts(g.points)}</Text>
                  {g.status !== 'bye' ? (
                    <Text style={[styles.status, on ? styles.statusOn : g.status === 'out' ? styles.statusOut : null]}>{STATUS[g.status]}</Text>
                  ) : null}
                  {g.rank ? <Text style={styles.rank}>{g.rank} that week</Text> : null}
                </View>
              </View>
            );
          })}
          {played.length ? (
            <View style={[styles.row, styles.headRow]}>
              <Text style={[styles.week, styles.colWeek]}>TOTAL</Text>
              <Text style={[styles.main, styles.colLine, { fontWeight: '700' }]}>{tiles.map((t) => `${t.value} ${t.label}`).join(' · ')}</Text>
              <Text style={[styles.points, styles.colPts, { color: colors.gold }]}>{total.toFixed(1)}</Text>
            </View>
          ) : null}
        </View>
        <Text style={styles.note}>Legacy points use our league's scoring. Started and bench come from the Sleeper lineup that week. The rank compares him with every NFL player at his position that week, scored our way.</Text>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  stack: { gap: 14 },
  hero: { flexDirection: 'row', gap: 12, alignItems: 'center' },
  eyebrow: { color: colors.gold, fontSize: 11, fontWeight: '700', letterSpacing: 1 },
  title: { color: colors.text, fontSize: 24, fontWeight: '800' },
  table: { backgroundColor: colors.card, borderRadius: 16, overflow: 'hidden' },
  row: { flexDirection: 'row', gap: 8, paddingVertical: 11, paddingHorizontal: 14, borderTopWidth: 1, borderColor: '#1F3F31', alignItems: 'center' },
  headRow: { backgroundColor: '#0D2219', borderTopWidth: 0, paddingVertical: 10 },
  th: { color: colors.muted, fontSize: 10, fontWeight: '800', letterSpacing: 1 },
  colWeek: { width: 52 },
  colLine: { flex: 1 },
  colPts: { width: 78, alignItems: 'flex-end', textAlign: 'right' },
  week: { color: colors.text, fontSize: 14, fontWeight: '700' },
  opp: { color: colors.muted, fontSize: 11 },
  main: { color: colors.text, fontSize: 13 },
  sub: { color: colors.muted, fontSize: 12 },
  snap: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 4 },
  snapTrack: { width: 70, height: 5, borderRadius: 3, backgroundColor: '#1F3F31', overflow: 'hidden' },
  snapFill: { height: 5, backgroundColor: colors.live },
  snapText: { color: colors.muted, fontSize: 10 },
  points: { color: colors.text, fontSize: 17, fontWeight: '800' },
  pointsOff: { color: colors.muted },
  status: { color: colors.muted, fontSize: 10, fontWeight: '700' },
  statusOn: { color: colors.gold },
  statusOut: { color: '#E8826F' },
  rank: { color: colors.muted, fontSize: 10, marginTop: 1 },
  note: { color: colors.muted, fontSize: 12, lineHeight: 18 },
});
