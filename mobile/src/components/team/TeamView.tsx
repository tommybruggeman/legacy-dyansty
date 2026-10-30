import { router, useFocusEffect } from 'expo-router';
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';

import type { Pick, Player, TeamPage, TeamWeek, WeekPlayer } from '../../lib/api';
import { colors } from '../../lib/theme';
import { PlayerFace } from '../PlayerFace';
import { LineupSheet, type Picking, SlotChip } from './LineupSheet';
import { Card, Note, Pill, Pos, Row, SwipeTabs, Tile, Tiles, Title, firstName, money, ordinal, record, ui } from '../ui';

const TABS = ['Roster', 'Contracts', 'Picks'] as const;

// Set when a team page opens a page of its own, so returning from it keeps the tab.
let openedHere = false;
function go(path: Parameters<typeof router.push>[0]) {
  openedHere = true;
  router.push(path);
}

// While any of this team's players is on the field, the page refreshes this often (the server scores at most every 15 s).
export const LIVE_REFRESH_MS = 15_000;
export const isLive = (team: TeamPage | null) => !!team?.week && !team.week.is_final && Object.values(team.week.players).some((p) => p.status === 'playing');

// One owner's team with the same three tabs as My Team (league activity lives on League > Teams). Other owners' teams
// are read only; trades are made in Sleeper.
export function TeamView({ team, eyebrow, badge, top, onChange }: { team: TeamPage; eyebrow?: string; badge?: ReactNode; top?: ReactNode; onChange?: () => void }) {
  const [tab, setTab] = useState<(typeof TABS)[number]>('Roster');
  // Always open on the roster. Coming back from a player or cap page opened here keeps the tab;
  // leaving for another part of the app and coming back starts over.
  const opened = useRef(false);
  useFocusEffect(useCallback(() => {
    const back = opened.current;
    opened.current = false;
    if (!back) setTab('Roster');
    return () => { opened.current = openedHere; openedHere = false; };
  }, []));
  useEffect(() => setTab('Roster'), [team.team_id]);
  // Other owners' teams are one page (no tabs), so a swipe there only ever means back to the teams list.
  if (!team.is_me) {
    return (
      <>
        <Title badge={badge}>{team.team_name}</Title>
        {top}
        <TopTiles team={team} tab="Roster" />
        <RosterTab team={team} onChange={onChange} after={<><CapCard team={team} /><PicksCard team={team} /></>} />
      </>
    );
  }
  return (
    <>
      <Title eyebrow={eyebrow} badge={badge}>{team.team_name}</Title>
      {top}
      <TopTiles team={team} tab={tab} />
      <UnderlineTabs value={tab} onChange={setTab} />
      <SwipeTabs
        options={TABS}
        value={tab}
        onChange={setTab}
        render={(t) =>
          t === 'Roster' ? <RosterTab team={team} onChange={onChange} /> : t === 'Contracts' ? <ContractsTab team={team} /> : <PicksTab team={team} />
        }
      />
    </>
  );
}

// The three boxes on top change with the tab: each tab's first two, then one that fits it.
function TopTiles({ team, tab }: { team: TeamPage; tab: (typeof TABS)[number] }) {
  const r = team.record;
  if (tab === 'Contracts') {
    const label = ['THIS SEASON', 'NEXT SEASON'];
    return (
      <Tiles>
        {team.cap.by_year.slice(0, 3).map((y, i) => (
          <Tile key={y.season} label={label[i] ?? String(y.season)} value={money(y.space)} tone={y.space < 0 ? 'red' : undefined} sub={i < 2 ? `${y.season} space` : "cap space"} />
        ))}
      </Tiles>
    );
  }
  if (tab === 'Picks') {
    const owned = team.picks.owned;
    const drafts = [...new Set(owned.map((p) => p.year))];
    const first = owned[0];
    const firsts = owned.filter((p) => p.round === 1);
    const firstYears = [...new Set(firsts.map((p) => p.year))];
    return (
      <Tiles>
        <Tile label="PICKS" value={String(owned.length)} sub={`next ${drafts.length || 0} draft${drafts.length === 1 ? '' : 's'}`} />
        <Tile label="FIRST" value={first ? ROUND[first.round] ?? `R${first.round}` : '—'} sub={first ? `${first.year} draft` : 'no picks'} />
        <Tile label="1STS" value={String(firsts.length)} sub={firstYears.length ? firstYears.join(' + ') : 'none owned'} />
      </Tiles>
    );
  }
  const power = team.power;
  const move = power?.move ? `${power.move > 0 ? '▲' : '▼'}${Math.abs(power.move)} this week` : null;
  const wk = team.week;
  if (wk) {
    const started = Object.values(wk.players).some((p) => p.status === 'playing' || p.status === 'final');
    const opp = wk.opponent;
    const won = opp ? wk.score > opp.score : false;
    return (
      <Tiles>
        <Tile label="RECORD" value={record(r.wins, r.losses, r.ties)} sub={`${ordinal(r.rank)} · ${r.standing_points} SP`} />
        <Tile label={`WEEK ${wk.week}`} value={(wk.is_final || started ? wk.score : wk.projected ?? 0).toFixed(1)} tone={!wk.is_final && started ? 'live' : undefined}
          sub={wk.is_final ? 'final' : started && wk.projected != null ? `proj ${wk.projected.toFixed(1)}` : 'projected'} />
        {opp ? (
          <Tile label={`VS ${firstName(opp.name).toUpperCase()}`}
            value={wk.is_final ? (won ? 'Won' : wk.score < opp.score ? 'Lost' : 'Tied') : wk.win_chance != null ? `${wk.win_chance}%` : '—'}
            sub={wk.is_final ? `by ${Math.abs(wk.score - opp.score).toFixed(1)}` : 'win odds'} />
        ) : (
          <Tile label="POWER RANK" value={power ? `#${power.rank}` : '—'} sub={move ?? (power?.of ? `of ${power.of} teams` : 'not ranked yet')} />
        )}
      </Tiles>
    );
  }
  return (
    <Tiles>
      <Tile label="RECORD" value={record(r.wins, r.losses, r.ties)} sub={`${ordinal(r.rank)} · ${r.standing_points} SP`} />
      <Tile label="PPG" value={r.pf_per_game != null ? r.pf_per_game.toFixed(1) : '—'} sub="avg / game" />
      <Tile label="POWER RANK" value={power ? `#${power.rank}` : '—'} sub={move ?? (power?.of ? `of ${power.of} teams` : 'not ranked yet')} />
    </Tiles>
  );
}

// Slim text tabs with a gold underline (swipe the page to move between them too).
function UnderlineTabs({ value, onChange }: { value: (typeof TABS)[number]; onChange: (v: (typeof TABS)[number]) => void }) {
  return (
    <View style={styles.tabs}>
      {TABS.map((t) => (
        <Pressable key={t} onPress={() => onChange(t)} hitSlop={8} style={[styles.tab, t === value && styles.tabOn]}>
          <Text style={[styles.tabText, t === value && styles.tabTextOn]}>{t.toUpperCase()}</Text>
        </Pressable>
      ))}
    </View>
  );
}

// Short injury labels so the tag doesn't crowd the name.
const INJURY_SHORT: Record<string, string> = { questionable: 'Q', doubtful: 'D', out: 'OUT', ir: 'IR', pup: 'PUP', sus: 'SUS' };

function Face({ p, label, hurt }: { p: Player; label: string; hurt?: boolean }) {
  return <PlayerFace name={p.name} team={p.nfl_team} pos={label} hurt={hurt} />;
}


function PlayerRow({ p, first, showSlot, chip, onEmpty, w, season }: { p: Player; first?: boolean; showSlot?: boolean; chip?: ReactNode; onEmpty?: () => void; w?: WeekPlayer; season: number }) {
  if (p.empty) {
    return (
      <Row first={first} onPress={onEmpty}>
        {chip}
        <View style={styles.emptyFace}><Text style={styles.emptyPlus}>+</Text></View>
        <View style={ui.flex}>
          <Text style={[ui.name, styles.emptyName]}>Empty</Text>
          <Text style={ui.sub}>{onEmpty ? 'Tap to start someone' : 'Nobody starting here'}</Text>
        </View>
      </Row>
    );
  }
  const injury = p.injury ? INJURY_SHORT[p.injury.toLowerCase()] ?? p.injury.toUpperCase() : null;
  const tag = injury ? <Pill label={injury} tone="out" /> : p.is_rookie ? <Pill label="ROOKIE" tone="gold" /> : null;
  const label = showSlot && p.slot ? p.slot : p.pos;
  const live = w?.status === 'playing';
  return (
    <Pressable onPress={() => go(`/player/${p.player_id}`)}>
      <View style={[styles.prow, first && styles.prowFirst, live && styles.prowLive]}>
        <View style={styles.pmain}>
          {chip}
          <Face p={p} label={chip ? p.pos : label} hurt={!!injury} />
          <View style={ui.flex}>
            <View style={styles.nameLine}>
              <Text style={[ui.name, styles.shrink]} numberOfLines={1}>{p.name}</Text>
              {tag}
            </View>
            <GameText p={p} w={w} />
            {w?.line?.length ? (
              <Text style={styles.statLine}>
                {w.line.map((s, i) => (
                  <Text key={i} style={s.tone === 'good' ? styles.good : s.tone === 'bad' ? styles.bad : undefined}>{i ? ', ' : ''}{s.text}</Text>
                ))}
              </Text>
            ) : null}
          </View>
          <Points p={p} w={w} />
        </View>
        <ContractFoot p={p} w={w} season={season} />
      </View>
    </Pressable>
  );
}

// The game: kickoff time before, the clock and score (with a football when his team has the ball) during, the result after.
function GameText({ p, w }: { p: Player; w?: WeekPlayer }) {
  const g = w?.game;
  if (!w) return <Text style={ui.sub}>{p.nfl_team || 'FA'}</Text>;
  if (w.status === 'bye') return <Text style={styles.game}>BYE</Text>;
  const vs = g ? `${g.home ? 'vs' : '@'} ${g.opp}` : p.nfl_team || '';
  if (g && (w.status === 'playing' || g.state === 'in')) {
    return (
      <View style={styles.gameRow}>
        <View style={styles.dot} />
        <Text style={[styles.game, styles.gameLive]} numberOfLines={1}>
          <Text style={styles.clock}>{g.quarter > 4 ? 'OT' : `Q${g.quarter}`} {g.clock}</Text> {g.score}-{g.opp_score} {vs}
        </Text>
        {g.possession ? <View style={styles.ball}><View style={styles.lace} /></View> : null}
      </View>
    );
  }
  if (g && (w.status === 'final' || g.state === 'post')) {
    const res = g.score > g.opp_score ? 'W' : g.score < g.opp_score ? 'L' : 'T';
    return <Text style={styles.game} numberOfLines={1}><Text style={styles.finalWord}>Final</Text> {res} {g.score}-{g.opp_score} {vs}</Text>;
  }
  if (w.status === 'final') return <Text style={styles.game}>Final</Text>;
  return <Text style={styles.game} numberOfLines={1}>{[w.kickoff ? kickoffText(w.kickoff) : null, vs].filter(Boolean).join(' ')}</Text>;
}

// Before kickoff his projection; during the game live points with the projection that follows the game;
// after it his final points with the projection he started the day with.
function Points({ p, w }: { p: Player; w?: WeekPlayer }) {
  if (!w) {
    return (
      <View style={ui.right}>
        <Text style={styles.big}>{p.ppg != null ? p.ppg.toFixed(1) : '—'}</Text>
        <Text style={styles.under}>PPG</Text>
      </View>
    );
  }
  if (w.status === 'playing') {
    return (
      <View style={ui.right}>
        <Text style={[styles.big, styles.bigLive]}>{w.points.toFixed(1)}</Text>
        {w.live_proj != null ? <Text style={styles.under}>proj {w.live_proj.toFixed(1)}</Text> : null}
      </View>
    );
  }
  if (w.status === 'final') {
    return (
      <View style={ui.right}>
        <Text style={styles.big}>{w.points.toFixed(1)}</Text>
        {w.proj != null ? <Text style={styles.under}>proj {w.proj.toFixed(1)}</Text> : null}
      </View>
    );
  }
  return (
    <View style={ui.right}>
      <Text style={[styles.big, styles.bigPre]}>{w.status === 'bye' ? '—' : w.proj != null ? w.proj.toFixed(1) : '—'}</Text>
      <Text style={styles.under}>PROJ</Text>
    </View>
  );
}

// Salary, years and the last season of the deal; on the right his season PPG, or once his game is done what each point cost.
function ContractFoot({ p, w, season }: { p: Player; w?: WeekPlayer; season: number }) {
  if (p.no_contract) {
    return <View style={styles.foot}><Text style={styles.footText}>No contract</Text></View>;
  }
  const years = Math.max(p.years_left, 1);
  const done = w?.status === 'final' && w.points > 0 && p.contract_salary > 0;
  return (
    <View style={styles.foot}>
      <Text style={styles.footText} numberOfLines={1}>
        <Text style={styles.footGold}>{dollars(p.contract_salary)}</Text> · {years} yr{years === 1 ? '' : 's'} · through {season + years - 1}
      </Text>
      <Text style={styles.footText} numberOfLines={1}>
        {done ? <><Text style={styles.footGold}>${(p.contract_salary / w!.points).toFixed(2)} per point</Text> this week</> : p.ppg != null ? `${p.ppg.toFixed(1)} PPG this season` : 'no games yet'}
      </Text>
    </View>
  );
}

// "$48" for whole dollars, "$12.5" otherwise.
const dollars = (n: number) => `$${Number.isInteger(n) ? n : n.toFixed(1)}`;

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
// "Sun 11:00 AM" in the phone's own time zone.
function kickoffText(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const h = d.getHours() % 12 || 12;
  return `${DAYS[d.getDay()]} ${h}:${String(d.getMinutes()).padStart(2, '0')} ${d.getHours() < 12 ? 'AM' : 'PM'}`;
}

function pointsLabel(week: TeamWeek | null | undefined) {
  if (!week) return 'PPG · SALARY';
  const ps = Object.values(week.players);
  if (week.is_final || (ps.length && ps.every((p) => p.status === 'final' || p.status === 'bye'))) return `WEEK ${week.week} PTS`;
  return ps.some((p) => p.status === 'playing' || p.status === 'final') ? 'LIVE PTS' : 'PROJ PTS';
}

function RosterTab({ team, onChange, after }: { team: TeamPage; onChange?: () => void; after?: ReactNode }) {
  const lineup = team.lineup;
  const editing = !!lineup?.enabled;
  const [showBench, setShowBench] = useState(editing && team.is_me);
  const [picking, setPicking] = useState<Picking>(null);
  const { starters, bench, taxi, ir } = team.roster;
  const week = team.week?.players ?? {};
  const slotChip = (i: number) => editing && lineup ? (
    <SlotChip label={lineup.slots[i]?.label ?? ''} locked={lineup.slots[i]?.locked} onPress={() => setPicking({ kind: 'slot', index: i })} />
  ) : undefined;
  const benchChip = (p: Player) => editing ? (
    <SlotChip label="BN" bench locked={lineup?.locked.includes(p.player_id)} onPress={() => setPicking({ kind: 'bench', player: p })} />
  ) : undefined;
  return (
    <>
      {editing && lineup ? (
        <LineupSheet lineup={lineup} players={[...starters, ...bench]} picking={picking} onClose={() => setPicking(null)} onSaved={() => onChange?.()} />
      ) : null}
      <Card title="Starters" right={pointsLabel(team.week)}>
        {starters.length ? starters.map((p, i) => (
          <PlayerRow key={p.player_id || `empty-${i}`} p={p} first={i === 0} showSlot chip={slotChip(i)} w={week[p.player_id]} season={team.season}
            onEmpty={editing ? () => setPicking({ kind: 'slot', index: i }) : undefined} />
        )) : <Note>{lineup ? 'No lineup set yet.' : 'No lineup set in Sleeper.'}</Note>}
        {bench.length ? (
          <Pressable onPress={() => setShowBench(!showBench)} hitSlop={8}>
            <Text style={[ui.link, styles.center]}>{showBench ? 'Hide bench' : `Bench (${bench.length}) ›`}</Text>
          </Pressable>
        ) : null}
      </Card>
      {showBench ? (
        <Card title="Bench">
          {bench.map((p, i) => <PlayerRow key={p.player_id} p={p} first={i === 0} chip={benchChip(p)} w={week[p.player_id]} season={team.season} />)}
        </Card>
      ) : null}
      {after}
      <View style={styles.pair}>
        <Card style={styles.half}>
          <Text style={styles.eyebrow}>TAXI</Text>
          {taxi.length ? taxi.map((p) => (
            <View key={p.player_id} style={styles.mini}>
              <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
              <Text style={ui.sub}>{p.pos} · {money(p.salary)} cap</Text>
            </View>
          )) : <Text style={ui.sub}>Empty</Text>}
        </Card>
        <Card style={styles.half}>
          <Text style={styles.eyebrow}>IR</Text>
          {ir.length ? ir.map((p) => (
            <View key={p.player_id} style={styles.mini}>
              <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
              <Text style={ui.sub}>{p.pos} · {money(p.salary)}</Text>
            </View>
          )) : <Text style={ui.sub}>1 spot open</Text>}
          {team.is_me ? (
            <Pressable onPress={() => go('/ir')} hitSlop={8}>
              <Text style={[ui.link, styles.manage]}>{ir.length ? 'Remove / swap ›' : 'Move a player ›'}</Text>
            </Pressable>
          ) : null}
        </Card>
      </View>
      <Note>{team.roster.count} players under contract</Note>
      <HeadToHead team={team} />
      {!team.is_me ? (
        <Pressable style={styles.button} onPress={() => Linking.openURL(`https://sleeper.com/leagues/${team.sleeper_league_id}`)}>
          <Text style={styles.buttonText}>Trade in Sleeper ↗</Text>
        </Pressable>
      ) : null}
    </>
  );
}

const POS_ORDER = ['QB', 'RB', 'WR', 'TE'];
const posRank = (pos: string) => (POS_ORDER.includes(pos) ? POS_ORDER.indexOf(pos) : POS_ORDER.length);

// Every contract by position (QB, RB, WR, TE, then anyone else), biggest salary first within each, with its value (PPG per cap dollar).
function ContractsTab({ team }: { team: TeamPage }) {
  const { dead_cap } = team.contracts;
  const all = [...(team.contracts.all ?? [])].sort((a, b) => posRank(a.pos) - posRank(b.pos) || b.contract_salary - a.contract_salary);
  return (
    <>
      <Card
        title={team.is_me ? 'Your contracts' : 'Contracts'}
        right={
          <Pressable onPress={() => go('/league-cap')} hitSlop={8}>
            <Text style={ui.link}>Whole league ›</Text>
          </Pressable>
        }
      >
        {all.length === 0 ? <Note>No contracts yet.</Note> : null}
        {all.map((p, i) => {
          const tag = p.designation === 'taxi' ? 'TAXI' : p.designation === 'ir' ? 'IR' : p.is_rookie ? 'ROOKIE' : null;
          const years = Math.max(p.years_left, 1);
          return (
            <Row key={p.player_id} first={i === 0} onPress={() => go(`/player/${p.player_id}`)}>
              <Face p={p} label={p.pos} />
              <View style={ui.flex}>
                <View style={styles.nameLine}>
                  <Text style={[ui.name, styles.shrink]} numberOfLines={1}>{p.name}</Text>
                  {tag ? <Pill label={tag} tone={tag === 'IR' ? 'out' : 'gold'} /> : null}
                </View>
                <Text style={[ui.sub, styles.subGap]}>{money(p.contract_salary)} · {years} yr{years === 1 ? '' : 's'}</Text>
              </View>
              <View style={ui.right}>
                <Text style={styles.cPpg}>{p.ppg != null ? p.ppg.toFixed(1) : '—'}<Text style={styles.ppgUnit}> PPG</Text></Text>
                <Text style={styles.perDollar}>{p.per_dollar != null ? `${p.per_dollar.toFixed(2)} PPG / $` : '— PPG / $'}</Text>
              </View>
            </Row>
          );
        })}
      </Card>
      {dead_cap.length ? (
        <Card title="Dead cap" right={<Text style={[ui.value, ui.gold]}>{money(dead_cap.reduce((s, d) => s + d.amount, 0))}</Text>}>
          {dead_cap.map((d, i) => (
            <Row
              key={`${d.player}-${i}`}
              first={i === 0}
              // Opens the drop (or trade, for retained salary) this charge came from.
              onPress={d.player_id ? () => go({ pathname: '/activity', params: { player: d.player_id!, team: team.team_id, source: d.source ?? 'drop' } }) : undefined}
            >
              <Text style={[ui.value, ui.flex]}>{d.player}</Text>
              <Text style={ui.value}>{d.years_left ? `${deadYears(d)} · ` : ''}{money(d.amount)}</Text>
              {d.player_id ? <Text style={ui.sub}>›</Text> : null}
            </Row>
          ))}
        </Card>
      ) : null}
    </>
  );
}

function deadYears(d: { years_left?: number; more?: boolean }) {
  const n = d.years_left ?? 1;
  return `${n}${d.more ? '+' : ''} ${n === 1 && !d.more ? 'yr' : 'yrs'}`;
}

const ROUND = ['', '1st', '2nd', '3rd', '4th', '5th'];

function PicksTab({ team }: { team: TeamPage }) {
  const years = [...new Set(team.picks.owned.map((p) => p.year))];
  const away = team.picks.traded_away;
  return (
    <>
      {years.length === 0 ? <Note>No draft picks on record.</Note> : null}
      {years.map((year) => {
        const picks = team.picks.owned.filter((p) => p.year === year);
        return (
          <Card key={year} title={`${year} draft`} right={`${picks.length} pick${picks.length === 1 ? '' : 's'}`}>
            {picks.map((p: Pick, i) => (
              <Row key={`${p.round}-${p.original_owner}`} first={i === 0}>
                <Pos label={ROUND[p.round] ?? `R${p.round}`} />
                <View style={ui.flex}>
                  <Text style={ui.name}>{year} {ROUND[p.round] ?? `Round ${p.round}`}</Text>
                  <Text style={ui.sub}>{p.own ? 'Own' : `from ${p.original_owner}`}</Text>
                </View>
              </Row>
            ))}
          </Card>
        );
      })}
      {away.length ? (
        <Note>Traded away: {away.map((p) => `${p.year} ${ROUND[p.round] ?? p.round} (to ${p.current_owner})`).join(', ')}</Note>
      ) : null}
    </>
  );
}

// Other teams: cap space for the next three seasons as rings (filled = cap used), dead cap under them.
// "All contracts" opens the full contract list and dead cap rows in place.
function CapCard({ team }: { team: TeamPage }) {
  const [open, setOpen] = useState(false);
  const dead = team.contracts.dead_cap;
  const deadTotal = dead.reduce((s, d) => s + d.amount, 0);
  const toggle = () => setOpen(!open);
  return (
    <>
      <Card
        title="Cap by year"
        right={
          <Pressable onPress={toggle} hitSlop={8}>
            <Text style={styles.smallLink}>{open ? 'HIDE CONTRACTS' : 'ALL CONTRACTS ›'}</Text>
          </Pressable>
        }
      >
        <View style={styles.rings}>
          {team.cap.by_year.slice(0, 3).map((y) => (
            <View key={y.season} style={styles.ringCol}>
              <CapRing used={team.salary_cap ? y.used / team.salary_cap : 0} label={dollars(y.space)} red={y.space < 0} />
              <Text style={ui.sub}>{y.season}</Text>
            </View>
          ))}
        </View>
        <Pressable onPress={dead.length ? toggle : undefined} style={styles.deadLine}>
          <Text style={[ui.sub, ui.flex]}>Dead cap</Text>
          <Text style={styles.deadValue}>
            {dead.length ? `${dollars(deadTotal)} · ${dead.length} player${dead.length === 1 ? '' : 's'}${open ? '' : ' ›'}` : 'None'}
          </Text>
        </Pressable>
      </Card>
      {open ? <ContractsTab team={team} /> : null}
    </>
  );
}

const RING = 64;
const RING_W = 6;

// A ring filled clockwise from the top by `used` (0 to 1), drawn from two clipped half circles (no SVG in the app).
function CapRing({ used, label, red }: { used: number; label: string; red?: boolean }) {
  const deg = Math.max(0, Math.min(1, used)) * 360;
  const color = red ? '#E08A8A' : colors.gold;
  const arc = { borderTopColor: color, borderRightColor: color };
  return (
    <View style={styles.ring}>
      <View style={[styles.ringHalf, { left: RING / 2 }]}>
        <View style={[styles.ringArc, arc, { left: -RING / 2, transform: [{ rotate: `${45 + Math.min(deg, 180) - 180}deg` }] }]} />
      </View>
      <View style={[styles.ringHalf, { left: 0 }]}>
        <View style={[styles.ringArc, arc, { left: 0, transform: [{ rotate: `${225 + Math.max(deg - 180, 0) - 180}deg` }] }]} />
      </View>
      <Text style={[styles.ringText, red && { color }]}>{label}</Text>
    </View>
  );
}

// Other teams: every pick they own as a chip (year + round, and whose it was).
function PicksCard({ team }: { team: TeamPage }) {
  const owned = team.picks.owned;
  const away = team.picks.traded_away;
  return (
    <Card title="Picks" right={<Text style={[ui.value, ui.gold]}>{owned.length}</Text>}>
      {owned.length === 0 ? <Note>No draft picks on record.</Note> : null}
      <View style={styles.chips}>
        {owned.map((p) => (
          <View key={`${p.year}-${p.round}-${p.original_owner}`} style={styles.chip}>
            <Text style={styles.chipTop}>{p.year} {ROUND[p.round] ?? `R${p.round}`}</Text>
            <Text style={styles.chipSub}>{p.own ? 'own' : `from ${firstName(p.original_owner)}`}</Text>
          </View>
        ))}
      </View>
      {away.length ? (
        <Text style={[ui.sub, styles.subGap]}>Traded away: {away.map((p) => `${p.year} ${ROUND[p.round] ?? p.round} (to ${firstName(p.current_owner)})`).join(', ')}</Text>
      ) : null}
    </Card>
  );
}

// Your record against this owner, under their roster (other teams only).
function HeadToHead({ team }: { team: TeamPage }) {
  const h2h = team.head_to_head;
  if (!h2h) return null;
  return (
    <Card
      title="Head to head with you"
      right={<Text style={[ui.value, ui.gold]}>{h2h.wins > h2h.losses ? 'You lead' : h2h.wins < h2h.losses ? 'They lead' : 'Even'} {record(h2h.wins, h2h.losses, h2h.ties)}</Text>}
    >
      <Text style={ui.sub}>
        Last meeting: Week {h2h.last.week}, you {h2h.last.me >= h2h.last.them ? 'won' : 'lost'} {h2h.last.me.toFixed(1)} to {h2h.last.them.toFixed(1)}
      </Text>
    </Card>
  );
}

const styles = StyleSheet.create({
  smallLink: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1 },
  rings: { flexDirection: 'row', justifyContent: 'space-around', paddingVertical: 6 },
  ringCol: { alignItems: 'center', gap: 6 },
  ring: { width: RING, height: RING, borderRadius: RING / 2, borderWidth: RING_W, borderColor: '#22352B', alignItems: 'center', justifyContent: 'center' },
  ringHalf: { position: 'absolute', top: -RING_W, width: RING / 2, height: RING, overflow: 'hidden', marginLeft: -RING_W },
  ringArc: { position: 'absolute', top: 0, width: RING, height: RING, borderRadius: RING / 2, borderWidth: RING_W, borderColor: 'transparent' },
  ringText: { color: colors.text, fontSize: 14, fontWeight: '800' },
  deadLine: { flexDirection: 'row', alignItems: 'center', borderTopWidth: 1, borderTopColor: '#22352B', marginTop: 10, paddingTop: 10 },
  deadValue: { color: colors.gold, fontSize: 13, fontWeight: '700' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { backgroundColor: '#1A2E24', borderRadius: 10, paddingVertical: 7, paddingHorizontal: 10 },
  chipTop: { color: colors.text, fontSize: 13, fontWeight: '800' },
  chipSub: { color: colors.muted, fontSize: 10, fontWeight: '600', marginTop: 1 },
  nameLine: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  shrink: { flexShrink: 1 },
  subGap: { marginTop: 3 },
  bigSalary: { color: colors.gold, fontSize: 18, fontWeight: '800' },
  perDollar: { color: colors.gold, fontSize: 11, fontWeight: '700', marginTop: 2 },
  cPpg: { color: colors.text, fontSize: 18, fontWeight: '800' },
  tabs: { flexDirection: 'row', gap: 22, borderBottomWidth: 1, borderBottomColor: '#22352B', marginBottom: 12, marginTop: 2 },
  tab: { paddingVertical: 8 },
  tabOn: { borderBottomWidth: 3, borderBottomColor: colors.gold, marginBottom: -1 },
  tabText: { color: colors.muted, fontSize: 13, fontWeight: '800', letterSpacing: 0.6 },
  tabTextOn: { color: colors.text },
  prow: { paddingVertical: 10, paddingHorizontal: 4, borderTopWidth: 1, borderTopColor: '#22352B' },
  prowFirst: { borderTopWidth: 0 },
  prowLive: { borderLeftWidth: 3, borderLeftColor: colors.live, backgroundColor: 'rgba(76,195,138,0.08)', borderRadius: 6 },
  pmain: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  game: { color: colors.muted, fontSize: 11.5, marginTop: 3 },
  gameLive: { color: colors.text },
  gameRow: { flexDirection: 'row', alignItems: 'center', gap: 5, marginTop: 3 },
  dot: { width: 7, height: 7, borderRadius: 4, backgroundColor: colors.live },
  clock: { color: colors.live, fontWeight: '800' },
  finalWord: { fontWeight: '800' },
  ball: { width: 13, height: 8, borderRadius: 6, backgroundColor: '#9A5A2B', justifyContent: 'center', alignItems: 'center' },
  lace: { width: 5, height: 1, backgroundColor: '#fff' },
  statLine: { color: '#D9D0BD', fontSize: 11.5, fontWeight: '600', marginTop: 3, lineHeight: 15 },
  good: { color: colors.live, fontWeight: '800' },
  bad: { color: '#EF7A7A', fontWeight: '800' },
  big: { color: colors.text, fontSize: 21, fontWeight: '800' },
  bigLive: { color: colors.live },
  bigPre: { color: '#CFC6B4' },
  under: { color: colors.muted, fontSize: 10, fontWeight: '700', marginTop: 2 },
  foot: { flexDirection: 'row', justifyContent: 'space-between', gap: 8, backgroundColor: '#0B1D15', borderRadius: 8, paddingVertical: 4, paddingHorizontal: 8, marginTop: 8 },
  footText: { color: colors.muted, fontSize: 10.5, flexShrink: 1 },
  footGold: { color: colors.gold, fontWeight: '800' },
  ppgUnit: { color: colors.muted, fontSize: 10, fontWeight: '600' },
  center: { textAlign: 'center', marginTop: 10 },
  pair: { flexDirection: 'row', gap: 8 },
  half: { flex: 1 },
  eyebrow: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1.2, marginBottom: 6 },
  mini: { marginTop: 2, marginBottom: 4 },
  manage: { marginTop: 6, fontSize: 12 },
  year: { width: 44 },
  left: { width: 84, textAlign: 'right' },
  button: { marginTop: 12, borderWidth: 1, borderColor: colors.gold, borderRadius: 14, padding: 14, alignItems: 'center' },
  buttonText: { color: colors.gold, fontWeight: '800', fontSize: 15 },
  emptyFace: { width: 44, height: 44, borderRadius: 22, borderWidth: 1, borderStyle: 'dashed', borderColor: colors.muted, alignItems: 'center', justifyContent: 'center' },
  emptyPlus: { color: colors.muted, fontSize: 20, fontWeight: '700' },
  emptyName: { color: colors.muted },
});
