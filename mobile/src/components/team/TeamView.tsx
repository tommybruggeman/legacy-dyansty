import { router, useFocusEffect } from 'expo-router';
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { Dimensions, Modal, Pressable, ScrollView, StyleSheet, Text, View, type ScrollViewInstance } from 'react-native';

import type { Pick, Player, TeamPage, TeamWeek, TeamWeekItem, WeekPlayer } from '../../lib/api';
import { colors } from '../../lib/theme';
import { PlayerFace } from '../PlayerFace';
import { LineupSheet, type Picking, SlotChip } from './LineupSheet';
import { Card, Note, Pill, Row, SwipeTabs, firstName, money, ordinal, record, ui } from '../ui';

const TABS = ['Roster', 'Contracts', 'Picks'] as const;

// Set when a team page opens a page of its own, so returning from it keeps the tab.
let openedHere = false;
function go(path: Parameters<typeof router.push>[0]) {
  openedHere = true;
  router.push(path);
}

// While any of this team's players is on the field, the page refreshes this often (10 s, same as every live page).
export { LIVE_POLL_MS as LIVE_REFRESH_MS } from '../../lib/api';
export const isLive = (team: TeamPage | null) => !!team?.week && !team.week.is_final && Object.values(team.week.players).some((p) => p.status === 'playing');

// One owner's team with the same three tabs as My Team (league activity lives on League > Teams). Other owners' teams
// are read only apart from the Trade pill next to the name.
export function TeamView({ team, eyebrow, badge, top, onChange, onWeek }: { team: TeamPage; eyebrow?: string; badge?: ReactNode; top?: ReactNode; onChange?: () => void; onWeek?: (week: number | null) => void }) {
  const [tab, setTab] = useState<(typeof TABS)[number]>('Roster');
  // Always open on the roster. Coming back from a player or cap page opened here keeps the tab;
  // leaving for another part of the app and coming back starts over.
  const opened = useRef(false);
  useFocusEffect(useCallback(() => {
    const back = opened.current;
    opened.current = false;
    if (!back) {
      setTab('Roster');
      onWeek?.(null); // My Team always opens on this week
    }
    return () => { opened.current = openedHere; openedHere = false; };
  }, []));
  useEffect(() => setTab('Roster'), [team.team_id]);
  // Other owners' teams are one page (no tabs), so a swipe there only ever means back to the teams list.
  if (!team.is_me) {
    return (
      <>
        {/* Same header as My Team (Tommy, 2026-11-17); the slim box is your head to head with them. */}
        <MyHeader team={team} badge={badge} badgeOnCap />
        <HeadToHeadBar team={team} />
        {top}
        <RosterTab team={team} onChange={onChange} after={<><CapCard team={team} /><PicksCard team={team} /></>} />
      </>
    );
  }
  return (
    <>
      {/* My Team (Tommy, 2026-11-17): team name, record and cap left as a subtitle, standings spot in the circle,
          then this week's matchup as one slim bar. */}
      <MyHeader team={team} badge={badge} />
      <MatchupBar team={team} />
      {top}
      <UnderlineTabs value={tab} onChange={setTab} />
      <SwipeTabs
        options={TABS}
        value={tab}
        onChange={setTab}
        render={(t) =>
          t === 'Roster' ? <RosterTab team={team} onChange={onChange} onWeek={onWeek} /> : t === 'Contracts' ? <ContractsTab team={team} /> : <PicksTab team={team} />
        }
      />
    </>
  );
}

// Other teams' Trade pill sits beside the cap left line, like Healthy on the player page (Tommy, 2026-11-17).
function MyHeader({ team, badge, badgeOnCap }: { team: TeamPage; badge?: ReactNode; badgeOnCap?: boolean }) {
  const r = team.record;
  const now = team.cap.by_year.find((y) => y.season === team.season) ?? team.cap.by_year[0];
  const space = now?.space ?? team.cap.space;
  return (
    <View style={styles.hdr}>
      <View style={ui.flex}>
        <View style={styles.nameLine}>
          <Text style={styles.teamName} numberOfLines={1}>{team.team_name}</Text>
          {badgeOnCap ? null : badge}
        </View>
        <Text style={styles.hdrSub}>{record(r.wins, r.losses, r.ties)} · {r.standing_points} SP</Text>
        <View style={styles.capLine}>
          <Text style={[styles.hdrSub, space < 0 ? styles.over : styles.capLeft]}>{space < 0 ? `${dollars(-space)} over the cap` : `${dollars(space)} cap left`}</Text>
          {badgeOnCap ? badge : null}
        </View>
      </View>
      {r.rank ? (
        <View style={styles.rank}>
          <Text style={styles.rankNum}>{ordinal(r.rank)}</Text>
          <Text style={styles.rankWord}>PLACE</Text>
        </View>
      ) : null}
    </View>
  );
}

// "Wk 11 vs Nick   118.4 – 112.0  58%": projections before kickoff, the score once games start.
function MatchupBar({ team }: { team: TeamPage }) {
  const w = team.week;
  if (!w?.opponent) return null;
  const started = w.is_final || Object.values(w.players).some((p) => p.status === 'playing' || p.status === 'final');
  const mine = started ? w.score : w.projected;
  const theirs = started ? w.opponent.score : w.opponent.projected;
  // The server sends a whole percent (83), not a fraction.
  const pct = w.win_chance != null ? Math.round(w.win_chance) : null;
  return (
    <View style={styles.mbar}>
      <Text style={styles.mbarLeft} numberOfLines={1}>Wk {w.week} vs <Text style={styles.mbarName}>{firstName(w.opponent.name)}</Text></Text>
      <Text style={styles.mbarRight}>
        <Text style={styles.mbarName}>{mine != null ? mine.toFixed(1) : '—'}</Text> – {theirs != null ? theirs.toFixed(1) : '—'}
        {w.is_final ? <Text style={styles.mbarFinal}>  {w.score > w.opponent.score ? 'W' : w.score < w.opponent.score ? 'L' : 'T'}</Text>
          : pct != null ? <Text style={styles.mbarPct}>  {pct}%</Text> : null}
      </Text>
    </View>
  );
}

// The quiet week button next to Starters and its menu: past results, this week (gold), later opponents, playoffs.
// The menu is one fixed size (about six weeks tall) and scrolls inside, with no scroll bar, opening on the week
// being shown (Tommy, 2026-11-17).
const MENU_H = 300;
const ITEM_H = 48;
const MENU_PAD = 8;
function WeekButton({ team, onWeek }: { team: TeamPage; onWeek: (week: number | null) => void }) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState({ top: 0, right: 16, height: MENU_H });
  const ref = useRef<any>(null); // RN View instance (measureInWindow)
  const shown = team.week?.week ?? team.current_week ?? 1;
  const items = team.weeks ?? [];
  // Never runs past the tab bar on a short screen.
  const show = () => ref.current?.measureInWindow((_x: number, y: number, _w: number, h: number) => {
    const top = y + h + 6;
    setAt({ top, right: 16, height: Math.max(160, Math.min(MENU_H, Dimensions.get('window').height - top - 110)) });
    setOpen(true);
  });
  const index = Math.max(0, items.findIndex((it) => it.week === shown));
  const full = items.length * ITEM_H + MENU_PAD * 2;
  // The shown week sits in the middle of the box when it opens.
  const offset = Math.max(0, Math.min(full - at.height, MENU_PAD + index * ITEM_H - (at.height - ITEM_H) / 2));
  const list = useRef<ScrollViewInstance>(null);
  return (
    <>
      <Pressable ref={ref} onPress={show} hitSlop={8} style={styles.wk}>
        <Text style={styles.wkText}>Wk {shown} ▾</Text>
      </Pressable>
      <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
        <Pressable style={StyleSheet.absoluteFill} onPress={() => setOpen(false)} />
        <View style={[styles.menu, { top: at.top, right: at.right, height: Math.min(at.height, full) }]}>
          <ScrollView
            ref={list}
            showsVerticalScrollIndicator={false}
            contentContainerStyle={styles.menuList}
            contentOffset={{ x: 0, y: offset }}
            onLayout={() => list.current?.scrollTo({ y: offset, animated: false })}
          >
            {items.map((it: TeamWeekItem) => {
              const now = it.week === team.current_week;
              const picked = it.week === shown && !now;
              const sub = it.playoffs ? 'Playoffs'
                : it.result && it.score ? `${it.result} ${it.score[0].toFixed(1)} – ${it.score[1].toFixed(1)}${it.opponent ? ` vs ${firstName(it.opponent)}` : ''}`
                : it.opponent ? `vs ${firstName(it.opponent)}` : 'No game';
              return (
                <Pressable key={it.week} onPress={() => { setOpen(false); onWeek(now ? null : it.week); }} style={[styles.mi, now && styles.miNow, picked && styles.miPicked]}>
                  <Text style={[styles.miWeek, now && styles.gold, it.playoffs && !now && styles.miPlayoff]}>Wk {it.week}</Text>
                  <Text style={styles.miSub} numberOfLines={1}>{sub}</Text>
                </Pressable>
              );
            })}
          </ScrollView>
        </View>
      </Modal>
    </>
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
  // Short tags (R rookie, T taxi, IR) next to the injury tag.
  const role = p.designation === 'ir' ? 'IR' : p.designation === 'taxi' ? 'T' : p.is_rookie ? 'R' : null;
  const tag = (
    <>
      {role ? <Pill label={role} tone={role === 'IR' ? 'out' : 'gold'} /> : null}
      {injury && injury !== role ? <Pill label={injury} tone="out" /> : null}
    </>
  );
  const label = showSlot && p.slot ? p.slot : p.pos;
  const live = w?.status === 'playing';
  return (
    // A hurt player opens straight to the Health section of his page.
    <Pressable onPress={() => go(injury ? `/player/${p.player_id}?section=health` : `/player/${p.player_id}`)}>
      <View style={[styles.prow, first && styles.prowFirst, live && styles.prowLive]}>
        <View style={styles.pmain}>
          {chip}
          <Face p={p} label={chip ? p.pos : label} hurt={!!injury} />
          <View style={ui.flex}>
            <View style={styles.nameLine}>
              <Text style={[ui.name, styles.shrink]} numberOfLines={1}>{p.name}</Text>
              {tag}
            </View>
            <GameText p={p} w={w} season={season} />
            {season != null && !kickedOff(w) && w?.status !== 'bye' ? <Text style={styles.deal} numberOfLines={1}>{contractText(p, season)}</Text> : null}
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
      </View>
    </Pressable>
  );
}

// Line 1, the game: kickoff time before, the clock and score (with a football when his team has the ball) during,
// the result after. The contract sits on its own second line until kickoff (Tommy, 2026-11-17).
// On a bye the contract stays on the same line as BYE (Tommy, 2026-11-17).
function GameText({ p, w, season }: { p: Player; w?: WeekPlayer; season?: number }) {
  const g = w?.game;
  if (!w) return <Text style={ui.sub}>{p.nfl_team || 'FA'}</Text>;
  if (w.status === 'bye') return <Text style={styles.game}><Text style={styles.byeWord}>BYE</Text>{season != null ? <Text style={styles.byeDeal}> · {contractText(p, season)}</Text> : null}</Text>;
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

// Has his game kicked off? From then on the row shows only the game and his stats, no contract.
const kickedOff = (w?: WeekPlayer) => !!w && (w.status === 'playing' || w.status === 'final' || w.game?.state === 'in' || w.game?.state === 'post');

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
      {w.status !== 'bye' && w.proj != null ? <Text style={styles.projTag}>proj</Text> : null}
    </View>
  );
}

// "$48 · thru 2027" (or "No contract").
function contractText(p: Player, season: number) {
  if (p.gone) return 'Not on your team now';
  if (p.no_contract) return 'No contract';
  return `${dollars(p.contract_salary)} · thru ${season + Math.max(p.years_left, 1) - 1}`;
}

// "$48" for whole dollars, "$12.5" otherwise.
const dollars = (n: number) => `${n < 0 ? '-' : ''}$${Number.isInteger(n) ? Math.abs(n) : Math.abs(n).toFixed(1)}`;

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

function RosterTab({ team, onChange, after, onWeek }: { team: TeamPage; onChange?: () => void; after?: ReactNode; onWeek?: (week: number | null) => void }) {
  const lineup = team.lineup;
  const editing = !!lineup?.enabled;
  const [showBench, setShowBench] = useState(editing && team.is_me);
  const [picking, setPicking] = useState<Picking>(null);
  const { starters, taxi, ir } = team.roster;
  const mine = team.is_me;
  // Other teams: taxi and IR players join the bench (tagged T and IR) instead of their own cards.
  const bench = mine ? team.roster.bench : [...team.roster.bench, ...taxi, ...ir].filter((p, i, all) => all.findIndex((q) => q.player_id === p.player_id) === i);
  const week = team.week?.players ?? {};
  // Every row gets its slot tag; it's tappable only when this lineup is set in the app.
  const slotChip = (i: number) => editing && lineup ? (
    <SlotChip label={lineup.slots[i]?.label ?? ''} locked={lineup.slots[i]?.locked} onPress={() => setPicking({ kind: 'slot', index: i })} />
  ) : <SlotChip label={starters[i]?.slot || starters[i]?.pos || ''} />;
  const benchChip = (p: Player) => editing ? (
    <SlotChip label="BN" bench locked={lineup?.locked.includes(p.player_id)} onPress={() => setPicking({ kind: 'bench', player: p })} />
  ) : <SlotChip label="BN" bench />;
  return (
    <>
      {editing && lineup ? (
        <LineupSheet lineup={lineup} players={[...starters, ...bench]} picking={picking} onClose={() => setPicking(null)} onSaved={() => onChange?.()}
          game={(p) => <GameText p={p} w={week[p.player_id]} />} points={(p) => <Points p={p} w={week[p.player_id]} />} />
      ) : null}
      <Card title="Starters" right={onWeek && team.weeks?.length ? <WeekButton team={team} onWeek={onWeek} /> : pointsLabel(team.week)}>
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
      {mine ? (
        <>
        <View style={styles.pair}>
          <Card style={styles.half}>
            <Text style={styles.eyebrow}>TAXI</Text>
            {taxi.length ? taxi.map((p) => (
              <Pressable key={p.player_id} style={styles.mini} onPress={() => go(`/player/${p.player_id}`)} hitSlop={4}>
                <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
                <Text style={ui.sub}>{p.pos} · {money(p.salary)} cap</Text>
              </Pressable>
            )) : <Text style={ui.sub}>Empty</Text>}
          </Card>
          <Card style={styles.half}>
            <Text style={styles.eyebrow}>IR</Text>
            {ir.length ? ir.map((p) => (
              <Pressable key={p.player_id} style={styles.mini} onPress={() => go(`/player/${p.player_id}`)} hitSlop={4}>
                <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
                <Text style={ui.sub}>{p.pos} · {money(p.salary)}</Text>
              </Pressable>
            )) : <Text style={ui.sub}>1 spot open</Text>}
            {team.is_me ? (
              <Pressable onPress={() => go('/ir')} hitSlop={8}>
                <Text style={[ui.link, styles.manage]}>{ir.length ? 'Remove / swap ›' : 'Move a player ›'}</Text>
              </Pressable>
            ) : null}
          </Card>
        </View>
        <Note>{team.roster.count} players under contract</Note>
        </>
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
      {team.is_me ? <CapBars team={team} /> : null}
      <Card
        title="Contracts"
        right={
          <Pressable onPress={() => go('/league-cap')} hitSlop={8}>
            <Text style={ui.link}>Whole league ›</Text>
          </Pressable>
        }
      >
        {all.length === 0 ? <Note>No contracts yet.</Note> : null}
        {all.map((p, i) => {
          const tag = p.designation === 'taxi' ? 'T' : p.designation === 'ir' ? 'IR' : p.is_rookie ? 'R' : null;
          const left = Math.max(p.years_left, 1);
          return (
            <Row key={p.player_id} first={i === 0} onPress={() => go(`/player/${p.player_id}`)}>
              <Face p={p} label={p.pos} />
              <View style={ui.flex}>
                <View style={styles.nameLine}>
                  <Text style={[ui.name, styles.shrink]} numberOfLines={1}>{p.name}</Text>
                  <YearDots left={left} total={Math.max(p.total_years ?? 0, left)} />
                  {tag ? <Pill label={tag} tone={tag === 'IR' ? 'out' : 'gold'} /> : null}
                </View>
                <Text style={[ui.sub, styles.subGap]}>{p.pos} · {p.ppg != null ? `${p.ppg.toFixed(1)} PPG` : 'no games yet'}</Text>
              </View>
              <View style={ui.right}>
                <Text style={styles.cSalary}>{dollars(p.contract_salary)}</Text>
                <Text style={styles.under}>thru {p.through}</Text>
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

// Filled dots = years left, all the dots = the whole contract (Tommy, 2026-11-17).
function YearDots({ left, total }: { left: number; total: number }) {
  return (
    <View style={styles.dots}>
      {Array.from({ length: Math.min(total, 6) }, (_, i) => <View key={i} style={[styles.ydot, i >= left && styles.ydotOff]} />)}
    </View>
  );
}

// This season and the next two as thin bars of the cap.
function CapBars({ team }: { team: TeamPage }) {
  const cap = team.salary_cap || 1;
  return (
    <Card title="Cap by year" right={`of ${dollars(team.salary_cap)}`}>
      {team.cap.by_year.slice(0, 3).map((y) => (
        <View key={y.season} style={styles.yr}>
          <Text style={styles.yrLabel}>{y.season}</Text>
          <View style={styles.track}><View style={[styles.fill, { width: `${Math.max(0, Math.min(1, y.used / cap)) * 100}%` }, y.space < 0 && styles.fillOver]} /></View>
          <Text style={[styles.yrText, y.space < 0 && styles.over]}>{y.space < 0 ? `${dollars(-y.space)} over` : `${dollars(y.space)} left`}</Text>
        </View>
      ))}
    </Card>
  );
}

function deadYears(d: { years_left?: number; more?: boolean }) {
  const n = d.years_left ?? 1;
  return `${n}${d.more ? '+' : ''} ${n === 1 && !d.more ? 'yr' : 'yrs'}`;
}

const ROUND = ['', '1st', '2nd', '3rd', '4th', '5th'];

// One row per pick: its slot (projected for next year's draft, from the standings), where it came from; picks
// traded away stay in the list, dimmed, with who has them now.
function PicksTab({ team }: { team: TeamPage }) {
  const all = [...team.picks.owned.map((p) => ({ p, gone: false })), ...team.picks.traded_away.map((p) => ({ p, gone: true }))];
  const years = [...new Set(all.map((x) => x.p.year))].sort();
  return (
    <>
      {years.length === 0 ? <Note>No draft picks on record.</Note> : null}
      {years.map((year) => {
        const rows = all.filter((x) => x.p.year === year).sort((a, b) => a.p.round - b.p.round || (a.p.slot ?? '').localeCompare(b.p.slot ?? ''));
        const next = year === team.season + 1;
        return (
          <Card key={year} title={next ? `${year} Rookie Draft` : String(year)} right={next ? 'projected slot' : `slot set after ${year - 1}`}>
            {rows.map(({ p, gone }: { p: Pick; gone: boolean }, i) => (
              <Row key={`${p.round}-${p.original_owner}-${gone}`} first={i === 0}>
                <View style={[styles.slot, p.own && !gone && styles.slotOwn, gone && styles.slotGone]}>
                  <Text style={[styles.slotText, p.own && !gone && styles.slotTextOwn]}>{p.slot ?? `R${p.round}`}</Text>
                </View>
                <View style={[ui.flex, gone && styles.dim]}>
                  <Text style={ui.name}>Round {p.round}</Text>
                  <Text style={ui.sub}>{gone ? `Traded to ${firstName(p.current_owner)}` : p.own ? 'Your own pick' : `From ${firstName(p.original_owner)}`}</Text>
                </View>
              </Row>
            ))}
          </Card>
        );
      })}
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
      {[...new Set(owned.map((p) => p.year))].sort().map((year) => (
        <View key={year} style={styles.pickYear}>
          <Text style={styles.pickYearLabel}>{year}</Text>
          <View style={[styles.chips, ui.flex]}>
            {owned.filter((p) => p.year === year).map((p) => (
              <View key={`${p.year}-${p.round}-${p.original_owner}`} style={styles.chip}>
                <Text style={styles.chipTop}>{ROUND[p.round] ?? `R${p.round}`}</Text>
                {/* Every chip the same size: round over the original owner, no "from" (Tommy, 2026-11-17). */}
                <Text style={styles.chipSub} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.8}>{p.own ? 'Own' : firstName(p.original_owner)}</Text>
              </View>
            ))}
          </View>
        </View>
      ))}
      {away.length ? (
        <Text style={[ui.sub, styles.subGap]}>Traded away: {away.map((p) => `${p.year} ${ROUND[p.round] ?? p.round} (to ${firstName(p.current_owner)})`).join(', ')}</Text>
      ) : null}
    </Card>
  );
}

// Your record against this owner and your last meeting, in the slim box under their header (other teams only).
function HeadToHeadBar({ team }: { team: TeamPage }) {
  const h2h = team.head_to_head;
  if (!h2h) return null;
  const lead = h2h.wins > h2h.losses ? 'You lead' : h2h.wins < h2h.losses ? 'They lead' : 'Even';
  const won = h2h.last.me >= h2h.last.them;
  return (
    <View style={[styles.mbar, styles.h2h]}>
      <View style={styles.h2hLine}>
        <Text style={styles.mbarLeft}>Head to head with you</Text>
        <Text style={styles.mbarName}>{lead} {record(h2h.wins, h2h.losses, h2h.ties)}</Text>
      </View>
      <View style={[styles.h2hLine, styles.h2hLast]}>
        <Text style={styles.h2hSub}>Last: Wk {h2h.last.week}, you {won ? 'won' : 'lost'}</Text>
        <Text style={styles.h2hSub}><Text style={styles.mbarName}>{h2h.last.me.toFixed(1)}</Text> – {h2h.last.them.toFixed(1)}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  hdr: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 12 },
  teamName: { color: colors.text, fontSize: 26, fontWeight: '900', flexShrink: 1 },
  hdrSub: { color: colors.muted, fontSize: 13, marginTop: 2 },
  capLeft: { color: colors.gold, fontWeight: '600' },
  capLine: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  over: { color: '#EF7A7A', fontWeight: '700' },
  rank: { width: 52, height: 52, borderRadius: 26, borderWidth: 2, borderColor: colors.gold, alignItems: 'center', justifyContent: 'center' },
  rankNum: { color: colors.gold, fontSize: 17, fontWeight: '900' },
  rankWord: { color: colors.muted, fontSize: 8, fontWeight: '800', letterSpacing: 0.8 },
  mbar: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, borderWidth: 1, borderColor: '#22352B', borderRadius: 14, paddingVertical: 10, paddingHorizontal: 12, marginBottom: 10, backgroundColor: '#0B2118' },
  mbarLeft: { color: colors.muted, fontSize: 13.5, flexShrink: 1 },
  mbarName: { color: colors.text, fontWeight: '800' },
  mbarRight: { color: colors.muted, fontSize: 13.5 },
  mbarPct: { color: colors.live, fontWeight: '800' },
  mbarFinal: { color: colors.gold, fontWeight: '900' },
  h2h: { flexDirection: 'column', alignItems: 'stretch', gap: 0 },
  h2hLine: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  h2hLast: { borderTopWidth: 1, borderTopColor: '#22352B', marginTop: 6, paddingTop: 6 },
  h2hSub: { color: colors.muted, fontSize: 12 },
  wk: { borderWidth: 1, borderColor: '#2F4136', borderRadius: 8, paddingVertical: 3, paddingHorizontal: 9 },
  wkText: { color: '#A9A493', fontSize: 12, fontWeight: '700' },
  menu: { position: 'absolute', width: 250, backgroundColor: '#10291F', borderWidth: 1, borderColor: colors.gold, borderRadius: 16, overflow: 'hidden', shadowColor: '#000', shadowOpacity: 0.6, shadowRadius: 20, shadowOffset: { width: 0, height: 12 } },
  menuList: { padding: MENU_PAD },
  mi: { height: ITEM_H, justifyContent: 'center', paddingHorizontal: 8, borderRadius: 10 },
  miPicked: { borderWidth: 1, borderColor: '#2F4136' },
  miNow: { backgroundColor: 'rgba(200,155,74,0.18)' },
  miWeek: { color: colors.text, fontSize: 13.5, fontWeight: '800' },
  miPlayoff: { color: colors.live },
  miSub: { color: colors.muted, fontSize: 11.5, marginTop: 1 },
  gold: { color: colors.gold },
  deal: { color: colors.muted, fontSize: 11.5, marginTop: 2 },
  byeDeal: { color: colors.muted },
  byeWord: { color: '#EF7A7A', fontWeight: '800' },
  dots: { flexDirection: 'row', gap: 3, marginLeft: 2 },
  ydot: { width: 6, height: 6, borderRadius: 3, backgroundColor: colors.gold },
  ydotOff: { backgroundColor: '#2F4136' },
  cSalary: { color: colors.gold, fontSize: 18, fontWeight: '900' },
  yr: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 6 },
  yrLabel: { color: colors.text, fontSize: 13, fontWeight: '800', width: 40 },
  track: { flex: 1, height: 6, borderRadius: 3, backgroundColor: '#22352B', overflow: 'hidden' },
  fill: { height: '100%', backgroundColor: colors.gold },
  fillOver: { backgroundColor: '#EF7A7A' },
  yrText: { color: colors.muted, fontSize: 12, width: 78, textAlign: 'right' },
  slot: { minWidth: 46, height: 30, borderRadius: 8, borderWidth: 1, borderColor: colors.gold, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 6 },
  slotOwn: { backgroundColor: colors.gold },
  slotGone: { borderColor: '#2F4136' },
  slotText: { color: colors.gold, fontSize: 12.5, fontWeight: '900' },
  slotTextOwn: { color: '#071A12' },
  dim: { opacity: 0.5 },
  smallLink: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1 },
  rings: { flexDirection: 'row', justifyContent: 'space-around', paddingVertical: 6 },
  ringCol: { alignItems: 'center', gap: 6 },
  ring: { width: RING, height: RING, borderRadius: RING / 2, borderWidth: RING_W, borderColor: '#22352B', alignItems: 'center', justifyContent: 'center' },
  ringHalf: { position: 'absolute', top: -RING_W, width: RING / 2, height: RING, overflow: 'hidden', marginLeft: -RING_W },
  ringArc: { position: 'absolute', top: 0, width: RING, height: RING, borderRadius: RING / 2, borderWidth: RING_W, borderColor: 'transparent' },
  ringText: { color: colors.text, fontSize: 14, fontWeight: '800' },
  deadLine: { flexDirection: 'row', alignItems: 'center', borderTopWidth: 1, borderTopColor: '#22352B', marginTop: 10, paddingTop: 10 },
  deadValue: { color: colors.gold, fontSize: 13, fontWeight: '700' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  pickYear: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, marginTop: 10 },
  pickYearLabel: { color: colors.gold, fontSize: 15, fontWeight: '900', width: 44, paddingTop: 8 },
  chip: { width: '23%', backgroundColor: '#1A2E24', borderRadius: 10, paddingVertical: 7, paddingHorizontal: 4, alignItems: 'center' },
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
  // The small "proj" under a player's points before kickoff (Tommy, 2026-11-17).
  projTag: { color: colors.muted, fontSize: 9, fontWeight: '600', marginTop: 1 },
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
  emptyFace: { width: 44, height: 44, borderRadius: 22, borderWidth: 1, borderStyle: 'dashed', borderColor: colors.muted, alignItems: 'center', justifyContent: 'center' },
  emptyPlus: { color: colors.muted, fontSize: 20, fontWeight: '700' },
  emptyName: { color: colors.muted },
});
