import { LinearGradient } from 'expo-linear-gradient';
import { router, useFocusEffect } from 'expo-router';
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { Alert, StyleSheet, Text, TextInput, View } from 'react-native';
import { Pressable } from '../../components/Pressable';

import { AlertsCard } from '../../components/home/AlertsCard';
import { DraftBoard } from '../../components/home/DraftBoard';
import { ProspectCard } from '../../components/home/ProspectCard';
import { GameDayBottom, Scoreboard } from '../../components/home/GameDayBottom';
import { Avatar, Headshot, type Ring } from '../../components/Pictures';
import { ErrorState, Loading } from '../../components/ScreenState';
import { Bar, Card, Pill, Screen, firstName, money, teamLabel, ordinal, record, ui } from '../../components/ui';
import { type BoxScore, type ByeSide, type HomeData, type HomeSide, type LivePlayer, type HomePlayoffGame, type PlayoffSide, type RaceRow, type RookieDeal, type TableRow, apiSend, LIVE_POLL_MS, prefetch, useApi } from '../../lib/api';
import { syncLiveActivity } from '../../lib/liveActivity';
import { colors } from '../../lib/theme';

// Home changes with the calendar: last week's recap (Tuesday), a preview
// before kickoff, live scores on game days and Monday night's "still to
// play", the bracket in the playoffs, rookie-deal decisions until the June 30
// rollover, then draft prep. Pictures, badges and tables over sentences.
const ROUND = ['', '1st', '2nd', '3rd', '4th', '5th'];
const SHORT = (iso: string) => new Date(iso.length > 10 ? iso : `${iso}T12:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
const KICK = (iso: string | null) => {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.toLocaleDateString(undefined, { weekday: 'short' })} ${d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`;
};
const pts = (n: number) => n.toFixed(1);

export default function Home() {
  const [live, setLive] = useState(false);
  // Game days refresh every 5 seconds while Home is open.
  const { data, error, loading, reload, refresh } = useApi<HomeData>('/home', live ? LIVE_POLL_MS : 0);
  const isLive = data?.phase === 'game_day' || data?.phase === 'playoffs';
  useEffect(() => setLive(isLive), [isLive]);
  // Game days: start or refresh the lock screen Live Activity for my matchup (iPhone only).
  useEffect(() => {
    if (data?.phase === 'game_day' || data?.phase === 'playoffs') syncLiveActivity();
  }, [data]);
  // Once Home is up, warm the other tabs so they open instantly.
  const warmed = useRef(false);
  useEffect(() => {
    if (!data || warmed.current) return;
    warmed.current = true;
    prefetch(['/my-team', '/standings', '/matchups', '/teams', '/league/activity', '/front-office', '/market/players', '/my-team/calls']);
  }, [data]);

  const seen = useRef(false);
  useFocusEffect(useCallback(() => {
    if (seen.current) refresh();
    seen.current = true;
  }, [refresh]));

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  // The day still picks which Home shows (recap, game day...), it just isn't printed.
  const heading = data.phase === 'decisions' || data.phase === 'draft_prep'
    ? `${data.season + (data.phase === 'draft_prep' ? 0 : 1)} offseason`
    : data.phase === 'playoffs' ? `Playoffs · Week ${data.week}` : `Week ${data.week}`;

  return (
    <Screen loading={loading}>
      <View style={styles.top}>
        <View style={ui.flex}>
          <Text style={styles.hey}>{heading}</Text>
        </View>
        <Pressable onPress={() => router.push('/settings')} hitSlop={8}>
          <View style={styles.meBadge}><Text style={styles.meText}>{initials(data.owner_name)}</Text></View>
        </Pressable>
      </View>

      <AlertsCard />
      {data.phase === 'recap' ? <Recap data={data} /> : null}
      {data.phase === 'game_day' ? <GameDay data={data} /> : null}
      {data.phase === 'playoffs' ? <Playoffs data={data} /> : null}
      {(data.phase === 'decisions' || data.phase === 'draft_prep') && data.champion ? <ChampionCard c={data.champion} season={data.season} /> : null}
      {data.phase === 'decisions' ? <Decisions data={data} onChange={reload} /> : null}
      {data.phase === 'draft_prep' ? <DraftPrep data={data} /> : null}
    </Screen>
  );
}

const initials = (name: string) => name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase() || '•';

// ---- pieces ----------------------------------------------------------------

function Eyebrow({ children, right }: { children: string; right?: ReactNode }) {
  return (
    <View style={styles.between}>
      <Text style={styles.cardEyebrow}>{children}</Text>
      {typeof right === 'string' ? <Text style={styles.dimSmall}>{right}</Text> : right ?? null}
    </View>
  );
}

// A third 'proj' entry draws the value smaller, grey and on one line (a projection, not a score).
type Badge = [string, string] | [string, string, 'proj'];

function Badges({ items }: { items: Badge[] }) {
  return (
    <View style={styles.badges}>
      {items.map(([value, label, kind]) => (
        <View key={label} style={styles.badge}>
          <Text style={[styles.badgeValue, kind === 'proj' && styles.badgeProj]} numberOfLines={1} adjustsFontSizeToFit>{value}</Text>
          <Text style={styles.badgeLabel}>{label}</Text>
        </View>
      ))}
    </View>
  );
}

// Both sides' win odds on one small bar under VS: your share in gold on the left, theirs on the right.
function SplitOdds({ odds }: { odds: [number, number] }) {
  const [mine, theirs] = odds;
  return (
    <View style={styles.odds}>
      <View style={styles.oddsNums}>
        <Text style={styles.oddsMine}>{mine}%</Text>
        <Text style={styles.oddsTheirs}>{theirs}%</Text>
      </View>
      <View style={styles.oddsTrack}>
        <View style={[styles.oddsFill, { width: `${Math.max(0, Math.min(100, mine))}%` }]} />
      </View>
      <Text style={styles.oddsLabel}>WIN ODDS</Text>
    </View>
  );
}

// final: the matchup is over, so a gold WINNER tag sits under the winning score (the losing score is already dimmed).
function Hero({ title, pill, me, them, week, matchupId, meLabel, themLabel, badges, showScores = true, proj, odds, final = false, vsSub, note }: {
  title: string; pill?: ReactNode; me: HomeSide; them?: HomeSide | null; week: number; matchupId?: number;
  meLabel?: string; themLabel?: string; badges: Badge[]; showScores?: boolean; proj?: [number, number] | null;
  odds?: [number, number] | null; final?: boolean; vsSub?: string | null; note?: string | null;
}) {
  const total = me.score + (them?.score ?? 0);
  const ahead = me.score >= (them?.score ?? 0);
  const decided = final && showScores && !!them && me.score !== them.score;
  const open = matchupId != null ? () => router.push(`/game/${week}/${matchupId}`) : undefined;
  const Side = ({ s, label, lead, projected }: { s: HomeSide | null | undefined; label: string; lead: boolean; projected?: number }) => (
    <View style={styles.side}>
      <Avatar uri={s?.avatar} name={s?.name ?? 'Bye'} size={54} me={label.startsWith('You')} />
      <Text style={styles.sideName} numberOfLines={1}>{label}</Text>
      {showScores ? <Text style={[styles.score, !lead && styles.dim]}>{pts(s?.score ?? 0)}</Text> : null}
      {showScores && projected != null ? <Text style={styles.proj}>proj {projected.toFixed(1)}</Text> : null}
      {decided ? <Text style={[styles.winner, !lead && styles.hidden]}>WINNER</Text> : null}
    </View>
  );
  return (
    <Pressable onPress={open}>
      <Card gold>
        <Eyebrow right={pill}>{title}</Eyebrow>
        <View style={styles.vs}>
          <Side s={me} label={meLabel ?? 'You'} lead={!total || ahead} projected={proj?.[0]} />
          <View style={styles.vsMid}>
            <Text style={styles.vsText}>VS</Text>
            {vsSub ? <Text style={styles.vsSub} numberOfLines={1}>{vsSub}</Text> : null}
            {odds ? <SplitOdds odds={odds} /> : null}
          </View>
          <Side s={them} label={themLabel ?? (them ? firstName(them.name ?? '') : 'Bye')} lead={!total || !ahead} projected={them ? proj?.[1] : undefined} />
        </View>
        <View style={styles.bar}><Bar fraction={total ? me.score / total : 0.5} /></View>
        {badges.length ? <Badges items={badges} /> : null}
        {note ? <Text style={styles.heroNote}>{note}</Text> : null}
      </Card>
    </Pressable>
  );
}

// The game-week Hero with each side's live projected total (same numbers as the game page), both sides'
// win odds on a split bar under VS, and the projected top-5 line at the bottom (the 5th-best projected
// final score in the league this week; a top-5 week is worth a standings point).
// Each side's projected total and win odds from the game page's box score, mine first (null once final).
function useMatchupProj(data: HomeData) {
  const g = data.game!;
  const final = !!g.is_final;
  const { data: box } = useApi<BoxScore>(`/matchups/${data.week}/${g.matchup_id}`, final ? 0 : LIVE_POLL_MS);
  const sides = box?.teams ?? [];
  const mine = box ? sides.findIndex((t) => t.roster_id === g.me!.roster_id) : -1;
  const proj: [number, number] | null = !final && mine >= 0 && sides.length === 2
    ? [sides[mine].projected ?? sides[mine].score, sides[1 - mine].projected ?? sides[1 - mine].score] : null;
  const odds: [number, number] | null = !final && mine >= 0 && box?.win_chance && g.them
    ? [box.win_chance[mine], box.win_chance[1 - mine]] : null;
  return { proj, odds };
}

// Monday passes its own MNF pill and done flag (every starter on both sides has played) so the card
// looks the same as the weekend one.
function LiveHero({ data, title, pill, done }: { data: HomeData; title: string; pill?: ReactNode; done?: boolean }) {
  const g = data.game!;
  const me = g.me!;
  const final = done ?? !!g.is_final;
  const { proj: rawProj, odds: rawOdds } = useMatchupProj(data);
  const proj = final ? null : rawProj;
  const odds = final ? null : rawOdds;
  const line = data.top5_line;
  return (
    <Hero
      title={title}
      pill={pill ?? <LivePill data={data} />}
      me={me}
      them={g.them}
      week={data.week}
      matchupId={g.matchup_id}
      proj={proj}
      odds={odds}
      final={final}
      badges={line && !final ? [[line.line.toFixed(1), 'PROJ. TOP-5 LINE']] : []}
    />
  );
}

const ring = (p: LivePlayer): Ring => (p.status === 'playing' ? 'live' : p.status === 'final' ? 'done' : 'wait');
const statusLine = (p: LivePlayer) =>
  p.status === 'playing' ? `Playing · ${p.team}` : p.status === 'final' ? 'Final' : p.status === 'bye' ? 'Bye' : KICK(p.kickoff);

function PlayerHeads({ title, right, players, showPoints = true, sub }: {
  title: string; right?: string; players: LivePlayer[]; showPoints?: boolean; sub?: (p: LivePlayer) => string;
}) {
  if (!players.length) return null;
  return (
    <Card>
      <Eyebrow right={right}>{title}</Eyebrow>
      <View style={styles.heads}>
        {players.map((p, i) => (
          <Pressable key={`${p.player_id}-${i}`} style={styles.head} onPress={p.player_id ? () => router.push(`/player/${p.player_id}`) : undefined}>
            <Headshot playerId={p.player_id} pos={p.pos} team={p.team} ring={showPoints ? ring(p) : 'gold'} />
            <Text style={styles.headName} numberOfLines={1}>{shortName(p.name)}</Text>
            {showPoints ? <Text style={styles.headPts}>{p.status === 'waiting' ? '—' : pts(p.points)}</Text> : null}
            {sub ? <Text style={styles.headSub} numberOfLines={1}>{sub(p)}</Text> : null}
          </Pressable>
        ))}
      </View>
    </Card>
  );
}

const shortName = (name: string) => {
  const parts = name.split(' ');
  return parts.length > 1 ? `${parts[0][0]}. ${parts.slice(1).join(' ')}` : name;
};

function AskBar({ placeholder }: { placeholder: string }) {
  const [draft, setDraft] = useState('');
  const ask = (q: string) => {
    setDraft('');
    router.navigate({ pathname: '/front-office', params: q.trim() ? { q: q.trim(), at: String(Date.now()) } : {} });
  };
  return (
    <View style={styles.ask}>
      <Pressable onPress={() => router.navigate('/front-office')} style={styles.gm}><Text style={styles.gmText}>GM</Text></Pressable>
      <TextInput
        value={draft}
        onChangeText={setDraft}
        placeholder={placeholder}
        placeholderTextColor={colors.muted}
        style={styles.askInput}
        returnKeyType="send"
        onSubmitEditing={() => ask(draft || placeholder.replace(/…$/, ''))}
      />
      <Pressable onPress={() => ask(draft || placeholder.replace(/…$/, ''))} style={styles.go} hitSlop={6}><Text style={styles.goText}>↑</Text></Pressable>
    </View>
  );
}

function Table({ title, right, onRight, onPress, head, rows, cutAfter }: {
  title: string; right?: string; onRight?: () => void; onPress?: () => void; head: [string, string];
  rows: { key: string; rank: number; name: string; avatar: string | null; me: boolean; a: string; b: string; teamId?: string | null }[];
  cutAfter?: number;
}) {
  const body = (
    <Card>
      <Eyebrow right={right ? <Pressable onPress={onRight} hitSlop={8}><Text style={ui.link}>{right}</Text></Pressable> : undefined}>{title}</Eyebrow>
      <View style={styles.tHead}>
        <Text style={[styles.th, styles.tOwner]}>OWNER</Text>
        <Text style={styles.th}>{head[0]}</Text>
        <Text style={styles.th}>{head[1]}</Text>
      </View>
      {rows.map((r, i) => (
        <Pressable
          key={r.key}
          onPress={onPress ?? (r.teamId ? () => router.push(`/team/${r.teamId}`) : undefined)}
          style={[styles.tRow, i > 0 && cutAfter != null && rows[i - 1].rank <= cutAfter && r.rank > cutAfter && styles.tCut]}
        >
          <Text style={[styles.tRank, r.me && ui.gold]}>{r.rank}</Text>
          <View style={[styles.tOwner, styles.tOwnerCell]}>
            <Avatar uri={r.avatar} name={r.name} size={24} me={r.me} />
            <Text style={[styles.tName, r.me && ui.gold]} numberOfLines={1}>{r.me ? 'You' : teamLabel(r, true)}</Text>
          </View>
          <Text style={[styles.td, r.me && ui.gold]}>{r.a}</Text>
          <Text style={[styles.td, r.me && ui.gold]}>{r.b}</Text>
        </Pressable>
      ))}
    </Card>
  );
  // A table with onPress opens its full page from anywhere inside the card.
  return onPress ? <Pressable onPress={onPress}>{body}</Pressable> : body;
}

const openStandings = () => router.navigate({ pathname: '/league', params: { at: String(Date.now()) } });

function Standings({ rows, playoffTeams }: { rows: TableRow[]; playoffTeams: number }) {
  return (
    <Table
      title="STANDINGS"
      right="All ›"
      onRight={openStandings}
      onPress={openStandings}
      head={['W-L', 'SP']}
      cutAfter={playoffTeams}
      rows={rows.map((r) => ({
        key: String(r.roster_id), rank: r.rank, name: r.name, avatar: r.avatar, me: r.is_me, teamId: r.team_id,
        a: record(r.wins, r.losses, r.ties), b: String(r.standing_points),
      }))}
    />
  );
}

function Race({ rows, title, window }: { rows: RaceRow[]; title: string; window: 'top' | 'bubble' }) {
  if (!rows.length) return null;
  let shown = rows.slice(0, 6);
  if (window === 'bubble') {
    const me = rows.find((r) => r.is_me);
    const lo = Math.max(0, Math.min(3, (me?.rank ?? 5) - 2));
    shown = rows.slice(lo, lo + 4);
  }
  const me = rows.find((r) => r.is_me);
  if (me && !shown.includes(me)) shown = [...shown.slice(0, shown.length - 1), me];
  return (
    <Table
      title={title}
      right="Scores ›"
      onRight={() => router.navigate('/league')}
      head={['PTS', 'T5']}
      cutAfter={5}
      rows={shown.map((r) => ({ key: String(r.roster_id), rank: r.rank, name: r.name, avatar: r.avatar, me: r.is_me, a: pts(r.score), b: r.top5 ? '✓' : '' }))}
    />
  );
}

function UpNext({ them, week, series }: { them: HomeSide; week: number; series?: HomeData['series'] }) {
  return (
    <Pressable onPress={them.team_id ? () => router.push(`/team/${them.team_id}`) : undefined}>
      <Card>
        <Eyebrow>{`UP NEXT · WEEK ${week}`}</Eyebrow>
        <View style={styles.upNext}>
          <Avatar uri={them.avatar} name={them.name} size={40} />
          <View style={ui.flex}>
            <Text style={ui.name}>{teamLabel(them)}</Text>
            <Text style={ui.sub}>
              {record(them.wins ?? 0, them.losses ?? 0, them.ties ?? 0)} · {ordinal(them.season_rank)}{them.ppg != null ? ` · ${them.ppg.toFixed(1)} PPG` : ''}
            </Text>
          </View>
          {series ? <Text style={styles.dimSmall}>{seriesText(series)}</Text> : null}
        </View>
      </Card>
    </Pressable>
  );
}

const seriesText = (s: NonNullable<HomeData['series']>) =>
  s.wins > s.losses ? `You lead ${record(s.wins, s.losses, s.ties)}` : s.wins < s.losses ? `They lead ${record(s.losses, s.wins, s.ties)}` : `Series ${record(s.wins, s.losses, s.ties)}`;

const left = (players: LivePlayer[]) => players.filter((p) => p.status === 'waiting' || p.status === 'playing').length;
const anyPlaying = (data: HomeData) => [...(data.players?.me ?? []), ...(data.players?.them ?? [])].some((p) => p.status === 'playing');

function LivePill({ data, label = 'LIVE', waiting }: { data: HomeData; label?: string; waiting?: string }) {
  if (data.game?.is_final) return <Pill label="FINAL" tone="gold" />;
  if (anyPlaying(data)) return <Pill label={`● ${label}`} tone="live" />;
  return waiting ? <Pill label={waiting} tone="gold" /> : null;
}

// ---- phases ----------------------------------------------------------------

function Recap({ data }: { data: HomeData }) {
  const r = data.recap;
  const next = data.game?.them;
  return (
    <>
      <RecapHero data={data} />
      <PlayerHeads title="YOUR TOP SCORERS" right={`Week ${r?.week ?? data.week - 1}`} players={r?.top_scorers ?? []} />
      <AskBar placeholder={`Ask your GM about week ${data.week}…`} />
      {data.standings?.length ? <Standings rows={data.standings} playoffTeams={data.playoff_teams} /> : null}
      {next ? <UpNext them={next} week={data.week} series={data.series} /> : null}
    </>
  );
}

// Last week's final, on Tuesdays. After a playoff week it's that playoff game instead (see PlayoffRecapHero).
function RecapHero({ data }: { data: HomeData }) {
  const r = data.recap;
  const game = r?.game;
  if (!r || !game?.me) return null;
  if (r.playoff_game) return <PlayoffRecapHero data={data} pg={r.playoff_game} week={r.week} weekScore={game.me.score} rank={game.me.rank} />;
  const won = game.me.score > (game.them?.score ?? 0);
  return (
    <Hero
      title={`WEEK ${r.week} FINAL${game.them ? ` · VS ${firstName(game.them.name ?? '').toUpperCase()}` : ''}`}
      pill={<Pill label={won ? 'WIN' : game.me.score === game.them?.score ? 'TIE' : 'LOSS'} tone={won ? 'live' : 'out'} />}
      me={game.me}
      them={game.them}
      week={r.week}
      matchupId={game.matchup_id}
      badges={[[ordinal(game.me.rank), 'HIGH SCORE'], [game.me.top5 ? '✓' : '✗', 'TOP 5'], [`+${r.standing_points}`, 'STANDING PTS']]}
    />
  );
}

function GameDay({ data }: { data: HomeData }) {
  const g = data.game;
  if (!g?.me) {
    return (
      <>
        <Card gold><Eyebrow>{`WEEK ${data.week}`}</Eyebrow><Text style={[ui.sub, styles.gap]}>No game this week.</Text></Card>
        <AskBar placeholder="Ask your GM…" />
        {data.bottom ? <GameDayBottom bottom={data.bottom} week={data.week} />
          : data.standings?.length ? <Standings rows={data.standings} playoffTeams={data.playoff_teams} /> : null}
      </>
    );
  }
  const mine = data.players?.me ?? [];
  const theirs = data.players?.them ?? [];
  const started = g.me.score > 0 || (g.them?.score ?? 0) > 0 || mine.some((p) => p.status !== 'waiting' && p.status !== 'empty' && p.status !== 'bye');
  const title = `WEEK ${data.week}${g.them ? ` · VS ${firstName(g.them.name ?? '').toUpperCase()}` : ''}`;

  if (!started) {
    const first = mine.filter((p) => p.kickoff).sort((a, b) => (a.kickoff! < b.kickoff! ? -1 : 1));
    return (
      <>
        <PreGameHero data={data} title={title} />
        <PlayerHeads title="YOUR STARTERS · FIRST UP" players={first.slice(0, 3)} showPoints={false} sub={(p) => KICK(p.kickoff)} />
        <LineupCheck issues={data.lineup_issues ?? []} />
        <AskBar placeholder="Who should I start this week?" />
        {data.bottom ? <GameDayBottom bottom={data.bottom} week={data.week} />
          : data.standings?.length ? <Standings rows={data.standings} playoffTeams={data.playoff_teams} /> : null}
      </>
    );
  }

  if (data.weekday === 0 && !g.is_final) {
    // Every starter on both sides has played (the week itself may still have MNF games left in other matchups).
    const done = mine.length > 0 && left(mine) === 0 && left(theirs) === 0;
    return (
      <>
        <LiveHero
          data={data}
          title={title}
          pill={done ? <Pill label="FINAL" tone="gold" /> : <LivePill data={data} label="MNF" waiting="MNF TONIGHT" />}
          done={done}
        />
        {data.bottom?.mode === 'shotgun' && data.bottom.scoreboard?.length ? <Scoreboard games={data.bottom.scoreboard} week={data.week} /> : null}
        {data.bottom ? <GameDayBottom bottom={data.bottom} week={data.week} /> : <Race rows={data.race ?? []} title="TOP 5 RACE" window="bubble" />}
        <AskBar placeholder="What do I need tonight?" />
      </>
    );
  }

  const now = [...mine].filter((p) => p.player_id && p.status !== 'bye')
    .sort((a, b) => order(a) - order(b) || b.points - a.points).slice(0, 3);
  return (
    <>
      <LiveHero data={data} title={title} />
      {data.bottom?.mode === 'shotgun' && data.bottom.scoreboard?.length
        ? <Scoreboard games={data.bottom.scoreboard} week={data.week} />
        : <PlayerHeads title="YOUR PLAYERS RIGHT NOW" right={anyPlaying(data) ? 'Live' : undefined} players={now} sub={statusLine} />}
      {data.bottom ? <GameDayBottom bottom={data.bottom} week={data.week} />
        : <Race rows={data.race ?? []} title={g.is_final ? 'TOP 5 · FINAL' : 'TOP 5 RACE · LIVE'} window="top" />}
      <AskBar placeholder="How is my week going?" />
    </>
  );
}

// Before kickoff: your week score on the left, theirs on the right, the projected top-5 line in the middle
// (the 5th-best projected total in the league; a top-5 week is worth a standings point) and both projected
// totals in small print under VS.
function PreGameHero({ data, title }: { data: HomeData; title: string }) {
  const g = data.game!;
  const { proj } = useMatchupProj(data);
  const line = data.top5_line;
  const them = g.them ? firstName(g.them.name).toUpperCase() : 'BYE';
  return (
    <Hero
      title={title}
      me={g.me!}
      them={g.them}
      week={data.week}
      matchupId={g.matchup_id}
      showScores={false}
      vsSub={proj ? `${proj[0].toFixed(1)} – ${proj[1].toFixed(1)}` : null}
      badges={[
        [pts(g.me!.score), 'YOU'],
        [line && !line.final ? line.line.toFixed(1) : '—', 'PROJ TOP 5'],
        [g.them ? pts(g.them.score) : '—', them],
      ]}
    />
  );
}

const order = (p: LivePlayer) => (p.status === 'playing' ? 0 : p.status === 'final' ? 1 : 2);

function LineupCheck({ issues }: { issues: LivePlayer[] }) {
  return (
    <Card>
      <Eyebrow right={issues.length ? <Pill label={`${issues.length} ISSUE${issues.length > 1 ? 'S' : ''}`} tone="out" /> : <Pill label="ALL SET" tone="live" />}>
        LINEUP CHECK
      </Eyebrow>
      {issues.length === 0 ? <Text style={[ui.sub, styles.gap]}>Every starter is healthy and has a game.</Text> : null}
      {issues.map((p, i) => (
        <Pressable
          key={`${p.player_id}-${i}`}
          style={styles.issue}
          onPress={p.player_id ? () => router.push({ pathname: '/player/[id]', params: { id: p.player_id!, section: 'health' } }) : undefined}
        >
          <Headshot playerId={p.player_id} pos={p.pos} team={p.team} size={44} ring="red" />
          <View style={ui.flex}>
            <Text style={ui.name}>{p.player_id ? shortName(p.name) : p.name} <Text style={styles.dimSmall}>{p.pos}</Text></Text>
            <Text style={styles.issueText}>{p.issue} · in your lineup</Text>
          </View>
          {p.player_id ? <Text style={styles.chev}>›</Text> : null}
        </Pressable>
      ))}
      {issues.length ? <Text style={[ui.sub, styles.gap]}>Set your lineup in Sleeper.</Text> : null}
    </Card>
  );
}

function Playoffs({ data }: { data: HomeData }) {
  const mine = data.players?.me ?? [];
  const pg = data.playoff_game;
  const tuesday = data.weekday === 1 && !!data.recap?.game?.me;
  return (
    <>
      {tuesday ? <RecapHero data={data} /> : pg ? <PlayoffHero data={data} pg={pg} /> : data.playoff_bye ? <ByeHero data={data} /> : null}
      {data.bracket?.length ? <BracketCard rounds={data.bracket} playoffTeams={data.playoff_teams} /> : null}
      {data.champion ? <ChampionCard c={data.champion} season={data.season} /> : tuesday
        ? <PlayerHeads title="YOUR TOP SCORERS" right={`Week ${data.recap!.week}`} players={data.recap!.top_scorers ?? []} />
        : <PlayerHeads title="YOUR PLAYERS RIGHT NOW" players={mine.filter((p) => p.player_id).sort((a, b) => order(a) - order(b) || b.points - a.points).slice(0, 3)} sub={statusLine} />}
      {/* Shotgun watch stays live all through the playoffs: every team still scores and last place still shotguns. */}
      {data.draft_board ? <DraftBoard board={data.draft_board} /> : null}
      {/* Prospect Power Rankings come out Tuesday; the card shows Tuesday through Saturday, off the game days. */}
      {data.weekday != null && data.weekday >= 1 && data.weekday <= 5 ? <ProspectCard /> : null}
      {data.bottom ? <GameDayBottom bottom={data.bottom} week={data.week} /> : null}
      <AskBar placeholder="Who should I start this week?" />
      {!pg && data.standings?.length ? <Standings rows={data.standings} playoffTeams={data.playoff_teams} /> : null}
    </>
  );
}

const asSide = (s: PlayoffSide, score?: number): HomeSide => ({ roster_id: s.roster_id, name: s.name, score: score ?? s.score, avatar: s.avatar });
const signed = (n: number) => `${n >= 0 ? '+' : '−'}${Math.abs(n).toFixed(1)}`;
// What the next game is called, short enough for a badge.
const nextLabel = (label?: string | null) =>
  !label ? '—' : label === 'Championship' ? 'Title game' : label.startsWith('Consolation') ? 'Consolation' : label;

// My playoff game, drawn exactly like the regular-season card for the same day (Tommy, 2026-11-22):
// before kickoff the projections under VS and YOU / PROJ TOP 5 / THEM; once games start each side's score and
// projection, win odds under VS and the projected top-5 line; once over, FINAL and WINNER.
// Scores are round totals; in week 2 of a two-week round the projections and win odds count week 1 too.
// The box score (projections, win odds, round totals) when the game has a matchup; without one the card still draws.
function WithBox({ path, every, children }: { path: string; every: number; children: (box: BoxScore | null) => ReactNode }) {
  const { data } = useApi<BoxScore>(path, every);
  return <>{children(data)}</>;
}

function PlayoffHero({ data, pg }: { data: HomeData; pg: HomePlayoffGame }) {
  const carry = pg.leg > 1 && pg.them ? `?carry=${pg.me.roster_id}:${pg.me.carry},${pg.them.roster_id}:${pg.them.carry}` : '';
  if (pg.matchup_id == null) return <PlayoffHeroCard data={data} pg={pg} box={null} carry={!!carry} />;
  return (
    <WithBox path={`/matchups/${data.week}/${pg.matchup_id}${carry}`} every={pg.state === 'final' ? 0 : LIVE_POLL_MS}>
      {(box) => <PlayoffHeroCard data={data} pg={pg} box={box} carry={!!carry} />}
    </WithBox>
  );
}

function PlayoffHeroCard({ data, pg, box, carry }: { data: HomeData; pg: HomePlayoffGame; box: BoxScore | null; carry: boolean }) {
  const final = pg.state === 'final', pre = pg.state === 'pre';
  const mid = pg.matchup_id;
  const sides = box?.teams ?? [];
  const i = box && sides.length === 2 ? sides.findIndex((t) => t.roster_id === pg.me.roster_id) : -1;
  const pick = <T,>(pair?: [T, T] | null): [T, T] | null => (pair && i >= 0 ? [pair[i], pair[1 - i]] : null);
  const round = carry ? box?.round : null;
  const score = round ? pick(round.score) : null;
  const proj = final ? null : round ? pick(round.projected)
    : i >= 0 ? [sides[i].projected ?? sides[i].score, sides[1 - i].projected ?? sides[1 - i].score] as [number, number] : null;
  const odds = final || pre || !pg.them ? null : pick(round ? round.win_chance : box?.win_chance);
  const line = data.top5_line;
  const them = pg.them ? firstName(pg.them.name ?? '') : null;
  const me = asSide(pg.me, score?.[0]);
  const badges: Badge[] = pre
    ? [[pts(me.score), 'YOU'], [line && !line.final ? line.line.toFixed(1) : '—', 'PROJ TOP 5'], [pg.them ? pts(asSide(pg.them, score?.[1]).score) : '—', them ? them.toUpperCase() : 'BYE']]
    : !final && line && !line.final ? [[line.line.toFixed(1), 'PROJ. TOP-5 LINE']] : [];
  const note = pg.legs > 1 ? (final ? 'Final · round total, both weeks' : `Week ${pg.leg} of ${pg.legs} · round total`) : null;
  return (
    <Hero
      title={`WEEK ${data.week}${them ? ` · VS ${them.toUpperCase()}` : ''}`}
      pill={<LivePill data={data} />}
      me={me}
      them={pg.them ? asSide(pg.them, score?.[1]) : null}
      week={data.week}
      matchupId={mid ?? undefined}
      showScores={!pre}
      vsSub={pre && proj ? `${proj[0].toFixed(1)} – ${proj[1].toFixed(1)}` : null}
      proj={pre ? null : proj}
      odds={odds}
      final={final}
      badges={badges}
      note={note}
    />
  );
}

// Tuesday after a playoff week: that game's final (or the score so far after week 1 of a two-week round), with
// HIGH SCORE / VS PROJ / UP NEXT, or HIGH SCORE / ROUND LEAD / VS PROJ halfway through a two-week round.
// VS PROJ = last week's score against my starters' projections frozen at kickoff (from the box score).
function PlayoffRecapHero(props: { data: HomeData; pg: HomePlayoffGame; week: number; weekScore: number; rank?: number }) {
  const mid = props.pg.matchup_id;
  if (mid == null) return <PlayoffRecapCard {...props} box={null} />;
  return <WithBox path={`/matchups/${props.week}/${mid}`} every={0}>{(box) => <PlayoffRecapCard {...props} box={box} />}</WithBox>;
}

function PlayoffRecapCard({ data, pg, week, weekScore, rank, box }: {
  data: HomeData; pg: HomePlayoffGame; week: number; weekScore: number; rank?: number; box: BoxScore | null;
}) {
  const midRound = pg.state !== 'final';
  const mid = pg.matchup_id;
  const mine = box?.teams.find((t) => t.roster_id === pg.me.roster_id);
  const kick = mine ? mine.players.reduce((sum, p) => sum + (p.proj ?? 0), 0) : null;
  const vsProj = kick != null && kick > 0 ? signed(weekScore - kick) : '—';
  const them = pg.them ? firstName(pg.them.name ?? '') : null;
  const lead = pg.me.score - (pg.them?.score ?? 0);
  const won = pg.them ? lead > 0 : true;
  const pill = midRound
    ? <Pill label={lead >= 0 ? 'LEADING' : 'TRAILING'} tone={lead >= 0 ? 'live' : 'out'} />
    : <Pill label={won ? 'WIN' : lead === 0 ? 'TIE' : 'LOSS'} tone={won ? 'live' : 'out'} />;
  const badges: Badge[] = midRound
    ? [[ordinal(rank), 'HIGH SCORE'], [signed(lead), lead >= 0 ? 'ROUND LEAD' : 'ROUND DEFICIT'], [vsProj, 'VS PROJ']]
    : [[ordinal(rank), 'HIGH SCORE'], [vsProj, 'VS PROJ'], [nextLabel(data.playoff_game?.label), 'UP NEXT']];
  return (
    <Hero
      title={`WEEK ${week}${midRound ? '' : ' FINAL'}${them ? ` · VS ${them.toUpperCase()}` : ''}`}
      pill={pill}
      me={asSide(pg.me)}
      them={pg.them ? asSide(pg.them) : null}
      week={week}
      matchupId={mid ?? undefined}
      badges={badges}
      note={pg.legs > 1 ? (midRound ? `Week ${pg.leg} of ${pg.legs} · round total` : 'Final · round total, both weeks') : null}
    />
  );
}

// A consolation bye week (Tommy, 2026-11-22): styled like the matchup card, both bye teams' scores and projections
// (they still count for Shotgun Watch and the top 5), NO WINNER under VS, a nudge to set a lineup and the next game.
function ByeHero({ data }: { data: HomeData }) {
  const b = data.playoff_bye!;
  const pre = b.me.score === 0 && (b.them?.score ?? 0) === 0;
  const side = (s: ByeSide | null, label: string) => (
    <View style={styles.side}>
      <Avatar uri={s?.avatar} name={s?.name ?? 'Bye'} size={54} me={label === 'You'} />
      <Text style={styles.sideName} numberOfLines={1}>{label}</Text>
      {!pre && s ? <Text style={styles.score}>{pts(s.score)}</Text> : null}
      {!pre && s?.proj != null ? <Text style={styles.proj}>proj {s.proj.toFixed(1)}</Text> : null}
    </View>
  );
  const them = b.them ? firstName(b.them.name) : 'Bye';
  const next = b.next_week ? (b.next_vs ? `Week ${b.next_week}: you play ${firstName(b.next_vs)}`
    : b.feeder?.names.length === 2 ? `Week ${b.next_week}: you play the ${b.feeder.names.map(firstName).join('–')} ${b.feeder.kind === 'w' ? 'winner' : 'loser'}` : '') : '';
  return (
    <Card gold>
      <Eyebrow right={<LivePill data={data} />}>{`BYE WEEK · WITH ${them.toUpperCase()}`}</Eyebrow>
      <View style={styles.vs}>
        {side(b.me, 'You')}
        <View style={styles.vsMid}>
          <Text style={styles.vsText}>VS</Text>
          {pre && b.me.proj != null ? <Text style={styles.vsSub}>{`${b.me.proj.toFixed(1)} – ${(b.them?.proj ?? 0).toFixed(1)}`}</Text> : null}
          <Text style={styles.byeTag}>NO WINNER</Text>
        </View>
        {side(b.them, them)}
      </View>
      <Text style={styles.heroNote}>{pre ? 'Set your lineup: your score still counts for Shotgun Watch' : 'Your score counts for Shotgun Watch'}{next ? `\n${next}` : ''}</Text>
    </Card>
  );
}

// The champion (Tommy picked the trophy banner, 2026-11-22): replaces "Your players" once the title game is final
// and stays on Home for 10 days.
function ChampionCard({ c, season }: { c: NonNullable<HomeData['champion']>; season: number }) {
  const line = c.opponent ? `${pts(c.score)} – ${pts(c.opponent.score)} over ${firstName(c.opponent.name)}` : '';
  return (
    <Card gold style={styles.champ}>
      <LinearGradient colors={['rgba(200,155,74,0.35)', 'rgba(200,155,74,0)']} style={StyleSheet.absoluteFill} pointerEvents="none" />
      <Text style={[styles.cardEyebrow, styles.champEyebrow]}>{`LEGACY DYNASTY · ${season} CHAMPION`}</Text>
      <Text style={styles.champCup}>🏆</Text>
      <Text style={styles.champName}>{c.is_me ? 'You' : firstName(c.name)}</Text>
      {c.team_name ? <Text style={styles.champTeam}>{c.team_name}</Text> : null}
      {line ? <Text style={styles.champLine}>{line}</Text> : null}
    </Card>
  );
}

type BracketRound = NonNullable<HomeData['bracket']>[number];
const weeksLabel = (r: BracketRound) => {
  const w = r.weeks?.length ? r.weeks : [r.week];
  return `WK ${w[0]}${w.length > 1 ? `-${w[w.length - 1]}` : ''}`;
};

function MatchBox({ game, gold }: { game: BracketRound['games'][number]; gold?: boolean }) {
  return (
    <View style={[styles.match, gold && styles.matchGold]}>
      {[game.a, game.b].map((s, j) => {
        const other = j === 0 ? game.b : game.a;
        const leads = !!s && (game.winner ? game.winner === s.roster_id : (s.score ?? 0) > (other?.score ?? 0));
        const out = !!s && !!game.winner && game.winner !== s.roster_id;
        if (!s) return <Text key={j} style={styles.matchTbd} numberOfLines={1} adjustsFontSizeToFit>{game.placeholders?.[j] ?? 'TBD'}</Text>;
        return (
          <View key={j} style={[styles.matchRow, out && styles.matchOut]}>
            <Avatar uri={s.avatar} name={s.name} size={18} />
            <Text style={[styles.matchName, leads && ui.gold]} numberOfLines={1}>{teamLabel(s, true)}</Text>
            <Text style={[styles.matchScore, leads && ui.gold]}>{s.score ? pts(s.score) : ''}</Text>
          </View>
        );
      })}
    </View>
  );
}

function SemiCol({ round, k }: { round: BracketRound; k: number }) {
  return (
    <View style={styles.bkSide}>
      <Text style={styles.roundLabel}>{round.label.toUpperCase()}</Text>
      <MatchBox game={round.games[k]} />
      <Text style={styles.bkWeeks}>{weeksLabel(round)}</Text>
    </View>
  );
}

// A real bracket: semifinal on each side, the final in the middle with lines running in from both semis.
// Other shapes (6+ teams with a quarterfinal round) keep the column-per-round layout.
function BracketCard({ rounds, playoffTeams }: { rounds: BracketRound[]; playoffTeams?: number }) {
  const last = rounds[rounds.length - 1];
  const semis = rounds[rounds.length - 2];
  const classic = rounds.length === 2 && semis.games.length === 2 && last.games.length === 1;
  // Home shows only the top bracket; tapping it opens this week's Matchups with both brackets.
  return (
    <Pressable onPress={() => router.navigate({ pathname: '/league', params: { tab: 'Matchups', at: String(Date.now()) } })}>
    <Card>
      <Eyebrow right={playoffTeams ? `Top ${playoffTeams}` : undefined}>BRACKET</Eyebrow>
      {classic ? (
        <View style={styles.bk}>
          <SemiCol round={semis} k={0} />
          <View style={styles.bkLine} />
          <View style={styles.bkMid}>
            <Text style={[styles.roundLabel, ui.gold]}>{last.label.toUpperCase()}</Text>
            <MatchBox game={last.games[0]} gold />
            <Text style={styles.bkWeeks}>{weeksLabel(last)}</Text>
          </View>
          <View style={styles.bkLine} />
          <SemiCol round={semis} k={1} />
        </View>
      ) : (
        <View style={styles.bracket}>
          {rounds.map((rnd) => (
            <View key={rnd.round} style={styles.roundCol}>
              <Text style={styles.roundLabel}>{rnd.label.toUpperCase()} · {weeksLabel(rnd)}</Text>
              {rnd.games.map((game, i) => <MatchBox key={i} game={game} gold={rnd === last} />)}
            </View>
          ))}
        </View>
      )}
    </Card>
    </Pressable>
  );
}

function Decisions({ data, onChange }: { data: HomeData; onChange: () => void }) {
  const d = data.decisions!;
  const [busy, setBusy] = useState<string | null>(null);
  const daysLeft = Math.max(0, Math.round((new Date(`${d.deadline.slice(0, 10)}T12:00:00`).getTime() - new Date(`${data.today}T12:00:00`).getTime()) / 86_400_000));
  const decided = d.players.filter((p) => p.choice).length;
  const next = data.me.cap_by_year?.[1];

  const choose = (p: RookieDeal, choice: 'keep' | 'walk') => {
    if (!p.decision_id) return;
    const go = async () => {
      setBusy(p.player_id);
      try {
        await apiSend('POST', `/home/decisions/${p.decision_id}`, { choice });
        onChange();
      } catch (e: any) {
        Alert.alert('Not saved', e?.message ?? 'Try again.');
      } finally {
        setBusy(null);
      }
    };
    Alert.alert(
      choice === 'keep' ? `Keep ${p.name}?` : `Let ${p.name} walk?`,
      choice === 'keep' ? 'He stays on your team on a new deal.' : 'He goes to free agency at the rollover.',
      [{ text: 'Cancel', style: 'cancel' }, { text: choice === 'keep' ? 'Keep' : 'Let walk', onPress: go }],
    );
  };

  return (
    <>
      <Card gold>
        <Eyebrow right={<Pill label={`BY ${SHORT(d.deadline).toUpperCase()}`} tone="gold" />}>ROLLOVER · KEEP OR WALK</Eyebrow>
        <Badges items={[[String(daysLeft), 'DAYS LEFT'], [`${decided}/${d.players.length}`, 'DECIDED'], [next ? money(next.space) : '—', `${next?.season ?? ''} SPACE`.trim()]]} />
        {!d.window_open ? <Text style={[ui.sub, styles.gap]}>Choices open when the commissioner starts the rollover. Anything you skip walks.</Text> : null}
      </Card>

      <Card>
        <Eyebrow>YOUR ROOKIE DEALS ENDING</Eyebrow>
        {d.players.length === 0 ? <Text style={[ui.sub, styles.gap]}>No rookie deals end this year.</Text> : null}
        {d.players.map((p, i) => (
          <View key={p.player_id} style={[styles.deal, i > 0 && styles.dealLine]}>
            <Headshot playerId={p.player_id} pos={p.pos} size={44} />
            <View style={ui.flex}>
              <Text style={ui.name}>{shortName(p.name)} <Text style={styles.dimSmall}>{p.pos}</Text></Text>
              <Text style={ui.sub}>{money(p.salary)} now{p.ppg != null ? ` · ${p.ppg.toFixed(1)} PPG` : ''}</Text>
            </View>
            {(['keep', 'walk'] as const).map((c) => (
              <Pressable
                key={c}
                disabled={!p.open || busy === p.player_id}
                onPress={() => choose(p, c)}
                style={[styles.choice, p.choice === c && styles.choiceOn, !p.open && !p.choice && styles.choiceOff]}
              >
                <Text style={[styles.choiceText, p.choice === c && ui.gold]}>{c === 'keep' ? 'Keep' : 'Walk'}</Text>
              </Pressable>
            ))}
          </View>
        ))}
      </Card>

      {next ? (
        <Card>
          <Eyebrow right={<Pressable onPress={() => router.push('/league-cap')} hitSlop={8}><Text style={ui.link}>League cap ›</Text></Pressable>}>
            {`${next.season} CAP`}
          </Eyebrow>
          <CapBar used={next.used} space={next.space} cap={data.salary_cap} />
        </Card>
      ) : null}

      <AskBar placeholder="Who should I keep?" />
    </>
  );
}

function CapBar({ used, space, cap }: { used: number; space: number; cap: number }) {
  const total = Math.max(cap, used + Math.max(space, 0)) || 1;
  return (
    <>
      <View style={styles.capBar}>
        <View style={{ flex: Math.max(used, 0) / total, backgroundColor: colors.green }} />
        <View style={{ flex: Math.max(space, 0) / total, backgroundColor: '#24392E' }} />
      </View>
      <View style={styles.legend}>
        <Text style={styles.legendText}><Text style={{ color: colors.green }}>■ </Text>Signed {money(used)}</Text>
        <Text style={styles.legendText}><Text style={{ color: '#4A6B5B' }}>■ </Text>Space {money(space)}</Text>
      </View>
    </>
  );
}

function DraftPrep({ data }: { data: HomeData }) {
  const space = data.me.cap_by_year?.[0]?.space ?? data.me.cap_space;
  const count = data.roster_count ?? 0;
  const max = data.roster_max ?? 22;
  return (
    <>
      <Card gold>
        <Eyebrow right={<Pill label="ON THE WEBSITE" tone="gold" />}>{`${data.picks?.year ?? ''} ROOKIE DRAFT`.trim()}</Eyebrow>
        <View style={styles.picks}>
          {data.picks?.picks.length ? data.picks.picks.map((p) => (
            <View key={`${p.round}-${p.original_owner}`} style={styles.pick}>
              <Text style={styles.pickRound}>{ROUND[p.round] ?? `R${p.round}`}</Text>
              <Text style={styles.pickSub} numberOfLines={1}>{p.own ? 'Yours' : `from ${firstName(p.original_owner)}`}</Text>
            </View>
          )) : <Text style={ui.sub}>No picks in the next draft.</Text>}
        </View>
        <Badges items={[[money(space), 'CAP SPACE'], [`${count}/${max}`, 'ROSTER'], [String(Math.max(0, max - count)), 'OPEN SPOTS']]} />
      </Card>
      {data.who_has_money?.length ? (
        <Table
          title="WHO HAS MONEY"
          right={`${data.season} space`}
          head={['SPACE', 'OPEN']}
          rows={data.who_has_money.map((m, i) => ({
            key: m.name, rank: i + 1, name: m.name, avatar: null, me: m.is_me, teamId: m.team_id,
            a: money(m.space), b: m.open_spots != null ? String(m.open_spots) : '—',
          }))}
        />
      ) : null}
      {data.coming_off?.players.length ? (
        <Card>
          <Eyebrow right={<Text style={[ui.name, ui.gold]}>{money(data.coming_off.total)}</Text>}>COMING OFF YOUR BOOKS</Eyebrow>
          <View style={styles.heads}>
            {data.coming_off.players.slice(0, 3).map((p) => (
              <View key={p.player_id || p.name} style={styles.head}>
                <Headshot playerId={p.player_id} pos={p.pos} ring="done" />
                <Text style={styles.headName} numberOfLines={1}>{shortName(p.name)}</Text>
                <Text style={styles.headSub}>{money(p.salary)}</Text>
              </View>
            ))}
          </View>
        </Card>
      ) : null}
      <AskBar placeholder="Plan my draft" />
      <ProspectCard />
    </>
  );
}

const styles = StyleSheet.create({
  top: { flexDirection: 'row', alignItems: 'center', marginBottom: 12 },
  eyebrow: { color: colors.muted, fontSize: 13 },
  hey: { color: colors.text, fontSize: 28, fontWeight: '800' },
  meBadge: { width: 36, height: 36, borderRadius: 18, borderWidth: 1, borderColor: colors.line, alignItems: 'center', justifyContent: 'center' },
  meText: { color: colors.gold, fontWeight: '800', fontSize: 13 },
  between: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  cardEyebrow: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1.2, flexShrink: 1 },
  dimSmall: { color: colors.muted, fontSize: 12 },
  dim: { color: colors.muted },
  gap: { marginTop: 8 },
  vs: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 12 },
  vsText: { color: colors.muted, fontWeight: '800', fontSize: 9, letterSpacing: 1 },
  vsMid: { alignItems: 'center', flex: 1 },
  vsSub: { color: colors.muted, opacity: 0.75, fontSize: 15.5, fontWeight: '600', marginTop: 7 },
  odds: { width: 96, marginTop: 10 },
  oddsNums: { flexDirection: 'row', justifyContent: 'space-between' },
  oddsMine: { color: colors.gold, fontSize: 13, fontWeight: '800' },
  oddsTheirs: { color: colors.muted, fontSize: 13, fontWeight: '800' },
  oddsTrack: { height: 6, borderRadius: 3, backgroundColor: '#3A4A42', overflow: 'hidden', marginTop: 4 },
  oddsFill: { height: '100%', backgroundColor: colors.gold },
  oddsLabel: { color: colors.muted, fontSize: 9, fontWeight: '800', letterSpacing: 1, textAlign: 'center', marginTop: 5 },
  side: { alignItems: 'center', gap: 6, width: 130 },
  sideName: { color: colors.text, fontWeight: '800', fontSize: 15 },
  score: { color: colors.text, fontSize: 30, fontWeight: '800' },
  proj: { color: colors.muted, fontSize: 12, marginTop: -4 },
  winner: { color: colors.gold, fontSize: 10, fontWeight: '800', letterSpacing: 1.4, marginTop: -2 },
  hidden: { opacity: 0 },
  bar: { marginTop: 12 },
  badges: { flexDirection: 'row', gap: 8, marginTop: 12 },
  byeTag: { marginTop: 8, fontSize: 9.5, fontWeight: '800', letterSpacing: 1, color: colors.gold, borderWidth: 1, borderColor: 'rgba(200,155,74,0.5)', borderRadius: 6, paddingHorizontal: 7, paddingVertical: 3, overflow: 'hidden' },
  champ: { alignItems: 'center', paddingVertical: 18, overflow: 'hidden' },
  champEyebrow: { letterSpacing: 2, textAlign: 'center' },
  champCup: { fontSize: 64, lineHeight: 74, marginTop: 10, marginBottom: 2 },
  champName: { color: colors.text, fontSize: 28, fontWeight: '900' },
  champTeam: { color: colors.muted, fontSize: 13, fontWeight: '700', letterSpacing: 0.5 },
  champLine: { color: '#E2BC5B', fontSize: 14, fontWeight: '800', marginTop: 10 },
  heroNote: { color: colors.muted, fontSize: 11, textAlign: 'center', marginTop: 8, opacity: 0.8 },
  badge: { flex: 1, backgroundColor: colors.row, borderRadius: 12, paddingVertical: 8, paddingHorizontal: 4, alignItems: 'center' },
  badgeValue: { color: colors.text, fontSize: 17, fontWeight: '800' },
  badgeProj: { color: colors.muted, opacity: 0.75, fontSize: 13, fontWeight: '600', lineHeight: 21 },
  badgeLabel: { color: colors.muted, fontSize: 10, fontWeight: '700', letterSpacing: 0.6, marginTop: 2, textAlign: 'center' },
  heads: { flexDirection: 'row', gap: 10, marginTop: 12 },
  head: { flex: 1, alignItems: 'center' },
  headName: { color: colors.text, fontSize: 12, fontWeight: '700', marginTop: 8 },
  headPts: { color: colors.gold, fontSize: 16, fontWeight: '800' },
  headSub: { color: colors.muted, fontSize: 10, fontWeight: '700', marginTop: 1 },
  needs: { color: colors.text, fontSize: 12, fontWeight: '700', textAlign: 'center', marginTop: 12 },
  ask: {
    flexDirection: 'row', alignItems: 'center', gap: 10, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.gold,
    borderRadius: 24, padding: 8, marginBottom: 12,
  },
  gm: { width: 32, height: 32, borderRadius: 16, backgroundColor: colors.gold, alignItems: 'center', justifyContent: 'center' },
  gmText: { color: colors.bg, fontWeight: '900', fontSize: 12 },
  askInput: { flex: 1, color: colors.text, fontSize: 14, paddingVertical: 6 },
  go: { width: 30, height: 30, borderRadius: 15, backgroundColor: colors.green, alignItems: 'center', justifyContent: 'center' },
  goText: { color: colors.text, fontWeight: '900', fontSize: 16 },
  tHead: { flexDirection: 'row', alignItems: 'center', marginTop: 8, paddingLeft: 22 },
  th: { color: colors.muted, fontSize: 10, fontWeight: '800', letterSpacing: 0.8, width: 52, textAlign: 'right' },
  tRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 7, borderTopWidth: 1, borderTopColor: colors.row },
  tCut: { borderTopColor: colors.gold, borderStyle: 'dashed' },
  tRank: { color: colors.muted, width: 22, fontSize: 13 },
  tOwner: { flex: 1, textAlign: 'left' },
  tOwnerCell: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  tName: { color: colors.text, fontWeight: '700', fontSize: 14, flexShrink: 1 },
  td: { color: colors.text, fontSize: 14, width: 52, textAlign: 'right' },
  upNext: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 10 },
  issue: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 10 },
  issueText: { color: '#E08A8A', fontSize: 12, marginTop: 1 },
  chev: { color: colors.muted, fontSize: 22, paddingLeft: 4 },
  bracket: { flexDirection: 'row', gap: 10, marginTop: 8 },
  roundCol: { flex: 1, gap: 8, justifyContent: 'center' },
  roundLabel: { color: colors.muted, fontSize: 9, fontWeight: '800', letterSpacing: 0.8, textAlign: 'center' },
  match: { backgroundColor: colors.row, borderRadius: 10, padding: 8, gap: 4 },
  matchGold: { borderWidth: 1, borderColor: colors.gold },
  matchRow: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  matchOut: { opacity: 0.45 },
  matchTbd: { color: colors.muted, fontSize: 10, fontWeight: '700', paddingVertical: 2 },
  bk: { flexDirection: 'row', alignItems: 'center', marginTop: 22 },
  bkSide: { flex: 1, gap: 4 },
  bkMid: { flex: 1, gap: 4, transform: [{ translateY: -12 }] }, // the final sits a little above the semis
  bkLine: { width: 8, height: 1.5, backgroundColor: colors.gold, opacity: 0.6 },
  bkWeeks: { color: colors.muted, fontSize: 9, fontWeight: '700', textAlign: 'center', opacity: 0.8 },
  matchName: { color: colors.text, fontSize: 11, fontWeight: '700', flex: 1 },
  matchScore: { color: colors.text, fontSize: 11, fontWeight: '700' },
  deal: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 10 },
  dealLine: { borderTopWidth: 1, borderTopColor: colors.row },
  choice: { borderWidth: 1, borderColor: colors.line, borderRadius: 10, paddingVertical: 6, paddingHorizontal: 10 },
  choiceOn: { borderColor: colors.gold },
  choiceOff: { opacity: 0.45 },
  choiceText: { color: colors.text, fontWeight: '700', fontSize: 12 },
  capBar: { flexDirection: 'row', height: 14, borderRadius: 7, overflow: 'hidden', marginTop: 10 },
  legend: { flexDirection: 'row', gap: 14, marginTop: 6 },
  legendText: { color: colors.muted, fontSize: 11 },
  picks: { flexDirection: 'row', gap: 8, marginTop: 10, flexWrap: 'wrap' },
  pick: { flexGrow: 1, minWidth: 70, backgroundColor: colors.row, borderRadius: 12, paddingVertical: 10, paddingHorizontal: 4, alignItems: 'center' },
  pickRound: { color: colors.gold, fontSize: 20, fontWeight: '800' },
  pickSub: { color: colors.muted, fontSize: 10, marginTop: 2 },
});
