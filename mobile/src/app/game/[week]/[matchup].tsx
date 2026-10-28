import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import { ErrorState, Loading } from '../../../components/ScreenState';
import { Back, Card, Note, Pos, Row, Screen, firstName } from '../../../components/ui';
import { type BoxPlayer, type BoxScore, useApi } from '../../../lib/api';
import { colors } from '../../../lib/theme';

export { ScreenError as ErrorBoundary } from '../../../components/ScreenState';

// Live games refresh every 30 seconds while this screen is open.
const LIVE_REFRESH_MS = 30_000;

// One game, slot by slot: your side on the left when you're playing.
export default function Game() {
  const { week, matchup } = useLocalSearchParams<{ week: string; matchup: string }>();
  const [live, setLive] = useState(true);
  const [shown, setShown] = useState<BoxPlayer | null>(null);
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
          <View style={styles.scoreBox}>
            <Text style={[styles.score, (i === 0 ? a.score >= b.score : b.score > a.score) && styles.gold]}>{side.score.toFixed(2)}</Text>
            {!data.is_final && side.projected != null ? <Text style={styles.proj}>proj {side.projected.toFixed(1)}</Text> : null}
          </View>
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
                <Pressable onPress={openPlayer(pa)} hitSlop={4}>
                  <Text style={styles.player} numberOfLines={1}>{pa?.name ?? ''}</Text>
                </Pressable>
                <Pressable onPress={pa?.player_id && pa.player_id !== '0' ? () => setShown(pa) : undefined} hitSlop={6}>
                  <Text style={[styles.points, styles.gold]}>{pa ? pa.points.toFixed(1) : ''}</Text>
                  {pa && detail(pa) ? <Text style={styles.small} numberOfLines={1}>{detail(pa)}</Text> : null}
                </Pressable>
              </View>
              <View style={styles.mid}><Pos label={pa?.slot || pb?.slot || ''} /></View>
              <View style={styles.side}>
                <Pressable onPress={openPlayer(pb)} hitSlop={4}>
                  <Text style={[styles.player, styles.rightText]} numberOfLines={1}>{pb?.name ?? ''}</Text>
                </Pressable>
                <Pressable onPress={pb?.player_id && pb.player_id !== '0' ? () => setShown(pb) : undefined} hitSlop={6}>
                  <Text style={[styles.points, styles.rightText]}>{pb ? pb.points.toFixed(1) : ''}</Text>
                  {pb && detail(pb) ? <Text style={[styles.small, styles.rightText]} numberOfLines={1}>{detail(pb)}</Text> : null}
                </Pressable>
              </View>
            </Row>
          );
        })}
      </Card>
      <Note>Tap a score to see where the points came from, a player's name for his page, or a team name at the top for that team.</Note>
      <Breakdown player={shown} onClose={() => setShown(null)} />
    </Screen>
  );
}

// A small card over the matchup: each scoring stat and what it was worth.
function Breakdown({ player, onClose }: { player: BoxPlayer | null; onClose: () => void }) {
  const rows = player?.breakdown ?? [];
  return (
    <Modal visible={!!player} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        {player ? (
          <Pressable style={styles.sheet} onPress={() => undefined}>
            <Text style={styles.sheetEyebrow}>{[player.pos, player.team].filter(Boolean).join(' · ')}</Text>
            <View style={styles.sheetHead}>
              <Text style={styles.sheetName} numberOfLines={1}>{player.name}</Text>
              <Text style={styles.sheetTotal}>{player.points.toFixed(1)}</Text>
            </View>
            {rows.length ? (
              rows.map((r) => (
                <View key={r.label} style={styles.bRow}>
                  <Text style={styles.bValue}>{r.value}</Text>
                  <Text style={styles.bLabel}>{r.label}</Text>
                  <Text style={[styles.bPts, r.points < 0 && styles.bNeg]}>{r.points > 0 ? '+' : ''}{r.points.toFixed(1)}</Text>
                </View>
              ))
            ) : (
              <Text style={styles.bEmpty}>
                {player.status === 'waiting' || player.status === 'bye' ? "His game hasn't started yet." : 'No stats yet for this week.'}
              </Text>
            )}
            {player.proj != null ? <Text style={styles.bEmpty}>Projected {player.proj.toFixed(1)} before kickoff{player.rank ? ` · ${player.rank} this week` : ''}</Text> : null}
            <View style={styles.sheetActions}>
              <Pressable onPress={() => { onClose(); router.push(`/player/${player.player_id}`); }} hitSlop={6}>
                <Text style={styles.sheetLink}>Player page ›</Text>
              </Pressable>
              <Pressable onPress={onClose} hitSlop={6}>
                <Text style={styles.sheetClose}>Close</Text>
              </Pressable>
            </View>
          </Pressable>
        ) : null}
      </Pressable>
    </Modal>
  );
}

// The small line under each score: "proj 14.1" before kickoff, "proj 14.1 · LIVE"
// mid-game, and his position rank that week ("RB4") once it's over.
function detail(p: BoxPlayer) {
  if (!p.player_id || p.player_id === '0') return '';
  const proj = p.proj != null ? `proj ${p.proj.toFixed(1)}` : null;
  if (p.status === 'bye') return 'BYE';
  if (p.status === 'playing') return [proj, 'LIVE'].filter(Boolean).join(' · ');
  if (p.status === 'final') return [p.rank, proj].filter(Boolean).join(' · ');
  return proj ?? '';
}

const openPlayer = (p?: BoxPlayer) => (p?.player_id && p.player_id !== '0' ? () => router.push(`/player/${p.player_id}`) : undefined);

const styles = StyleSheet.create({
  scoreBox: { alignItems: 'flex-end' },
  proj: { color: colors.muted, fontSize: 12, marginTop: -2 },
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
  small: { color: colors.muted, fontSize: 10, marginTop: 1, opacity: 0.8 },
  backdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.55)', justifyContent: 'center', padding: 28 },
  sheet: { backgroundColor: colors.card, borderRadius: 18, padding: 18, gap: 4 },
  sheetEyebrow: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1 },
  sheetHead: { flexDirection: 'row', alignItems: 'baseline', gap: 10, marginBottom: 8 },
  sheetName: { color: colors.text, fontSize: 20, fontWeight: '800', flex: 1 },
  sheetTotal: { color: colors.gold, fontSize: 22, fontWeight: '800' },
  bRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 6, borderTopWidth: 1, borderColor: '#1F3F31' },
  bValue: { color: colors.text, fontSize: 14, fontWeight: '700', width: 52 },
  bLabel: { color: colors.muted, fontSize: 14, flex: 1 },
  bPts: { color: colors.text, fontSize: 14, fontWeight: '700' },
  bNeg: { color: '#E8826F' },
  bEmpty: { color: colors.muted, fontSize: 13, marginTop: 4 },
  sheetActions: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 12 },
  sheetLink: { color: colors.gold, fontSize: 14, fontWeight: '700' },
  sheetClose: { color: colors.muted, fontSize: 14, fontWeight: '700' },
});
