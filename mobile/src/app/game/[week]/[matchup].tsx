import { LinearGradient } from 'expo-linear-gradient';
import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import { Avatar } from '../../../components/Pictures';
import { ErrorState, Loading } from '../../../components/ScreenState';
import { Back, Screen, firstName } from '../../../components/ui';
import { type BoxPlayer, type BoxScore, useApi } from '../../../lib/api';
import { teamColors } from '../../../lib/nflColors';
import { colors } from '../../../lib/theme';

export { ScreenError as ErrorBoundary } from '../../../components/ScreenState';

// Live games refresh every 15 seconds while this screen is open (the server reads the NFL scoreboard at most that often).
const LIVE_REFRESH_MS = 15_000;
const THEM = '#D08B6A';
const RED = '#E05555';

type Side = BoxScore['teams'][number];

// One game, slot by slot: your side on the left when you're playing.
// "Spotlight": players whose NFL game is on get a gold edge and a warm tint, brighter when
// their team has the ball and red in the red zone, with the game clock, score and stat line.
export default function Game() {
  const { week, matchup } = useLocalSearchParams<{ week: string; matchup: string }>();
  const [live, setLive] = useState(true);
  const [shown, setShown] = useState<BoxPlayer | null>(null);
  const { data, error, loading, reload } = useApi<BoxScore>(`/matchups/${week}/${matchup}`, live ? LIVE_REFRESH_MS : 0);
  useEffect(() => { if (data) setLive(!data.is_final); }, [data]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const swap = data.teams[1]?.roster_id === data.my_roster_id;
  const [a, b] = swap ? [data.teams[1], data.teams[0]] : data.teams;
  const odds = data.win_chance ? (swap ? [data.win_chance[1], data.win_chance[0]] : data.win_chance) : null;
  const rows = Math.max(a.players.length, b.players.length);
  const players = [...a.players, ...b.players];
  const playing = players.filter((p) => p.status === 'playing').length;
  const started = players.some((p) => p.status === 'playing' || p.status === 'final');
  const open = (teamId: string | null) => (teamId ? () => router.push(`/team/${teamId}`) : undefined);

  return (
    <Screen loading={loading} onRefresh={reload} fill>
      <Back label="Matchups" />
      <Text style={styles.eyebrow}>WEEK {data.week} · {firstName(a.name).toUpperCase()} VS {firstName(b.name).toUpperCase()}</Text>
      <View style={styles.header}>
        <ScoreSide side={a} other={b} final={data.is_final} onPress={open(a.team_id)} me />
        <StatusPill final={data.is_final} playing={playing} started={started} won={a.score > b.score} players={players} />
        <ScoreSide side={b} other={a} final={data.is_final} onPress={open(b.team_id)} right />
      </View>
      {data.is_final ? <FinalBanner a={a} b={b} /> : odds ? <WinOdds odds={odds} /> : null}
      <View style={styles.table}>
        {Array.from({ length: rows }).map((_, i) => {
          const pa = a.players[i];
          const pb = b.players[i];
          return (
            <View key={i} style={styles.row}>
              <Half p={pa} side="l" onScore={setShown} />
              <View style={styles.slot}><Text style={styles.slotText}>{pa?.slot || pb?.slot || ''}</Text></View>
              <Half p={pb} side="r" onScore={setShown} />
            </View>
          );
        })}
      </View>
      <Breakdown player={shown} onClose={() => setShown(null)} />
    </Screen>
  );
}

function ScoreSide({ side, other, final, onPress, me, right }: { side: Side; other: Side; final: boolean; onPress?: () => void; me?: boolean; right?: boolean }) {
  const won = side.score > other.score;
  const sub = final ? `${firstName(side.name)} · ${won ? 'W' : side.score < other.score ? 'L' : 'T'}` : side.projected != null ? `proj ${side.projected.toFixed(1)}` : firstName(side.name);
  return (
    <Pressable onPress={onPress} style={[styles.scoreSide, right && styles.scoreSideRight]}>
      <Avatar name={side.name} size={36} me={me} />
      <View style={right && styles.alignRight}>
        <Text style={[styles.score, me && styles.gold]}>{side.score.toFixed(1)}</Text>
        <Text style={[styles.sub, final && won && styles.win]}>{sub}</Text>
      </View>
    </Pressable>
  );
}

function StatusPill({ final, playing, started, won, players }: { final: boolean; playing: number; started: boolean; won: boolean; players: BoxPlayer[] }) {
  if (final) return <Text style={[styles.pill, styles.pillFinal]}>{won ? 'FINAL · YOU WON' : 'FINAL'}</Text>;
  if (playing) {
    return (
      <View style={[styles.pillRow, styles.pillLive]}>
        <View style={styles.dot} />
        <Text style={styles.pillLiveText}>LIVE · {playing} PLAYING</Text>
      </View>
    );
  }
  const next = players.map((p) => p.kickoff).filter((k): k is string => !!k && Date.parse(k) > Date.now()).sort()[0];
  return <Text style={styles.pill}>{next ? `NEXT ${kickoffText(next).toUpperCase()}` : started ? 'BETWEEN GAMES' : 'NOT STARTED'}</Text>;
}

function WinOdds({ odds }: { odds: number[] }) {
  return (
    <View style={styles.odds}>
      <Text style={styles.oddsPct}>{odds[0]}%</Text>
      <View style={styles.oddsBar}><View style={[styles.oddsFill, { width: `${odds[0]}%` }]} /></View>
      <Text style={styles.oddsLabel}>Win odds</Text>
      <View style={[styles.oddsBar, styles.oddsBarRight]}><View style={[styles.oddsFill, { width: `${odds[1]}%` }]} /></View>
      <Text style={styles.oddsPct}>{odds[1]}%</Text>
    </View>
  );
}

function FinalBanner({ a, b }: { a: Side; b: Side }) {
  const top = [...a.players, ...b.players].reduce<BoxPlayer | null>((best, p) => (!best || p.points > best.points ? p : best), null);
  const margin = Math.abs(a.score - b.score).toFixed(1);
  const lead = a.score === b.score ? 'Tied' : `${firstName(a.score > b.score ? a.name : b.name)} wins by ${margin}`;
  return <Text style={styles.banner}>{lead}{top && top.points > 0 ? ` · ${shortName(top.name)} top scorer (${top.points.toFixed(1)})` : ''}</Text>;
}

// One player's half of a row. Live = gold edge on the outside + warm tint; has the ball = brighter; red zone = red edge.
function Half({ p, side, onScore }: { p?: BoxPlayer; side: 'l' | 'r'; onScore: (p: BoxPlayer) => void }) {
  if (!p || !p.player_id || p.player_id === '0') return <View style={styles.half}><Text style={styles.empty}>{p ? 'Empty' : ''}</Text></View>;
  const g = p.game;
  const on = p.status === 'playing';
  const right = side === 'r';
  const edge = on ? (g?.red_zone ? RED : colors.gold) : null;
  const tint = on ? (g?.possession ? ['#4A3C1A', '#2A2D18'] : ['#3A3018', '#1D2A1D']) : null;
  const scored = p.status === 'playing' || p.status === 'final';
  return (
    <View style={[styles.half, right ? { borderRightWidth: 4, borderRightColor: edge ?? 'transparent' } : { borderLeftWidth: 4, borderLeftColor: edge ?? 'transparent' }]}>
      {tint ? <LinearGradient colors={tint as [string, string]} start={{ x: right ? 1 : 0, y: 0 }} end={{ x: right ? 0.2 : 0.8, y: 0 }} style={StyleSheet.absoluteFill} /> : null}
      <View style={styles.line}>
        <Pressable onPress={() => router.push(`/player/${p.player_id}`)} hitSlop={4} style={styles.nameWrap}>
          <Text style={styles.name} numberOfLines={1}>{shortName(p.name)}</Text>
          {p.team ? <Text style={[styles.chip, { backgroundColor: teamColors(p.team)[0] }]}>{p.team}</Text> : null}
          {on && g?.possession ? <View style={styles.ball}><View style={styles.lace} /></View> : null}
        </Pressable>
        <Pressable onPress={() => onScore(p)} hitSlop={8}>
          <Text style={[styles.points, side === 'l' && styles.gold]}>{scored || p.points ? p.points.toFixed(1) : '–'}</Text>
        </Pressable>
      </View>
      <View style={styles.line}>
        <GameText p={p} />
        <Text style={[styles.proj, on && p.live_proj != null && styles.projLive]}>{shownProj(p)}</Text>
      </View>
      {p.line?.length ? (
        <Text style={styles.stats}>
          {p.line.map((x, i) => (
            <Text key={i} style={x.tone === 'good' ? styles.good : x.tone === 'bad' ? styles.bad : undefined}>{i ? ', ' : ''}{x.text}</Text>
          ))}
        </Text>
      ) : null}
      {on && g?.possession ? (
        <Text style={[styles.offense, g.red_zone && styles.redZone]}>{g.red_zone ? 'RED ZONE' : 'ON OFFENSE'}{g.down ? ` · ${g.down}` : ''}</Text>
      ) : null}
    </View>
  );
}

// "Q3 8:42 24-13 vs MIA" live, "Final W 34-20 vs MIA" after, "Sun 11:00 AM vs MIA" before, or BYE.
function GameText({ p }: { p: BoxPlayer }) {
  const g = p.game;
  if (p.status === 'bye') return <Text style={styles.game}>BYE</Text>;
  const vs = g ? `${g.home ? 'vs' : '@'} ${g.opp}` : '';
  if (g && (p.status === 'playing' || g.state === 'in')) {
    return (
      <View style={styles.gameRow}>
        <View style={styles.dot} />
        <Text style={styles.game} numberOfLines={1}><Text style={styles.clock}>{g.quarter > 4 ? 'OT' : `Q${g.quarter}`} {g.clock}</Text> {g.score}-{g.opp_score} {vs}</Text>
      </View>
    );
  }
  if (g && (p.status === 'final' || g.state === 'post')) {
    const res = g.score > g.opp_score ? 'W' : g.score < g.opp_score ? 'L' : 'T';
    return <Text style={styles.game} numberOfLines={1}><Text style={styles.finalWord}>Final</Text> {res} {g.score}-{g.opp_score} {vs}</Text>;
  }
  if (p.status === 'final') return <Text style={styles.game}>{p.rank ?? 'Final'}</Text>;
  return <Text style={styles.game} numberOfLines={1}>{[p.kickoff ? kickoffText(p.kickoff) : null, vs].filter(Boolean).join(' ')}</Text>;
}

// Mid-game the projection follows the game: points so far plus his projection for the time left.
function shownProj(p: BoxPlayer) {
  const v = p.status === 'playing' && p.live_proj != null ? p.live_proj : p.proj;
  return v != null ? v.toFixed(1) : '';
}

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
// "Sun 11:00 AM" in the phone's own time zone.
function kickoffText(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const h = d.getHours() % 12 || 12;
  return `${DAYS[d.getDay()]} ${h}:${String(d.getMinutes()).padStart(2, '0')} ${d.getHours() < 12 ? 'AM' : 'PM'}`;
}

// "Justin Jefferson" -> "J. Jefferson" (keeps suffixes like III); team defenses stay whole.
function shortName(name: string) {
  const parts = name.split(' ').filter(Boolean);
  if (parts.length < 2 || /D\/ST|Defense/i.test(name)) return name;
  return `${parts[0][0]}. ${parts.slice(1).join(' ')}`;
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

const styles = StyleSheet.create({
  eyebrow: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1.2, marginBottom: 6 },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 },
  scoreSide: { flexDirection: 'row', alignItems: 'center', gap: 8, flex: 1 },
  scoreSideRight: { flexDirection: 'row-reverse' },
  alignRight: { alignItems: 'flex-end' },
  score: { color: colors.text, fontSize: 28, fontWeight: '900', fontVariant: ['tabular-nums'] },
  sub: { color: colors.muted, fontSize: 11, marginTop: -1 },
  win: { color: colors.live, fontWeight: '800' },
  gold: { color: colors.gold },
  pill: { color: colors.muted, backgroundColor: '#16301F', fontSize: 9, fontWeight: '800', letterSpacing: 0.5, paddingHorizontal: 8, paddingVertical: 4, borderRadius: 10, overflow: 'hidden' },
  pillFinal: { color: colors.gold, backgroundColor: '#2A2A1A' },
  pillRow: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 8, paddingVertical: 4, borderRadius: 10 },
  pillLive: { backgroundColor: 'rgba(76,195,138,0.16)' },
  pillLiveText: { color: colors.live, fontSize: 9, fontWeight: '800', letterSpacing: 0.5 },
  dot: { width: 7, height: 7, borderRadius: 4, backgroundColor: colors.live, marginRight: 5 },
  odds: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 10 },
  oddsPct: { color: colors.text, fontSize: 11, fontWeight: '800', fontVariant: ['tabular-nums'] },
  oddsBar: { flex: 1, height: 6, borderRadius: 3, backgroundColor: '#24372D', overflow: 'hidden' },
  oddsBarRight: { flexDirection: 'row', justifyContent: 'flex-end' },
  oddsFill: { height: 6, backgroundColor: colors.live },
  oddsLabel: { color: colors.muted, fontSize: 10, fontWeight: '800' },
  banner: { color: colors.gold, fontSize: 12, fontWeight: '700', backgroundColor: '#122B20', borderRadius: 10, overflow: 'hidden', paddingHorizontal: 10, paddingVertical: 8, marginBottom: 10 },
  table: { flexGrow: 1, borderRadius: 14, overflow: 'hidden', backgroundColor: colors.bg },
  row: { flexGrow: 1, flexDirection: 'row', alignItems: 'stretch', marginBottom: 3 },
  half: { flex: 1, minWidth: 0, justifyContent: 'center', backgroundColor: '#11271D', paddingHorizontal: 8, paddingVertical: 7, overflow: 'hidden' },
  empty: { color: colors.muted, fontSize: 12 },
  slot: { width: 30, alignItems: 'center', justifyContent: 'center', backgroundColor: '#08140F' },
  slotText: { color: colors.muted, fontSize: 10, fontWeight: '800' },
  line: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 4 },
  nameWrap: { flexDirection: 'row', alignItems: 'center', gap: 4, flexShrink: 1 },
  name: { color: colors.text, fontSize: 13, fontWeight: '800', flexShrink: 1 },
  chip: { flexShrink: 0, color: '#FFFFFF', fontSize: 8, fontWeight: '800', paddingHorizontal: 3, paddingVertical: 1, borderRadius: 3, overflow: 'hidden' },
  points: { color: colors.text, fontSize: 15, fontWeight: '800', fontVariant: ['tabular-nums'] },
  proj: { color: colors.muted, fontSize: 10 },
  projLive: { color: colors.text, fontWeight: '700' },
  gameRow: { flexDirection: 'row', alignItems: 'center', flexShrink: 1 },
  game: { color: colors.muted, fontSize: 10, marginTop: 2, flexShrink: 1 },
  clock: { color: colors.live, fontWeight: '800' },
  finalWord: { fontWeight: '800' },
  ball: { flexShrink: 0, width: 13, height: 8, borderRadius: 5, backgroundColor: '#9A5A2B', justifyContent: 'center', alignItems: 'center' },
  lace: { width: 5, height: 1, backgroundColor: '#FFFFFF' },
  stats: { color: '#D9D0BD', fontSize: 10.5, fontWeight: '600', marginTop: 3, lineHeight: 14 },
  good: { color: colors.live, fontWeight: '800' },
  bad: { color: '#EF7A7A', fontWeight: '800' },
  offense: { color: colors.gold, fontSize: 9, fontWeight: '800', letterSpacing: 0.4, marginTop: 4 },
  redZone: { color: '#FF7D7D' },
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
