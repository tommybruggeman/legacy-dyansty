import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { ErrorState, Loading } from '../../../components/ScreenState';
import { Back, Card, Note, Pos, Row, Screen, firstName } from '../../../components/ui';
import { type BoxScore, useApi } from '../../../lib/api';
import { colors } from '../../../lib/theme';

// Live games refresh every 30 seconds while this screen is open.
const LIVE_REFRESH_MS = 30_000;

// One game, slot by slot: your side on the left when you're playing.
export default function Game() {
  const { week, matchup } = useLocalSearchParams<{ week: string; matchup: string }>();
  const [live, setLive] = useState(true);
  const { data, error, loading, reload } = useApi<BoxScore>(`/matchups/${week}/${matchup}`, live ? LIVE_REFRESH_MS : 0);
  useEffect(() => { if (data) setLive(!data.is_final); }, [data]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const [a, b] = data.teams[1]?.roster_id === data.my_roster_id ? [data.teams[1], data.teams[0]] : data.teams;
  const rows = Math.max(a.players.length, b.players.length);
  const open = (teamId: string | null) => (teamId ? () => router.push(`/team/${teamId}`) : undefined);

  return (
    <Screen loading={loading} onRefresh={reload}>
      <Back label="Matchups" />
      <Text style={styles.eyebrow}>WEEK {data.week}</Text>
      {[a, b].map((side, i) => (
        <Pressable key={side.roster_id} onPress={open(side.team_id)} style={styles.scoreLine}>
          <Text style={[styles.team, i === 0 && a.score >= b.score && styles.gold]} numberOfLines={1}>{side.name}</Text>
          <Text style={[styles.score, (i === 0 ? a.score >= b.score : b.score > a.score) && styles.gold]}>{side.score.toFixed(2)}</Text>
        </Pressable>
      ))}
      <Card style={styles.card}>
        <Row first>
          <Text style={[styles.head, styles.side]}>{firstName(a.name).toUpperCase()}</Text>
          <Text style={[styles.head, styles.mid]}>POS</Text>
          <Text style={[styles.head, styles.side, styles.rightText]}>{firstName(b.name).toUpperCase()}</Text>
        </Row>
        {Array.from({ length: rows }).map((_, i) => {
          const pa = a.players[i];
          const pb = b.players[i];
          return (
            <Row key={i}>
              <View style={styles.side}>
                <Text style={styles.player} numberOfLines={1}>{pa?.name ?? ''}</Text>
                <Text style={[styles.points, styles.gold]}>{pa ? pa.points.toFixed(1) : ''}</Text>
              </View>
              <View style={styles.mid}><Pos label={pa?.slot || pb?.slot || ''} /></View>
              <View style={styles.side}>
                <Text style={[styles.player, styles.rightText]} numberOfLines={1}>{pb?.name ?? ''}</Text>
                <Text style={[styles.points, styles.rightText]}>{pb ? pb.points.toFixed(1) : ''}</Text>
              </View>
            </Row>
          );
        })}
      </Card>
      <Note>Tap a name at the top to open that team.</Note>
    </Screen>
  );
}

const styles = StyleSheet.create({
  eyebrow: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1.2, marginBottom: 4 },
  scoreLine: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 4 },
  team: { color: colors.text, fontSize: 20, fontWeight: '800', flexShrink: 1 },
  score: { color: colors.text, fontSize: 26, fontWeight: '800' },
  gold: { color: colors.gold },
  card: { marginTop: 10, paddingVertical: 4 },
  head: { color: colors.muted, fontSize: 11, fontWeight: '700', letterSpacing: 1 },
  side: { flex: 1 },
  mid: { width: 44, alignItems: 'center', textAlign: 'center' },
  rightText: { textAlign: 'right' },
  player: { color: colors.text, fontSize: 14, fontWeight: '700' },
  points: { color: colors.muted, fontSize: 12, marginTop: 1 },
});
