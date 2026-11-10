import { LinearGradient } from 'expo-linear-gradient';
import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import { Avatar } from '../../../components/Pictures';
import { ErrorState, Loading } from '../../../components/ScreenState';
import { Back, Screen, firstName, teamLabel } from '../../../components/ui';
import { type BoxPlayer, type BoxScore, LIVE_POLL_MS, useApi } from '../../../lib/api';
import { colors } from '../../../lib/theme';

export { ScreenError as ErrorBoundary } from '../../../components/ScreenState';

// Live games refresh every 15 seconds while this screen is open (the server reads the NFL scoreboard at most that often).
const LIVE_REFRESH_MS = LIVE_POLL_MS;
const THEM = '#D08B6A';
// Live row colors: game on, his team has the ball, his team is in the red zone.
const LIVE_LOOK = {
  live: { edge: colors.gold, tint: ['#3A3018', '#1D2A1D'] },
  offense: { edge: colors.live, tint: ['#1C4A33', '#173326'] },
  redZone: { edge: '#E05555', tint: ['#4A1E1E', '#2A1F1C'] },
} as const;

type Side = BoxScore['teams'][number];

// One game, slot by slot: your side on the left when you're playing.
// "Spotlight": players whose NFL game is on get a colored edge and tint: gold while his game is on,
// green when his team has the ball, red in the red zone. No extra text; the colors say it.
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
  const open = (teamId: string | null) => (teamId ? () => router.push(`/team/${teamId}`) : undefined);

  return (
    <Screen loading={loading}>
      <Back label="Matchups" />
      <View style={styles.header}>
        <ScoreSide side={a} other={b} final={data.is_final} onPress={open(a.team_id)} me />
        <ScoreSide side={b} other={a} final={data.is_final} onPress={open(b.team_id)} right />
      </View>
      {data.is_final ? <FinalBanner a={a} b={b} /> : odds ? <WinOdds odds={odds} /> : null}
      <Text style={styles.section}>STARTERS</Text>
      <Lineup left={a.players} right={b.players} onScore={setShown} />
      {a.bench?.length || b.bench?.length ? (
        <>
          <Text style={[styles.section, styles.sectionBench]}>BENCH</Text>
          <Lineup left={a.bench ?? []} right={b.bench ?? []} onScore={setShown} bench />
        </>
      ) : null}
      <Breakdown player={shown} onClose={() => setShown(null)} />
    </Screen>
  );
}

// Two lineups side by side, row by row. Bench rows pair by order (QB, RB, WR, TE, then projection) and show each player's position.
function Lineup({ left, right, onScore, bench }: { left: BoxPlayer[]; right: BoxPlayer[]; onScore: (p: BoxPlayer) => void; bench?: boolean }) {
  const rows = Math.max(left.length, right.length);
  return (
    <View style={styles.table}>
      {Array.from({ length: rows }).map((_, i) => {
        const pa = left[i];
        const pb = right[i];
        return (
          <View key={i} style={styles.row}>
            <Half p={pa} vs={pb} side="l" onScore={onScore} bench={bench} first={i === 0} />
            <View style={[styles.slot, i > 0 && styles.divider]}><Text style={styles.slotText}>{bench ? 'BN' : pa?.slot || pb?.slot || ''}</Text></View>
            <Half p={pb} vs={pa} side="r" onScore={onScore} bench={bench} first={i === 0} />
          </View>
        );
      })}
    </View>
  );
}

function ScoreSide({ side, other, final, onPress, me, right }: { side: Side; other: Side; final: boolean; onPress?: () => void; me?: boolean; right?: boolean }) {
  const won = side.score > other.score;
  const sub = final ? `${teamLabel(side, true)} · ${won ? 'W' : side.score < other.score ? 'L' : 'T'}` : side.projected != null ? `proj ${side.projected.toFixed(1)}` : teamLabel(side, true);
  return (
    <Pressable onPress={onPress} style={[styles.scoreSide, right && styles.scoreSideRight]}>
      <Avatar uri={side.avatar} name={teamLabel(side)} size={36} me={me} />
      <View style={right && styles.alignRight}>
        <Text style={[styles.score, me && styles.gold]}>{side.score.toFixed(1)}</Text>
        <Text style={[styles.sub, final && won && styles.win]}>{sub}</Text>
      </View>
    </Pressable>
  );
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
  const lead = a.score === b.score ? 'Tied' : `${teamLabel(a.score > b.score ? a : b, true)} wins by ${margin}`;
  return <Text style={styles.banner}>{lead}{top && top.points > 0 ? ` · ${shortName(top.name)} top scorer (${top.points.toFixed(1)})` : ''}</Text>;
}

// One player's half of a row, ESPN style: name + team, game ("@BAL 33-10 Final"), one short stat line
// ("7 REC, 51 YDS, TD"), with big points and the projection on the inside edge next to the slot. The right side is mirrored.
// Live = gold edge + warm tint; has the ball = green; red zone = red.
// The slot winner's points are gold once both players have played.
function Half({ p, vs, side, onScore, bench, first }: { p?: BoxPlayer; vs?: BoxPlayer; side: 'l' | 'r'; onScore: (p: BoxPlayer) => void; bench?: boolean; first?: boolean }) {
  if (!p || !p.player_id || p.player_id === '0') return <View style={[styles.half, bench && styles.halfBench, !first && styles.divider]}><Text style={styles.empty}>{p ? 'Empty' : ''}</Text></View>;
  const g = p.game;
  const on = p.status === 'playing';
  const right = side === 'r';
  const look = on ? (g?.red_zone ? LIVE_LOOK.redZone : g?.possession ? LIVE_LOOK.offense : LIVE_LOOK.live) : null;
  const edge = look?.edge ?? null;
  const tint = look?.tint ?? null;
  const scored = p.status === 'playing' || p.status === 'final';
  const vsScored = !!vs && (vs.status === 'playing' || vs.status === 'final');
  const lead = !bench && scored && vsScored && vs ? (p.points > vs.points ? 'win' : p.points < vs.points ? 'lose' : null) : null;
  const line = p.short_line ?? p.line ?? [];
  const align = right ? styles.textRight : null;
  return (
    <View style={[styles.half, right && styles.halfRight, bench && styles.halfBench, !first && styles.divider, right ? { borderRightWidth: 4, borderRightColor: edge ?? 'transparent' } : { borderLeftWidth: 4, borderLeftColor: edge ?? 'transparent' }]}>
      {tint ? <LinearGradient colors={tint as [string, string]} start={{ x: right ? 1 : 0, y: 0 }} end={{ x: right ? 0.2 : 0.8, y: 0 }} style={StyleSheet.absoluteFill} /> : null}
      <View style={[styles.info, right && styles.infoRight]}>
        <Pressable onPress={() => router.push(`/player/${p.player_id}`)} hitSlop={4} style={[styles.nameWrap, right && styles.nameWrapRight]}>
          <Text style={styles.name} numberOfLines={1}>{shortName(p.name)}</Text>
          {bench && p.pos ? <Text style={styles.benchPos}>{p.pos}</Text> : null}
          {p.team ? <Text style={styles.team}>{p.team}</Text> : null}
        </Pressable>
        <GameText p={p} right={right} />
        <Text style={[styles.stats, align]} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.85}>
          {line.length
            ? line.map((x, i) => (
                <Text key={i} style={x.tone === 'good' ? styles.good : x.tone === 'bad' ? styles.bad : undefined}>{i ? ', ' : ''}{x.text}</Text>
              ))
            : <Text style={styles.statsEmpty}>{p.status === 'bye' ? 'On bye' : scored ? 'No stats yet' : ' '}</Text>}
        </Text>
      </View>
      <Pressable onPress={() => onScore(p)} hitSlop={8} style={[styles.pts, right && styles.ptsRight]}>
        <Text style={[styles.points, lead === 'win' && styles.gold, lead === 'lose' && styles.pointsLose, bench && styles.pointsBench]}>{scored || p.points ? p.points.toFixed(1) : '–'}</Text>
        <Text style={[styles.proj, on && p.live_proj != null && styles.projLive]}>{shownProj(p)}</Text>
      </Pressable>
    </View>
  );
}

// ESPN style: "@MIN 13-6 10:19 4th" live, "@BAL 33-10 Final" after, "@BAL Sun 11:00 AM" before, or BYE.
// "@" is an away game; a home game is just the opponent.
function GameText({ p, right }: { p: BoxPlayer; right?: boolean }) {
  const g = p.game;
  const al = right ? styles.textRight : null;
  if (p.status === 'bye') return <Text style={[styles.game, al]}>BYE</Text>;
  const opp = g ? `${g.home ? '' : '@'}${g.opp}` : '';
  if (g && (p.status === 'playing' || g.state === 'in')) {
    return (
      <Text style={[styles.game, al]} numberOfLines={1}>{opp} {g.score}-{g.opp_score} <Text style={styles.clock}>{liveClock(g)}</Text></Text>
    );
  }
  if (g && (p.status === 'final' || g.state === 'post')) {
    return <Text style={[styles.game, al]} numberOfLines={1}>{opp} {g.score}-{g.opp_score} Final</Text>;
  }
  if (p.status === 'final') return <Text style={[styles.game, al]}>{p.rank ?? 'Final'}</Text>;
  return <Text style={[styles.game, al]} numberOfLines={1}>{[opp, p.kickoff ? kickoffText(p.kickoff) : null].filter(Boolean).join(' ')}</Text>;
}

const ORDINAL = ['', '1st', '2nd', '3rd', '4th'];
// "10:19 4th", "Halftime" or "OT 3:12".
function liveClock(g: NonNullable<BoxPlayer['game']>) {
  if (/half/i.test(g.detail ?? '')) return 'Half';
  if (g.quarter > 4) return `OT ${g.clock}`;
  return `${g.clock} ${ORDINAL[g.quarter] ?? ''}`.trim();
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
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 },
  scoreSide: { flexDirection: 'row', alignItems: 'center', gap: 8, flex: 1 },
  scoreSideRight: { flexDirection: 'row-reverse' },
  alignRight: { alignItems: 'flex-end' },
  score: { color: colors.text, fontSize: 28, fontWeight: '900', fontVariant: ['tabular-nums'] },
  sub: { color: colors.muted, fontSize: 11, marginTop: -1 },
  win: { color: colors.live, fontWeight: '800' },
  gold: { color: colors.gold },
  odds: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 10 },
  oddsPct: { color: colors.text, fontSize: 11, fontWeight: '800', fontVariant: ['tabular-nums'] },
  oddsBar: { flex: 1, height: 6, borderRadius: 3, backgroundColor: '#24372D', overflow: 'hidden' },
  oddsBarRight: { flexDirection: 'row', justifyContent: 'flex-end' },
  oddsFill: { height: 6, backgroundColor: colors.live },
  oddsLabel: { color: colors.muted, fontSize: 10, fontWeight: '800' },
  banner: { color: colors.gold, fontSize: 12, fontWeight: '700', backgroundColor: '#122B20', borderRadius: 10, overflow: 'hidden', paddingHorizontal: 10, paddingVertical: 8, marginBottom: 10 },
  table: { borderRadius: 14, overflow: 'hidden', backgroundColor: colors.card, marginHorizontal: -10 },
  section: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1.2, marginBottom: 6 },
  sectionBench: { color: colors.muted, marginTop: 16 },
  row: { flexDirection: 'row', alignItems: 'stretch' },
  half: { flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: colors.card, paddingLeft: 10, paddingRight: 6, paddingVertical: 9, overflow: 'hidden' },
  halfRight: { flexDirection: 'row-reverse', paddingLeft: 6, paddingRight: 10 },
  info: { flex: 1, minWidth: 0 },
  infoRight: { alignItems: 'flex-end' },
  nameWrapRight: { flexDirection: 'row-reverse' },
  textRight: { textAlign: 'right' },
  pts: { alignItems: 'flex-end', minWidth: 38 },
  ptsRight: { alignItems: 'flex-start' },
  divider: { borderTopWidth: 1, borderTopColor: '#1F3F31' },
  halfBench: { backgroundColor: '#0E2119' },
  empty: { color: colors.muted, fontSize: 13 },
  slot: { width: 28, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.card },
  slotText: { color: '#8E8676', fontSize: 10, fontWeight: '800' },
  nameWrap: { flexDirection: 'row', alignItems: 'center', gap: 4, flexShrink: 1 },
  name: { color: colors.text, fontSize: 14, fontWeight: '800', flexShrink: 1 },
  team: { flexShrink: 0, color: '#8E8676', fontSize: 10, fontWeight: '700' },
  points: { color: colors.text, fontSize: 17, fontWeight: '800', fontVariant: ['tabular-nums'] },
  pointsBench: { color: '#C9C2B2' },
  pointsLose: { color: colors.muted },
  benchPos: { flexShrink: 0, color: colors.muted, fontSize: 10, fontWeight: '800' },
  proj: { color: colors.muted, fontSize: 10.5, marginTop: 1 },
  projLive: { color: colors.text, fontWeight: '700' },
  game: { color: colors.muted, fontSize: 10.5, marginTop: 2, flexShrink: 1, alignSelf: 'stretch' },
  clock: { color: colors.live, fontWeight: '800' },
  stats: { color: '#D9D0BD', fontSize: 11, fontWeight: '600', marginTop: 2, lineHeight: 15, alignSelf: 'stretch' },
  statsEmpty: { color: '#8E8676', fontWeight: '500' },
  good: { color: colors.live, fontWeight: '800' },
  bad: { color: '#EF7A7A', fontWeight: '800' },
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
