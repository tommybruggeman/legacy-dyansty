import { router } from 'expo-router';
import { type ReactNode, useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Avatar } from '../Pictures';
import { ErrorState, Loading } from '../ScreenState';
import { type Game, type Matchups as MatchupsData, type MatchupTeam, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

const first = (name: string) => name.split(' ')[0];

// Live games refresh every 30 seconds while this screen is open.
const LIVE_REFRESH_MS = 30_000;


// Built from the phone Weekly Matchups design: week picker, three summary
// tiles, your game pinned first (Scorebug cards), gold rank = a Top 5 score this week.
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
  const started = data.games.some((g) => g.teams.some((t) => t.score > 0));
  const status = !started ? null : data.is_final ? 'FINAL' : 'LIVE';

  return (
    <View style={styles.content}>
      <View style={styles.titleRow}>
        {status ? <Text style={[styles.badge, data.is_final ? styles.finalBadge : styles.liveBadge]}>{status}</Text> : <View />}
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

      {mine ? <GameCard game={mine} week={shown} myId={data.my_roster_id} weekFinal={data.is_final} isMine /> : null}
      {others.map((game) => (
        <GameCard key={game.matchup_id} game={game} week={shown} myId={data.my_roster_id} weekFinal={data.is_final} />
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

// Scorebug cards (picked by Tommy from the 10 list designs): rank, owner face,
// record and score per row. Live games get a green edge and a "playing / yet
// to play" footer; finished games fade the loser and point at the winner.
function GameCard({ game, week, myId, weekFinal, isMine }: { game: Game; week: number; myId: number | null; weekFinal: boolean; isMine?: boolean }) {
  const [a, b] = game.teams;
  const playing = (a.playing ?? 0) + (b.playing ?? 0);
  const toPlay = (a.to_play ?? 0) + (b.to_play ?? 0);
  const started = a.score > 0 || b.score > 0 || playing > 0;
  const done = weekFinal || (started && playing === 0 && toPlay === 0 && a.playing != null);
  const state = done ? 'final' : started ? 'live' : 'pre';
  const winner = state === 'final' && a.score !== b.score ? (a.score > b.score ? a.roster_id : b.roster_id) : null;
  return (
    <Pressable onPress={() => router.push(`/game/${week}/${game.matchup_id}`)} style={[styles.card, isMine && styles.myCard]}>
      {state === 'live' ? <View style={styles.liveEdge} /> : null}
      <TeamRow team={a} state={state} isMe={a.roster_id === myId} winner={winner} />
      <TeamRow team={b} state={state} isMe={b.roster_id === myId} winner={winner} />
      <Footer game={game} state={state} playing={playing} toPlay={toPlay} />
    </Pressable>
  );
}

function TeamRow({ team, state, isMe, winner }: { team: MatchupTeam; state: 'pre' | 'live' | 'final'; isMe: boolean; winner: number | null }) {
  const lost = winner != null && winner !== team.roster_id;
  return (
    <View style={[styles.teamRow, lost && styles.lost]}>
      <View style={[styles.rank, team.top5 && styles.rankTop5]}>
        <Text style={[styles.rankText, team.top5 && styles.rankTextTop5]}>{team.rank}</Text>
      </View>
      <Avatar uri={team.avatar} name={team.name} size={30} me={isMe} />
      <View style={styles.nameBox}>
        <Text style={styles.teamName} numberOfLines={1}>{first(team.name)}</Text>
        {isMe ? <Text style={styles.you}>YOU</Text> : null}
        {team.record ? <Text style={styles.record}>{team.record}</Text> : null}
      </View>
      <View style={styles.scoreBox}>
        <View style={styles.scoreLine}>
          <Text style={[styles.score, state === 'pre' && styles.scorePre]}>{team.score.toFixed(2)}</Text>
          {winner === team.roster_id ? <Text style={styles.winArrow}>◀</Text> : null}
        </View>
        {state !== 'final' && team.projected != null ? <Text style={styles.proj}>proj {team.projected.toFixed(1)}</Text> : null}
      </View>
    </View>
  );
}

const kickoffText = (iso?: string | null) => {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? '' : d.toLocaleString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit' }).replace(',', '');
};

function Footer({ game, state, playing, toPlay }: { game: Game; state: 'pre' | 'live' | 'final'; playing: number; toPlay: number }) {
  const [a, b] = game.teams;
  let left: ReactNode = null;
  let right = '';
  if (state === 'final') {
    left = <Text style={styles.footText}>FINAL</Text>;
    right = `Margin ${Math.abs(a.score - b.score).toFixed(2)}`;
  } else if (state === 'live') {
    left = playing > 0 ? (
      <View style={styles.liveRow}><View style={styles.liveDot} /><Text style={styles.liveText}>{playing} PLAYING</Text></View>
    ) : <Text style={styles.footText}>Next {kickoffText(game.next_kickoff)}</Text>;
    right = toPlay > 0 ? `${toPlay} yet to play` : 'last ones playing';
  } else {
    left = <Text style={styles.footText}>{game.win_chance ? `Win odds ${game.win_chance[0]}% · ${game.win_chance[1]}%` : 'Not started'}</Text>;
    right = kickoffText(game.next_kickoff);
  }
  return (
    <View style={styles.footer}>
      {left}
      <Text style={styles.footText}>{right}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  content: { gap: 12 },
  titleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
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
  card: { borderRadius: 16, backgroundColor: colors.row, overflow: 'hidden', borderWidth: 1, borderColor: '#1B2E24' },
  myCard: { borderColor: colors.gold, borderWidth: 1.5 },
  liveEdge: { position: 'absolute', left: 0, top: 0, bottom: 0, width: 4, backgroundColor: colors.live, zIndex: 1 },
  teamRow: { flexDirection: 'row', alignItems: 'center', paddingLeft: 16, paddingRight: 14, paddingTop: 10, paddingBottom: 8, gap: 10 },
  lost: { opacity: 0.5 },
  rank: { width: 26, height: 26, borderRadius: 13, backgroundColor: colors.card, alignItems: 'center', justifyContent: 'center' },
  rankTop5: { backgroundColor: colors.gold },
  rankText: { color: colors.muted, fontSize: 12, fontWeight: '800' },
  rankTextTop5: { color: colors.bg },
  nameBox: { flexDirection: 'row', alignItems: 'center', gap: 6, flexShrink: 1 },
  teamName: { color: colors.text, fontSize: 16, fontWeight: '800', flexShrink: 1 },
  you: { color: colors.gold, fontSize: 9.5, fontWeight: '800', letterSpacing: 0.6, backgroundColor: '#2A2A1A', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 6, overflow: 'hidden' },
  record: { color: colors.muted, opacity: 0.6, fontSize: 11, fontWeight: '600' },
  scoreBox: { marginLeft: 'auto', alignItems: 'flex-end', minWidth: 86 },
  scoreLine: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  score: { color: colors.text, fontSize: 21, fontWeight: '900' },
  scorePre: { opacity: 0.4 },
  winArrow: { color: colors.text, fontSize: 10 },
  proj: { color: colors.muted, fontSize: 10.5, fontWeight: '600', opacity: 0.55, marginTop: 1 },
  footer: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingLeft: 16, paddingRight: 14, paddingVertical: 8, borderTopWidth: 1, borderTopColor: '#1B2E24' },
  footText: { color: colors.muted, fontSize: 11, fontWeight: '700' },
  liveRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  liveDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: colors.live, shadowColor: colors.live, shadowOpacity: 0.8, shadowRadius: 4 },
  liveText: { color: colors.live, fontSize: 11, fontWeight: '800', letterSpacing: 0.5 },
  badge: { fontSize: 11, fontWeight: '800', letterSpacing: 1, paddingHorizontal: 10, paddingVertical: 3, borderRadius: 10, overflow: 'hidden' },
  liveBadge: { color: colors.live, backgroundColor: '#163626' },
  finalBadge: { color: colors.muted, backgroundColor: colors.row },
  note: { color: colors.muted, fontSize: 13, textAlign: 'center' },
});
