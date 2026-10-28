import { router, useFocusEffect } from 'expo-router';
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { Avatar, Headshot, type Ring } from '../../components/Pictures';
import { ErrorState, Loading } from '../../components/ScreenState';
import { Bar, Card, Pill, Screen, firstName, money, ordinal, record, ui } from '../../components/ui';
import { type HomeData, type HomeSide, type LivePlayer, type RaceRow, type RookieDeal, type TableRow, apiSend, prefetch, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

// Home changes with the calendar: last week's recap (Tue-Wed), a preview
// before kickoff, live scores on game days and Monday night's "still to
// play", the bracket in the playoffs, rookie-deal decisions until the June 30
// rollover, then draft prep. Pictures, badges and tables over sentences.
const ROUND = ['', '1st', '2nd', '3rd', '4th', '5th'];
const DAY = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: 'long' });
const SHORT = (iso: string) => new Date(iso.length > 10 ? iso : `${iso}T12:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
const KICK = (iso: string | null) => {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.toLocaleDateString(undefined, { weekday: 'short' })} ${d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`;
};
const pts = (n: number) => n.toFixed(1);

export default function Home() {
  const [live, setLive] = useState(false);
  // Game days refresh every minute while Home is open.
  const { data, error, loading, reload, refresh } = useApi<HomeData>('/home', live ? 60_000 : 0);
  const isLive = data?.phase === 'game_day' || data?.phase === 'playoffs';
  useEffect(() => setLive(isLive), [isLive]);
  // Once Home is up, warm the other tabs so they open instantly.
  const warmed = useRef(false);
  useEffect(() => {
    if (!data || warmed.current) return;
    warmed.current = true;
    prefetch(['/my-team', '/standings', '/matchups', '/teams', '/front-office', '/market/free-agents', '/market/expiring']);
  }, [data]);

  const seen = useRef(false);
  useFocusEffect(useCallback(() => {
    if (seen.current) refresh();
    seen.current = true;
  }, [refresh]));

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const eyebrow = data.phase === 'decisions' || data.phase === 'draft_prep'
    ? `${data.season + (data.phase === 'draft_prep' ? 0 : 1)} offseason`
    : data.phase === 'playoffs' ? `Playoffs · Week ${data.week}` : `Week ${data.week} · ${DAY(data.today)}`;

  return (
    <Screen loading={loading} onRefresh={reload}>
      <View style={styles.top}>
        <View style={ui.flex}>
          <Text style={styles.eyebrow}>{eyebrow}</Text>
          <Text style={styles.hey}>Hey, {firstName(data.owner_name) || 'there'}</Text>
        </View>
        <Pressable onPress={() => router.push('/settings')} hitSlop={8}>
          <View style={styles.meBadge}><Text style={styles.meText}>{initials(data.owner_name)}</Text></View>
        </Pressable>
      </View>

      {data.phase === 'recap' ? <Recap data={data} /> : null}
      {data.phase === 'game_day' ? <GameDay data={data} /> : null}
      {data.phase === 'playoffs' ? <Playoffs data={data} /> : null}
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

function Badges({ items }: { items: [string, string][] }) {
  return (
    <View style={styles.badges}>
      {items.map(([value, label]) => (
        <View key={label} style={styles.badge}>
          <Text style={styles.badgeValue}>{value}</Text>
          <Text style={styles.badgeLabel}>{label}</Text>
        </View>
      ))}
    </View>
  );
}

function Hero({ title, pill, me, them, week, matchupId, meLabel, themLabel, badges, showScores = true }: {
  title: string; pill?: ReactNode; me: HomeSide; them?: HomeSide | null; week: number; matchupId?: number;
  meLabel?: string; themLabel?: string; badges: [string, string][]; showScores?: boolean;
}) {
  const total = me.score + (them?.score ?? 0);
  const ahead = me.score >= (them?.score ?? 0);
  const open = matchupId != null ? () => router.push(`/game/${week}/${matchupId}`) : undefined;
  const Side = ({ s, label, lead }: { s: HomeSide | null | undefined; label: string; lead: boolean }) => (
    <View style={styles.side}>
      <Avatar uri={s?.avatar} name={s?.name ?? 'Bye'} size={54} me={label.startsWith('You')} />
      <Text style={styles.sideName} numberOfLines={1}>{label}</Text>
      {showScores ? <Text style={[styles.score, !lead && styles.dim]}>{pts(s?.score ?? 0)}</Text> : null}
    </View>
  );
  return (
    <Pressable onPress={open}>
      <Card gold>
        <Eyebrow right={pill}>{title}</Eyebrow>
        <View style={styles.vs}>
          <Side s={me} label={meLabel ?? 'You'} lead={!total || ahead} />
          <Text style={styles.vsText}>VS</Text>
          <Side s={them} label={themLabel ?? (them ? firstName(them.name) : 'Bye')} lead={!total || !ahead} />
        </View>
        <View style={styles.bar}><Bar fraction={total ? me.score / total : 0.5} /></View>
        <Badges items={badges} />
      </Card>
    </Pressable>
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
            <Text style={[styles.tName, r.me && ui.gold]} numberOfLines={1}>{r.me ? 'You' : firstName(r.name)}</Text>
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
            <Text style={ui.name}>{them.name}</Text>
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
  if (data.game?.is_final) return <Pill label="FINAL" />;
  if (anyPlaying(data)) return <Pill label={`● ${label}`} tone="live" />;
  return waiting ? <Pill label={waiting} tone="gold" /> : null;
}

// ---- phases ----------------------------------------------------------------

function Recap({ data }: { data: HomeData }) {
  const r = data.recap;
  const game = r?.game;
  const next = data.game?.them;
  const won = game?.me && game.me.score > (game.them?.score ?? 0);
  return (
    <>
      {game?.me ? (
        <Hero
          title={`WEEK ${r!.week} FINAL${game.them ? ` · VS ${firstName(game.them.name).toUpperCase()}` : ''}`}
          pill={<Pill label={won ? 'WIN' : game.me.score === game.them?.score ? 'TIE' : 'LOSS'} tone={won ? 'live' : 'out'} />}
          me={game.me}
          them={game.them}
          week={r!.week}
          matchupId={game.matchup_id}
          badges={[[ordinal(game.me.rank), 'HIGH SCORE'], [game.me.top5 ? '✓' : '✗', 'TOP 5'], [`+${r!.standing_points}`, 'STANDING PTS']]}
        />
      ) : null}
      <PlayerHeads title="YOUR TOP SCORERS" right={`Week ${r?.week ?? data.week - 1}`} players={r?.top_scorers ?? []} />
      <AskBar placeholder={`Ask your GM about week ${data.week}…`} />
      {data.standings?.length ? <Standings rows={data.standings} playoffTeams={data.playoff_teams} /> : null}
      {next ? <UpNext them={next} week={data.week} series={data.series} /> : null}
    </>
  );
}

function GameDay({ data }: { data: HomeData }) {
  const g = data.game;
  if (!g?.me) {
    return (
      <>
        <Card gold><Eyebrow>{`WEEK ${data.week}`}</Eyebrow><Text style={[ui.sub, styles.gap]}>No game this week.</Text></Card>
        <AskBar placeholder="Ask your GM…" />
        {data.standings?.length ? <Standings rows={data.standings} playoffTeams={data.playoff_teams} /> : null}
      </>
    );
  }
  const mine = data.players?.me ?? [];
  const theirs = data.players?.them ?? [];
  const started = g.me.score > 0 || (g.them?.score ?? 0) > 0 || mine.some((p) => p.status !== 'waiting' && p.status !== 'empty' && p.status !== 'bye');
  const title = `WEEK ${data.week}${g.them ? ` · VS ${firstName(g.them.name).toUpperCase()}` : ''}`;

  if (!started) {
    const first = mine.filter((p) => p.kickoff).sort((a, b) => (a.kickoff! < b.kickoff! ? -1 : 1));
    const s = data.series;
    return (
      <>
        <Hero
          title={title}
          pill={first[0] ? <Pill label={`KICKOFF ${KICK(first[0].kickoff).toUpperCase()}`} /> : undefined}
          me={g.me}
          them={g.them}
          week={data.week}
          matchupId={g.matchup_id}
          showScores={false}
          badges={[
            [g.me.ppg != null ? g.me.ppg.toFixed(1) : '—', 'YOUR PPG'],
            [g.them?.ppg != null ? g.them.ppg.toFixed(1) : '—', 'THEIR PPG'],
            [s ? record(s.wins, s.losses, s.ties) : '0-0', 'SERIES'],
          ]}
        />
        <PlayerHeads title="YOUR STARTERS · FIRST UP" players={first.slice(0, 3)} showPoints={false} sub={(p) => KICK(p.kickoff)} />
        <LineupCheck issues={data.lineup_issues ?? []} />
        <AskBar placeholder="Who should I start this week?" />
        {data.standings?.length ? <Standings rows={data.standings} playoffTeams={data.playoff_teams} /> : null}
      </>
    );
  }

  const ahead = g.me.score >= (g.them?.score ?? 0);
  if (data.weekday === 0 && !g.is_final) {
    const still = [...mine.filter((p) => p.status !== 'final' && p.player_id && p.status !== 'bye').map((p) => ({ ...p, side: 'You' })),
      ...theirs.filter((p) => p.status !== 'final' && p.player_id && p.status !== 'bye').map((p) => ({ ...p, side: firstName(g.them?.name ?? '') }))];
    const gap = Math.abs(g.me.score - (g.them?.score ?? 0));
    return (
      <>
        <Hero
          title={title}
          pill={<LivePill data={data} label="MNF" waiting="MNF TONIGHT" />}
          me={g.me}
          them={g.them}
          week={data.week}
          matchupId={g.matchup_id}
          badges={[[String(left(mine)), 'LEFT: YOU'], [String(left(theirs)), `LEFT: ${firstName(g.them?.name ?? 'THEM').toUpperCase()}`], [ordinal(g.me.rank), 'HIGH SCORE']]}
        />
        {still.length ? (
          <Card>
            <Eyebrow>STILL TO PLAY</Eyebrow>
            <View style={styles.heads}>
              {still.slice(0, 4).map((p, i) => (
                <View key={`${p.player_id}-${i}`} style={styles.head}>
                  <Headshot playerId={p.player_id} pos={p.pos} team={p.team} ring={p.side === 'You' ? 'gold' : 'them'} size={56} />
                  <Text style={styles.headName} numberOfLines={1}>{shortName(p.name)}</Text>
                  <Text style={styles.headSub} numberOfLines={1}>{p.side} · {p.status === 'playing' ? pts(p.points) : KICK(p.kickoff)}</Text>
                </View>
              ))}
            </View>
            {g.them ? <Text style={styles.needs}>{ahead ? `${firstName(g.them.name)} needs ${pts(gap)} to pass you` : `You need ${pts(gap)} to win`}</Text> : null}
          </Card>
        ) : null}
        <Race rows={data.race ?? []} title="TOP 5 RACE" window="bubble" />
        <AskBar placeholder="What do I need tonight?" />
      </>
    );
  }

  const now = [...mine].filter((p) => p.player_id && p.status !== 'bye')
    .sort((a, b) => order(a) - order(b) || b.points - a.points).slice(0, 3);
  return (
    <>
      <Hero
        title={title}
        pill={<LivePill data={data} />}
        me={g.me}
        them={g.them}
        week={data.week}
        matchupId={g.matchup_id}
        badges={[[ordinal(g.me.rank), 'HIGH SCORE'], [g.me.top5 ? '✓' : '✗', 'IN TOP 5'], [String(left(mine)), 'LEFT TO PLAY']]}
      />
      <PlayerHeads title="YOUR PLAYERS RIGHT NOW" right={anyPlaying(data) ? 'Live' : undefined} players={now} sub={statusLine} />
      <Race rows={data.race ?? []} title={g.is_final ? 'TOP 5 · FINAL' : 'TOP 5 RACE · LIVE'} window="top" />
      <AskBar placeholder="How is my week going?" />
    </>
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
        <View key={`${p.player_id}-${i}`} style={styles.issue}>
          <Headshot playerId={p.player_id} pos={p.pos} team={p.team} size={44} ring="red" />
          <View style={ui.flex}>
            <Text style={ui.name}>{p.player_id ? shortName(p.name) : p.name} <Text style={styles.dimSmall}>{p.pos}</Text></Text>
            <Text style={styles.issueText}>{p.issue} · in your lineup</Text>
          </View>
        </View>
      ))}
      {issues.length ? <Text style={[ui.sub, styles.gap]}>Set your lineup in Sleeper.</Text> : null}
    </Card>
  );
}

function Playoffs({ data }: { data: HomeData }) {
  const g = data.game;
  const mine = data.players?.me ?? [];
  const seed = (s?: HomeSide | null) => {
    const found = data.bracket?.flatMap((r) => r.games.flatMap((x) => [x.a, x.b])).find((b) => b && s && b.roster_id === s.roster_id);
    return found?.seed ? ` · ${ordinal(found.seed).replace(/(st|nd|rd|th)$/, '')} seed` : '';
  };
  const round = data.bracket?.find((r) => r.week === data.week);
  return (
    <>
      {g?.me ? (
        <Hero
          title={`${(round?.label ?? 'Playoffs').toUpperCase()}${g.them ? ` · VS ${firstName(g.them.name).toUpperCase()}` : ''}`}
          pill={<LivePill data={data} />}
          me={g.me}
          them={g.them}
          week={data.week}
          matchupId={g.matchup_id}
          meLabel={`You${seed(g.me)}`}
          themLabel={g.them ? `${firstName(g.them.name)}${seed(g.them)}` : undefined}
          badges={[[String(left(mine)), 'LEFT: YOU'], [String(left(data.players?.them ?? [])), 'LEFT: THEM'], [ordinal(g.me.rank), 'HIGH SCORE']]}
        />
      ) : null}
      {data.bracket?.length ? (
        <Card>
          <Eyebrow right={`Top ${data.playoff_teams}`}>BRACKET</Eyebrow>
          <View style={styles.bracket}>
            {data.bracket.map((rnd) => (
              <View key={rnd.round} style={styles.roundCol}>
                <Text style={styles.roundLabel}>{rnd.label.toUpperCase()} · WK {rnd.week}</Text>
                {rnd.games.map((game, i) => (
                  <View key={i} style={styles.match}>
                    {[game.a, game.b].map((s, j) => {
                      const other = j === 0 ? game.b : game.a;
                      const leads = game.winner ? game.winner === s?.roster_id : (s?.score ?? 0) > (other?.score ?? 0);
                      return (
                        <View key={j} style={styles.matchRow}>
                          <Avatar uri={s?.avatar} name={s?.name ?? '?'} size={20} />
                          <Text style={[styles.matchName, leads && ui.gold]} numberOfLines={1}>{s ? firstName(s.name) : 'TBD'}</Text>
                          <Text style={[styles.matchScore, leads && ui.gold]}>{s?.score ? pts(s.score) : ''}</Text>
                        </View>
                      );
                    })}
                  </View>
                ))}
              </View>
            ))}
          </View>
        </Card>
      ) : null}
      <PlayerHeads title="YOUR PLAYERS RIGHT NOW" players={mine.filter((p) => p.player_id).sort((a, b) => order(a) - order(b) || b.points - a.points).slice(0, 3)} sub={statusLine} />
      <AskBar placeholder="Who should I start this week?" />
      {!g?.me && data.standings?.length ? <Standings rows={data.standings} playoffTeams={data.playoff_teams} /> : null}
    </>
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
  vsText: { color: colors.muted, fontWeight: '800' },
  side: { alignItems: 'center', gap: 6, width: 130 },
  sideName: { color: colors.text, fontWeight: '800', fontSize: 15 },
  score: { color: colors.text, fontSize: 30, fontWeight: '800' },
  bar: { marginTop: 12 },
  badges: { flexDirection: 'row', gap: 8, marginTop: 12 },
  badge: { flex: 1, backgroundColor: colors.row, borderRadius: 12, paddingVertical: 8, paddingHorizontal: 4, alignItems: 'center' },
  badgeValue: { color: colors.text, fontSize: 17, fontWeight: '800' },
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
  bracket: { flexDirection: 'row', gap: 10, marginTop: 8 },
  roundCol: { flex: 1, gap: 8, justifyContent: 'center' },
  roundLabel: { color: colors.muted, fontSize: 9, fontWeight: '800', letterSpacing: 0.8, textAlign: 'center' },
  match: { backgroundColor: colors.row, borderRadius: 10, padding: 8, gap: 4 },
  matchRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  matchName: { color: colors.text, fontSize: 12, fontWeight: '700', flex: 1 },
  matchScore: { color: colors.text, fontSize: 12, fontWeight: '700' },
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
