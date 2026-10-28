import { router, useLocalSearchParams } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Headshot } from '../../components/Pictures';
import { PointsChart } from '../../components/player/PointsChart';
import { ErrorState, Loading } from '../../components/ScreenState';
import { Back, Screen, money } from '../../components/ui';
import { type PlayerPage, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';
import { useTargets } from '../../lib/targets';

export { ScreenError as ErrorBoundary } from '../../components/ScreenState';

// Any player, from any list: real NFL stats next to how he's done in this
// league, the points chart (tap it for the full game log), next game,
// contract history and health.
export default function Player() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, error, loading, reload } = useApi<PlayerPage>(`/players/${id}`);
  const { isTarget, toggle } = useTargets();

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const c = data.contract;
  const ls = data.league_season ?? { ppg: null, ppg_started: null, rank: null, started: 0, played: 0, per_dollar: null };
  const nfl = data.nfl_season;
  const log = data.game_log ?? [];
  const history = data.history ?? [];
  const name = data.name ?? 'Player';
  const owner = c?.owner_name ?? data.rostered_by?.owner_name ?? '';
  const eyebrow = [data.pos, data.nfl_team, data.age ? `AGE ${Math.floor(data.age)}` : null, c?.is_rookie ? 'ROOKIE' : null].filter(Boolean).join(' · ');
  const h: PlayerPage['health'] = data.health ?? { healthy: true, status: 'Healthy', detail: null, update: null, return: null, this_week: null, missed: [] };
  const missed = h.missed ?? [];

  return (
    <Screen loading={loading} onRefresh={reload}>
      <Back label="Back" />
      <View style={styles.stack}>
        <View style={styles.hero}>
          <Headshot playerId={data.player_id} pos={data.pos} team={data.nfl_team} size={84} />
          <View style={styles.heroText}>
            <Text style={styles.eyebrow}>{eyebrow}</Text>
            <Text style={styles.name} numberOfLines={2}>{name}</Text>
            <View style={styles.pills}>
              {data.is_mine ? (
                <Text style={[styles.pill, styles.pillGold]}>YOURS</Text>
              ) : c || data.rostered_by ? (
                <Text style={[styles.pill, styles.pillLine]}>{(owner.split(' ')[0] || 'OWNED').toUpperCase()}</Text>
              ) : (
                <Text style={[styles.pill, styles.pillLine]}>FREE AGENT</Text>
              )}
              <Text style={[styles.pill, h.healthy ? styles.pillOk : styles.pillOut]}>{h.healthy ? 'Healthy' : h.status}</Text>
            </View>
          </View>
        </View>

        <View style={styles.tiles}>
          <Tile label="SALARY" value={c ? money(c.salary) : data.rostered_by ? '—' : 'FA'} sub={c ? `${c.years_left} yr${c.years_left === 1 ? '' : 's'} left` : data.rostered_by ? 'no contract yet' : 'not signed'} />
          <Tile label="PPG" value={ls.ppg != null ? ls.ppg.toFixed(1) : '—'} sub={ls.ppg_started != null ? `${ls.ppg_started.toFixed(1)} as starter` : 'this season'} />
          <Tile label={`${data.pos} RANK`} value={ls.rank ? `#${ls.rank.rank}` : '—'} sub="in this league" />
        </View>

        {nfl ? (
          <View style={styles.card}>
            <Text style={styles.label}>IN THE NFL · {nfl.games} GAME{nfl.games === 1 ? '' : 'S'}</Text>
            <View style={styles.statRow}>
              {(nfl.tiles ?? []).map((t) => (
                <View key={t.label} style={styles.stat}>
                  <Text style={styles.statValue}>{t.value}</Text>
                  <Text style={styles.statLabel}>{t.label}</Text>
                </View>
              ))}
            </View>
            {nfl.snap_pct != null ? (
              <View style={styles.snapRow}>
                <Text style={styles.statLabel}>Snap share</Text>
                <View style={styles.snapTrack}><View style={[styles.snapFill, { width: `${Math.min(100, nfl.snap_pct)}%` }]} /></View>
                <Text style={styles.snapText}>{nfl.snap_pct}%{nfl.snap_trend === 'up' ? ' ▲' : nfl.snap_trend === 'down' ? ' ▼' : ''}</Text>
              </View>
            ) : null}
          </View>
        ) : null}

        {ls.played || ls.rank ? (
          <View style={styles.card}>
            <Text style={styles.label}>IN LEGACY · {data.season}</Text>
            <View style={styles.statRow}>
              <View style={styles.stat}>
                <Text style={styles.statValue}>{ls.started}/{ls.played}</Text>
                <Text style={styles.statLabel}>weeks started</Text>
              </View>
              <View style={styles.stat}>
                <Text style={styles.statValue} numberOfLines={1} adjustsFontSizeToFit>{ls.rank ? ls.rank.total.toFixed(0) : '—'}</Text>
                <Text style={styles.statLabel}>total points</Text>
              </View>
              <View style={styles.stat}>
                <Text style={styles.statValue}>{ls.per_dollar != null ? ls.per_dollar.toFixed(1) : '—'}</Text>
                <Text style={styles.statLabel}>pts per $1</Text>
              </View>
              <View style={styles.stat}>
                <Text style={styles.statValue}>{ls.best ? ls.best.points.toFixed(1) : '—'}</Text>
                <Text style={styles.statLabel}>{ls.best ? `best · W${ls.best.week}` : 'best week'}</Text>
              </View>
            </View>
          </View>
        ) : null}

        {log.length ? (
          <PointsChart weeks={log} avg={ls.ppg} onPress={() => router.push(`/player-games/${data.player_id}`)} />
        ) : null}

        {data.next_game ? <NextGame game={data.next_game} pos={data.pos} /> : null}

        {history.length ? (
          <View style={styles.card}>
            <View style={styles.cardHead}>
              <Text style={styles.cardTitle}>Contract history</Text>
              <Text style={styles.small}>newest first</Text>
            </View>
            {history.map((e, i) => (
              <Pressable key={`${e.kind}-${e.date}-${i}`} style={styles.event} onPress={e.team_id ? () => router.push(`/team/${e.team_id}`) : undefined}>
                <View style={styles.rail}>
                  <View style={[styles.dot, { backgroundColor: DOT[e.kind] ?? (e.live ? colors.gold : '#4CC38A') }, e.live && { backgroundColor: colors.gold }]} />
                  {i < history.length - 1 ? <View style={styles.railLine} /> : null}
                </View>
                <View style={styles.eventBody}>
                  <View style={styles.eventHead}>
                    <Text style={styles.eventTitle}>{e.title}</Text>
                    {e.tag ? <Text style={[styles.tag, tagStyle(e)]}>{e.tag}</Text> : null}
                  </View>
                  <Text style={styles.small}>{eventSub(e)}</Text>
                </View>
              </Pressable>
            ))}
          </View>
        ) : !c && data.rostered_by ? (
          <Pressable style={styles.card} onPress={data.rostered_by.team_id ? () => router.push(`/team/${data.rostered_by!.team_id}`) : undefined}>
            <Text style={styles.cardTitle}>Contract</Text>
            <Text style={styles.small}>On {owner || 'a'}'s roster in Sleeper · contract not entered yet</Text>
          </Pressable>
        ) : c ? (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>Contract</Text>
            <Text style={styles.small}>{[c.owner_name, `${money(c.salary)} a year through ${c.through}`, c.is_rookie ? 'rookie deal' : null].filter(Boolean).join(' · ')}</Text>
          </View>
        ) : null}

        <View style={styles.card}>
          <Text style={styles.cardTitle}>Health</Text>
          <View style={styles.healthRow}>
            <View style={[styles.healthDot, { backgroundColor: h.healthy ? colors.live : '#E8826F' }]} />
            <Text style={styles.value}>{h.healthy ? 'Healthy' : `${h.status}${h.detail ? ` · ${h.detail}` : ''}`}</Text>
          </View>
          {h.this_week?.practice || h.this_week?.game_status ? (
            <Text style={styles.small}>Week {h.this_week.week}: {[h.this_week.practice, h.this_week.game_status].filter(Boolean).join(' · ')}</Text>
          ) : null}
          {h.estimate ? <Text style={[styles.small, styles.estimate]}>{h.estimate.text}</Text> : null}
          {h.update ? <Text style={styles.small}>{h.update}</Text> : null}
          {h.return ? <Text style={styles.small}>Expected back {h.return}</Text> : null}
          {missed.length ? (
            <Text style={styles.small}>
              Missed: {missed.map((m) => `${m.injury} (${m.season}, ${m.games} game${m.games === 1 ? '' : 's'})`).join(' · ')}
            </Text>
          ) : (
            <Text style={styles.small}>No missed games on file.</Text>
          )}
        </View>

        <Pressable onPress={() => router.navigate({ pathname: '/front-office', params: { q: `${data.is_mine ? 'Should I keep, start or trade' : 'What do you think of'} ${name}?`, at: String(Date.now()) } })} style={styles.ask}>
          <View style={styles.fo}><Text style={styles.foText}>FO</Text></View>
          <Text style={styles.askText}>Ask the Front Office about {name.split(' ').slice(-1)[0]}</Text>
        </Pressable>

        {!data.is_mine && id ? (
          <Pressable onPress={() => toggle(id)} style={[styles.button, isTarget(id) && styles.saved]}>
            <Text style={[styles.buttonText, isTarget(id) && styles.savedText]}>{isTarget(id) ? '★ Saved as a transfer target' : '☆ Save as a transfer target'}</Text>
          </Pressable>
        ) : null}

        {data.is_mine ? (
          <Pressable onPress={() => router.push('/ir')} style={styles.button}>
            <Text style={styles.buttonText}>Manage IR</Text>
          </Pressable>
        ) : null}
      </View>
    </Screen>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <View style={styles.tile}>
      <Text style={styles.label}>{label}</Text>
      <Text style={styles.tileValue} numberOfLines={1}>{value}</Text>
      <Text style={styles.tileSub} numberOfLines={1}>{sub}</Text>
    </View>
  );
}

function NextGame({ game, pos }: { game: NonNullable<PlayerPage['next_game']>; pos: string }) {
  if (game.bye) {
    return (
      <View style={styles.card}>
        <View style={styles.cardHead}><Text style={styles.cardTitle}>Next game</Text><Text style={styles.small}>Week {game.week}</Text></View>
        <Text style={styles.value}>On bye this week</Text>
      </View>
    );
  }
  const when = game.kickoff
    ? new Date(game.kickoff).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' })
    : null;
  const vs = game.vs_pos;
  const tone = vs?.label === 'soft' ? styles.good : vs?.label === 'tough' ? styles.bad : null;
  return (
    <View style={styles.card}>
      <View style={styles.cardHead}><Text style={styles.cardTitle}>Next game</Text><Text style={styles.small}>Week {game.week}</Text></View>
      <View style={styles.gameRow}>
        <Headshot playerId={game.opp} pos="DEF" team={game.opp} size={44} ring="wait" />
        <View style={{ flex: 1 }}>
          <Text style={styles.gameTitle}>{game.home ? 'vs' : 'at'} {game.opp}</Text>
          {when ? <Text style={styles.small}>{when}{game.home ? ' · at home' : ' · away'}</Text> : null}
        </View>
        {game.projected != null ? (
          <View style={styles.projBox}>
            <Text style={styles.projValue}>{game.projected.toFixed(1)}</Text>
            <Text style={styles.factLabel}>PROJECTED</Text>
          </View>
        ) : null}
      </View>
      <View style={styles.facts}>
        {vs ? <Fact label={`VS ${pos}s`} value={vs.text} style={tone} /> : null}
        {game.yards_line ? (
          <Fact label={game.yards_line.label} value={`O/U ${game.yards_line.line}`} />
        ) : game.team_total != null ? <Fact label="TEAM TOTAL" value={`${game.team_total} pts`} /> : null}
        {game.weather ? <Fact label="WEATHER" value={game.weather} /> : null}
      </View>
    </View>
  );
}

function Fact({ label, value, style }: { label: string; value: string; style?: object | null }) {
  return (
    <View style={styles.fact}>
      <Text style={styles.factLabel}>{label}</Text>
      <Text style={[styles.factValue, style]} numberOfLines={1}>{value}</Text>
    </View>
  );
}

const DOT: Record<string, string> = { released: '#E8826F', traded: '#7FA6C9', expired: '#6F8F7E' };

function tagStyle(e: PlayerPage['history'][number]) {
  if (e.live) return styles.tagGold;
  if (e.kind === 'released') return styles.tagRed;
  if (e.kind === 'traded') return styles.tagBlue;
  if (e.tag === 'ROOKIE DEAL') return styles.tagGreen;
  return styles.tagMuted;
}

function eventSub(e: PlayerPage['history'][number]) {
  const money_ = e.salary != null ? `$${e.salary.toFixed(1)}${e.kind === 'released' ? '' : ' a year'}` : null;
  const years = e.years && !e.live && e.kind !== 'released' && e.kind !== 'traded' ? `${e.years} yr${e.years === 1 ? '' : 's'}` : null;
  return [e.owner, e.date, money_, years].filter(Boolean).join(' · ');
}

const styles = StyleSheet.create({
  stack: { gap: 14 },
  hero: { flexDirection: 'row', gap: 14, alignItems: 'center' },
  heroText: { flex: 1, gap: 4 },
  eyebrow: { color: colors.gold, fontSize: 11, fontWeight: '700', letterSpacing: 1 },
  name: { color: colors.text, fontSize: 26, fontWeight: '800', lineHeight: 30 },
  pills: { flexDirection: 'row', gap: 6 },
  pill: { fontSize: 11, fontWeight: '800', borderRadius: 10, overflow: 'hidden', paddingHorizontal: 8, paddingVertical: 3 },
  pillGold: { color: colors.bg, backgroundColor: colors.gold },
  pillLine: { color: colors.muted, borderWidth: 1, borderColor: '#2C5140' },
  pillOk: { color: colors.live, borderWidth: 1, borderColor: '#2F7A57' },
  pillOut: { color: '#E8826F', borderWidth: 1, borderColor: '#7A3F35' },
  tiles: { flexDirection: 'row', gap: 8 },
  tile: { flex: 1, backgroundColor: colors.card, borderRadius: 14, padding: 12 },
  label: { color: colors.muted, fontSize: 10, fontWeight: '700', letterSpacing: 1 },
  estimate: { color: colors.text },
  tileValue: { color: colors.text, fontSize: 22, fontWeight: '800', marginTop: 4 },
  tileSub: { color: colors.muted, fontSize: 11 },
  card: { backgroundColor: colors.card, borderRadius: 16, padding: 16, gap: 10 },
  cardHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' },
  cardTitle: { color: colors.text, fontSize: 15, fontWeight: '700' },
  small: { color: colors.muted, fontSize: 13 },
  value: { color: colors.text, fontSize: 14 },
  statRow: { flexDirection: 'row', gap: 6 },
  stat: { flex: 1 },
  statValue: { color: colors.text, fontSize: 19, fontWeight: '800' },
  statLabel: { color: colors.muted, fontSize: 11 },
  snapRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  snapTrack: { flex: 1, height: 8, borderRadius: 4, backgroundColor: '#1F3F31', overflow: 'hidden' },
  snapFill: { height: 8, backgroundColor: colors.live },
  snapText: { color: colors.text, fontSize: 12, fontWeight: '700' },
  gameRow: { flexDirection: 'row', gap: 12, alignItems: 'center' },
  gameTitle: { color: colors.text, fontSize: 16, fontWeight: '700' },
  projBox: { alignItems: 'flex-end' },
  projValue: { color: colors.gold, fontSize: 24, fontWeight: '800' },
  facts: { flexDirection: 'row', gap: 8 },
  fact: { flex: 1, borderWidth: 1, borderColor: '#2C5140', borderRadius: 10, padding: 8 },
  factLabel: { color: colors.muted, fontSize: 10 },
  factValue: { color: colors.text, fontSize: 14, fontWeight: '700' },
  good: { color: colors.live },
  bad: { color: '#E8826F' },
  event: { flexDirection: 'row', gap: 12 },
  rail: { width: 14, alignItems: 'center' },
  dot: { width: 12, height: 12, borderRadius: 6, marginTop: 3 },
  railLine: { flex: 1, width: 2, backgroundColor: '#2C5140', marginVertical: 3 },
  eventBody: { flex: 1, paddingBottom: 14, gap: 3 },
  eventHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8 },
  eventTitle: { color: colors.text, fontSize: 15, fontWeight: '700', flexShrink: 1 },
  tag: { fontSize: 10, fontWeight: '800', letterSpacing: 0.5, borderRadius: 8, overflow: 'hidden', paddingHorizontal: 7, paddingVertical: 3 },
  tagGold: { color: colors.bg, backgroundColor: colors.gold },
  tagRed: { color: '#E8826F', backgroundColor: '#2A1D18' },
  tagBlue: { color: '#9DC0E0', backgroundColor: '#1A2A38' },
  tagGreen: { color: colors.live, backgroundColor: '#143D2B' },
  tagMuted: { color: colors.muted, backgroundColor: '#1F3F31' },
  healthRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  healthDot: { width: 10, height: 10, borderRadius: 5 },
  ask: { flexDirection: 'row', alignItems: 'center', gap: 10, borderWidth: 1, borderColor: colors.gold, borderRadius: 24, paddingVertical: 12, paddingHorizontal: 16 },
  fo: { width: 22, height: 22, borderRadius: 11, backgroundColor: colors.gold, alignItems: 'center', justifyContent: 'center' },
  foText: { color: colors.bg, fontSize: 11, fontWeight: '800' },
  askText: { color: colors.text, fontSize: 14 },
  button: { borderWidth: 1, borderColor: '#2C5140', borderRadius: 14, padding: 14, alignItems: 'center' },
  buttonText: { color: colors.text, fontWeight: '700', fontSize: 15 },
  saved: { borderColor: colors.gold },
  savedText: { color: colors.gold },
});
