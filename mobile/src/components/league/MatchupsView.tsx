import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { ErrorState, Loading } from '../ScreenState';
import { type Game, type Matchups as MatchupsData, type MatchupTeam, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

const first = (name: string) => name.split(' ')[0];

// Live games refresh every 30 seconds while this screen is open.
const LIVE_REFRESH_MS = 30_000;


// Built from the phone Weekly Matchups design: week picker, three summary
// tiles, your game pinned first, gold rank = a Top 5 score this week.
// Tapping a game opens its box score.
export function MatchupsView() {
  const [week, setWeek] = useState<number | null>(null);
  const [live, setLive] = useState(true);
  const { data, error, loading, reload } = useApi<MatchupsData>(week ? `/matchups?week=${week}` : '/matchups', live ? LIVE_REFRESH_MS : 0);
  useEffect(() => { if (data) setLive(!data.is_final); }, [data]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const mine = data.games.find((g) => g.teams.some((t) => t.roster_id === data.my_roster_id));
  const others = data.games.filter((g) => g !== mine);
  const shown = data.week;

  return (
    <View style={styles.content}>
      <View style={styles.titleRow}>
        <View style={styles.weekPicker}>
          <Pressable disabled={shown <= 1} onPress={() => setWeek(shown - 1)} hitSlop={10}>
            <Text style={[styles.arrow, shown <= 1 && styles.dim]}>‹</Text>
          </Pressable>
          <Text style={styles.weekText}>Week {shown}</Text>
          <Pressable disabled={shown >= 18} onPress={() => setWeek(shown + 1)} hitSlop={10}>
            <Text style={[styles.arrow, shown >= 18 && styles.dim]}>›</Text>
          </Pressable>
        </View>
      </View>

      {data.summary ? (
        <View style={styles.tiles}>
          <Tile label="HIGH SCORE" value={data.summary.high.score.toFixed(2)} sub={first(data.summary.high.name)} />
          <Tile label="TOP 5 LINE" value={data.summary.cut.score.toFixed(2)} sub={first(data.summary.cut.name)} />
          <Tile
            label="CLOSEST"
            value={data.summary.closest_margin.toFixed(2)}
            sub={`${first(data.summary.closest.teams[0].name)} v ${first(data.summary.closest.teams[1].name)}`}
          />
        </View>
      ) : (
        <Text style={styles.note}>No scores yet this week.</Text>
      )}

      {mine ? <GameCard game={mine} week={shown} myId={data.my_roster_id} final={data.is_final} isMine /> : null}
      {others.map((game) => (
        <GameCard key={game.matchup_id} game={game} week={shown} myId={data.my_roster_id} final={data.is_final} />
      ))}
      <Text style={styles.note}>Gold rank = a Top 5 score this week · tap a game for the box score</Text>
    </View>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <View style={styles.tile}>
      <Text style={styles.tileLabel}>{label}</Text>
      <Text style={styles.tileValue}>{value}</Text>
      <Text style={styles.tileSub} numberOfLines={1}>{sub}</Text>
    </View>
  );
}

function GameCard({ game, week, myId, final, isMine }: { game: Game; week: number; myId: number | null; final: boolean; isMine?: boolean }) {
  const [a, b] = game.teams;
  const margin = Math.abs(a.score - b.score).toFixed(2);
  const scored = a.score > 0 || b.score > 0;
  const status = !scored ? 'Not started' : a.score === b.score ? 'Tied' : `${first(a.name)} ${final ? 'won' : 'leads'} by ${margin}`;

  return (
    <Pressable onPress={() => router.push(`/game/${week}/${game.matchup_id}`)} style={[styles.card, isMine && styles.myCard]}>
      {isMine ? <Text style={styles.yourMatchup}>YOUR MATCHUP</Text> : null}
      <TeamRow team={a} leading={scored && a.score > b.score} isMe={a.roster_id === myId} />
      <TeamRow team={b} leading={false} isMe={b.roster_id === myId} />
      <View style={styles.footer}>
        <Text style={styles.status}>{status}</Text>
        {scored ? (
          <Text style={[styles.badge, final ? styles.finalBadge : styles.liveBadge]}>{final ? 'FINAL' : 'LIVE'}</Text>
        ) : null}
      </View>
    </Pressable>
  );
}

function TeamRow({ team, leading, isMe }: { team: MatchupTeam; leading: boolean; isMe: boolean }) {
  return (
    <View style={styles.teamRow}>
      <View style={[styles.rank, team.top5 && styles.rankTop5]}>
        <Text style={[styles.rankText, team.top5 && styles.rankTextTop5]}>{team.rank}</Text>
      </View>
      <Text style={[styles.teamName, leading && styles.gold]} numberOfLines={1}>{first(team.name)}</Text>
      {isMe ? <Text style={styles.you}>YOU</Text> : null}
      <Text style={[styles.score, leading && styles.gold]}>{team.score.toFixed(2)}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  content: { gap: 12 },
  titleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-end' },
  weekPicker: {
    flexDirection: 'row', alignItems: 'center', gap: 10, borderWidth: 1, borderColor: colors.line,
    borderRadius: 12, paddingHorizontal: 12, paddingVertical: 8,
  },
  arrow: { color: colors.gold, fontSize: 22, fontWeight: '700' },
  dim: { opacity: 0.3 },
  weekText: { color: colors.text, fontSize: 16 },
  tiles: { flexDirection: 'row', gap: 8 },
  tile: { flex: 1, borderWidth: 1, borderColor: colors.line, borderRadius: 14, backgroundColor: colors.row, padding: 10, alignItems: 'center' },
  tileLabel: { color: colors.muted, fontSize: 11, fontWeight: '700', letterSpacing: 1 },
  tileValue: { color: colors.gold, fontSize: 20, fontWeight: '800', marginTop: 4 },
  tileSub: { color: colors.text, fontSize: 13, fontWeight: '600', marginTop: 2 },
  card: { borderWidth: 1, borderColor: colors.line, borderRadius: 18, backgroundColor: colors.row, overflow: 'hidden' },
  myCard: { borderColor: colors.gold, borderWidth: 1.5 },
  yourMatchup: { color: colors.gold, fontSize: 12, fontWeight: '800', letterSpacing: 1.2, paddingHorizontal: 16, paddingTop: 12 },
  teamRow: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 12, gap: 12, borderBottomWidth: 1, borderBottomColor: colors.card },
  rank: { width: 32, height: 32, borderRadius: 16, backgroundColor: colors.card, alignItems: 'center', justifyContent: 'center' },
  rankTop5: { backgroundColor: colors.gold },
  rankText: { color: colors.muted, fontWeight: '700' },
  rankTextTop5: { color: colors.bg },
  teamName: { color: colors.text, fontSize: 19, fontWeight: '700', flexShrink: 1 },
  you: { color: colors.gold, fontSize: 11, fontWeight: '800', backgroundColor: '#2A2A1A', paddingHorizontal: 8, paddingVertical: 2, borderRadius: 8, overflow: 'hidden' },
  score: { color: colors.text, fontSize: 19, fontWeight: '700', marginLeft: 'auto' },
  gold: { color: colors.gold },
  footer: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, paddingVertical: 12, backgroundColor: colors.card },
  status: { color: colors.muted, fontSize: 14, fontWeight: '600' },
  badge: { fontSize: 11, fontWeight: '800', letterSpacing: 1, paddingHorizontal: 10, paddingVertical: 3, borderRadius: 10, overflow: 'hidden' },
  liveBadge: { color: colors.live, backgroundColor: '#163626' },
  finalBadge: { color: colors.muted, backgroundColor: colors.row },
  note: { color: colors.muted, fontSize: 13, textAlign: 'center' },
});
