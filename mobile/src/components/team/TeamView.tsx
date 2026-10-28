import { router, useFocusEffect } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';

import type { Pick, Player, TeamPage } from '../../lib/api';
import { colors } from '../../lib/theme';
import { PlayerFace } from '../PlayerFace';
import { Card, Note, Pill, Pos, Row, Segmented, SwipeTabs, Tile, Tiles, Title, money, ordinal, record, ui } from '../ui';

const TABS = ['Roster', 'Contracts', 'Picks', 'Activity'] as const;

// Set when a team page opens a page of its own, so returning from it keeps the tab.
let openedHere = false;
function go(path: Parameters<typeof router.push>[0]) {
  openedHere = true;
  router.push(path);
}

// One owner's team with the same four tabs as My Team. Other owners' teams
// are read only; trades are made in Sleeper.
export function TeamView({ team, eyebrow }: { team: TeamPage; eyebrow?: string }) {
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
  const next = team.cap.by_year[1];
  const r = team.record;

  return (
    <>
      <Title eyebrow={eyebrow ?? team.owner_name.toUpperCase()}>{team.team_name}</Title>
      <Tiles>
        <Tile label="RECORD" value={record(r.wins, r.losses, r.ties)} sub={`${ordinal(r.rank)} · ${r.standing_points} SP`} />
        <Tile label="PPG" value={r.pf_per_game != null ? r.pf_per_game.toFixed(1) : '—'} sub="avg / game" />
        <Tile
          label="CAP SPACE"
          value={money(team.cap.space)}
          tone={team.cap.space < 0 ? 'red' : undefined}
          sub={next ? `${money(next.space)} in ${next.season}` : undefined}
        />
      </Tiles>
      <Segmented options={TABS} value={tab} onChange={setTab} />
      <SwipeTabs
        options={TABS}
        value={tab}
        onChange={setTab}
        render={(t) =>
          t === 'Roster' ? <RosterTab team={team} /> : t === 'Contracts' ? <ContractsTab team={team} /> : t === 'Picks' ? <PicksTab team={team} /> : <ActivityTab team={team} />
        }
      />
    </>
  );
}

// Short injury labels so the tag doesn't crowd the name.
const INJURY_SHORT: Record<string, string> = { questionable: 'Q', doubtful: 'D', out: 'OUT', ir: 'IR', pup: 'PUP', sus: 'SUS' };

function Face({ p, label, hurt }: { p: Player; label: string; hurt?: boolean }) {
  return <PlayerFace name={p.name} team={p.nfl_team} pos={label} hurt={hurt} />;
}

// Cap used for one season as a ring of ticks, with space left in the middle.
const TICKS = 48;
function CapRing({ season, used, space, cap }: { season: number; used: number; space: number; cap: number }) {
  const filled = Math.round(Math.max(0, Math.min(1, used / cap)) * TICKS);
  return (
    <View style={styles.ringWrap}>
      <View style={styles.ring}>
        {Array.from({ length: TICKS }, (_, i) => (
          <View
            key={i}
            style={[styles.tick, { transform: [{ rotate: `${(i * 360) / TICKS}deg` }, { translateY: -RING / 2 + 6 }] }, i < filled && styles.tickOn, space < 0 && i < filled && { backgroundColor: colors.danger }]}
          />
        ))}
        <Text style={[styles.ringValue, space < 0 && { color: colors.danger }]}>{money(space)}</Text>
        <Text style={styles.ringLeft}>left</Text>
      </View>
      <Text style={styles.ringYear}>{season}</Text>
    </View>
  );
}

function ContractRow({ p, first, sub }: { p: Player; first?: boolean; sub: string }) {
  return (
    <Row first={first} onPress={() => go(`/player/${p.player_id}`)}>
      <Face p={p} label={p.pos} />
      <View style={ui.flex}>
        <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
        <Text style={[ui.sub, styles.subGap]}>{sub}</Text>
      </View>
      <Text style={styles.bigSalary}>{money(p.contract_salary)}</Text>
    </Row>
  );
}

function PlayerRow({ p, first, showSlot }: { p: Player; first?: boolean; showSlot?: boolean }) {
  const injury = p.injury ? INJURY_SHORT[p.injury.toLowerCase()] ?? p.injury.toUpperCase() : null;
  const tag = injury ? <Pill label={injury} tone="out" /> : p.is_rookie ? <Pill label="ROOKIE" tone="gold" /> : null;
  const label = showSlot && p.slot ? p.slot : p.pos;
  const years = p.no_contract ? 0 : p.years_left;
  return (
    <Row first={first} onPress={() => go(`/player/${p.player_id}`)}>
      <Face p={p} label={label} hurt={!!injury} />
      <View style={ui.flex}>
        <View style={styles.nameLine}>
          <Text style={[ui.name, styles.shrink]} numberOfLines={1}>{p.name}</Text>
          {tag}
        </View>
        <View style={styles.subLine}>
          <Text style={ui.sub}>{p.nfl_team || 'FA'}</Text>
          {p.no_contract ? (
            <Text style={ui.sub}>no contract</Text>
          ) : (
            <>
              <View style={styles.pips}>
                {Array.from({ length: Math.min(5, Math.max(3, years)) }, (_, i) => (
                  <View key={i} style={[styles.pip, i < years && styles.pipOn]} />
                ))}
              </View>
              <Text style={ui.sub}>{years} yr{years === 1 ? '' : 's'}</Text>
            </>
          )}
        </View>
      </View>
      <View style={ui.right}>
        <Text style={styles.ppg}>
          {p.ppg != null ? p.ppg.toFixed(1) : '—'}
          <Text style={styles.ppgUnit}> PPG</Text>
        </Text>
        {p.no_contract ? null : <Text style={styles.salary}>{money(p.contract_salary)}</Text>}
      </View>
    </Row>
  );
}

function RosterTab({ team }: { team: TeamPage }) {
  const [showBench, setShowBench] = useState(false);
  const { starters, bench, taxi, ir } = team.roster;
  return (
    <>
      <Card title="Starters" right="PPG · SALARY">
        {starters.length ? starters.map((p, i) => <PlayerRow key={p.player_id} p={p} first={i === 0} showSlot />) : <Note>No lineup set in Sleeper.</Note>}
        {bench.length ? (
          <Pressable onPress={() => setShowBench(!showBench)} hitSlop={8}>
            <Text style={[ui.link, styles.center]}>{showBench ? 'Hide bench' : `Bench (${bench.length}) ›`}</Text>
          </Pressable>
        ) : null}
      </Card>
      {showBench ? (
        <Card title="Bench">
          {bench.map((p, i) => <PlayerRow key={p.player_id} p={p} first={i === 0} />)}
        </Card>
      ) : null}
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
      {!team.is_me ? (
        <Pressable style={styles.button} onPress={() => Linking.openURL(`https://sleeper.com/leagues/${team.sleeper_league_id}`)}>
          <Text style={styles.buttonText}>Trade in Sleeper ↗</Text>
        </Pressable>
      ) : null}
    </>
  );
}

function ContractsTab({ team }: { team: TeamPage }) {
  const { biggest, expiring, dead_cap } = team.contracts;
  // Every deal ending this season, then the biggest deals that run longer.
  const soon = expiring.filter((p) => !p.no_contract).sort((a, b) => b.contract_salary - a.contract_salary);
  const locked = biggest.filter((p) => p.years_left > 1);
  return (
    <>
      <Card
        title="Cap by year"
        right={
          <Pressable onPress={() => go('/league-cap')} hitSlop={8}>
            <Text style={ui.link}>Whole league ›</Text>
          </Pressable>
        }
      >
        <View style={styles.rings}>
          {team.cap.by_year.slice(0, 3).map((y) => (
            <CapRing key={y.season} season={y.season} used={y.used} space={y.space} cap={team.salary_cap} />
          ))}
        </View>
      </Card>
      {soon.length ? (
        <Card style={styles.expiringCard}>
          <View style={styles.groupHead}>
            <Text style={[styles.groupTitle, { color: '#E3C47A' }]}>EXPIRING AFTER {team.season}</Text>
            <Text style={ui.sub}>{money(soon.reduce((s, p) => s + p.contract_salary, 0))} frees up</Text>
          </View>
          {soon.map((p, i) => <ContractRow key={p.player_id} p={p} first={i === 0} sub={`${p.nfl_team || 'FA'} · ${p.pos}`} />)}
        </Card>
      ) : null}
      {locked.length ? (
        <Card>
          <View style={styles.groupHead}>
            <Text style={[styles.groupTitle, { color: colors.gold }]}>LOCKED IN</Text>
            <Text style={ui.sub}>{money(locked.reduce((s, p) => s + p.contract_salary, 0))}</Text>
          </View>
          {locked.map((p, i) => <ContractRow key={p.player_id} p={p} first={i === 0} sub={`${p.nfl_team || 'FA'} · ${p.pos} · through ${p.through}`} />)}
        </Card>
      ) : null}
      {dead_cap.length ? (
        <Card title="Dead cap" right={<Text style={[ui.value, ui.gold]}>{money(dead_cap.reduce((s, d) => s + d.amount, 0))}</Text>}>
          {dead_cap.map((d, i) => (
            <Row key={`${d.player}-${i}`} first={i === 0}>
              <Text style={[ui.value, ui.flex]}>{d.player}</Text>
              <Text style={ui.value}>{d.years_left ? `${deadYears(d)} · ` : ''}{money(d.amount)}</Text>
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

function ActivityTab({ team }: { team: TeamPage }) {
  const h2h = team.head_to_head;
  return (
    <>
      <Card>
        {team.activity.length === 0 ? <Note>No signings or releases yet.</Note> : null}
        {team.activity.map((a, i) => (
          <Row key={`${a.player}-${a.at}-${i}`} first={i === 0}>
            <Pill label={a.kind} tone={a.kind === 'SIGN' ? 'live' : 'out'} />
            <Text style={[ui.value, ui.flex]}>{a.kind === 'SIGN' ? 'Signed' : 'Released'} {a.player}</Text>
            <Text style={ui.sub}>{a.at ? new Date(a.at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : ''}</Text>
          </Row>
        ))}
      </Card>
      {h2h ? (
        <Card
          title="Head to head with you"
          right={<Text style={[ui.value, ui.gold]}>{h2h.wins > h2h.losses ? 'You lead' : h2h.wins < h2h.losses ? 'They lead' : 'Even'} {record(h2h.wins, h2h.losses, h2h.ties)}</Text>}
        >
          <Text style={ui.sub}>
            Last meeting: Week {h2h.last.week}, you {h2h.last.me >= h2h.last.them ? 'won' : 'lost'} {h2h.last.me.toFixed(1)} to {h2h.last.them.toFixed(1)}
          </Text>
        </Card>
      ) : null}
    </>
  );
}

const RING = 84;

const styles = StyleSheet.create({
  nameLine: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  shrink: { flexShrink: 1 },
  subGap: { marginTop: 3 },
  bigSalary: { color: colors.gold, fontSize: 18, fontWeight: '800' },
  rings: { flexDirection: 'row', justifyContent: 'space-around', paddingTop: 6 },
  ringWrap: { alignItems: 'center' },
  ring: { width: RING, height: RING, alignItems: 'center', justifyContent: 'center' },
  tick: { position: 'absolute', left: RING / 2 - 2, top: RING / 2 - 5, width: 4, height: 10, borderRadius: 2, backgroundColor: colors.card },
  tickOn: { backgroundColor: colors.gold },
  ringValue: { color: colors.gold, fontSize: 15, fontWeight: '800' },
  ringLeft: { color: colors.muted, fontSize: 10 },
  ringYear: { color: colors.text, fontSize: 12, fontWeight: '700', marginTop: 6 },
  expiringCard: { borderColor: '#6B5524', backgroundColor: '#17190F' },
  groupHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 2 },
  groupTitle: { fontSize: 11, fontWeight: '800', letterSpacing: 1.6 },
  subLine: { flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 3 },
  pips: { flexDirection: 'row', gap: 3 },
  pip: { width: 6, height: 6, borderRadius: 3, backgroundColor: '#33402F' },
  pipOn: { backgroundColor: colors.gold },
  ppg: { color: colors.text, fontSize: 19, fontWeight: '800' },
  ppgUnit: { color: colors.muted, fontSize: 10, fontWeight: '600' },
  salary: { color: colors.gold, fontSize: 13, fontWeight: '700', marginTop: 1 },
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
});
