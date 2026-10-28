import { router } from 'expo-router';
import { useState } from 'react';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';

import type { Pick, Player, TeamPage } from '../../lib/api';
import { colors } from '../../lib/theme';
import { Bar, Card, Note, Pill, Pos, Row, Segmented, SwipeTabs, Tile, Tiles, Title, money, ordinal, record, ui } from '../ui';

const TABS = ['Roster', 'Contracts', 'Picks', 'Activity'] as const;

// One owner's team with the same four tabs as My Team. Other owners' teams
// are read only; trades are made in Sleeper.
export function TeamView({ team, eyebrow }: { team: TeamPage; eyebrow?: string }) {
  const [tab, setTab] = useState<(typeof TABS)[number]>('Roster');
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
      <SwipeTabs options={TABS} value={tab} onChange={setTab}>
        {tab === 'Roster' ? <RosterTab team={team} /> : null}
        {tab === 'Contracts' ? <ContractsTab team={team} /> : null}
        {tab === 'Picks' ? <PicksTab team={team} /> : null}
        {tab === 'Activity' ? <ActivityTab team={team} /> : null}
      </SwipeTabs>
    </>
  );
}

function PlayerRow({ p, first, showSlot }: { p: Player; first?: boolean; showSlot?: boolean }) {
  const tag = p.injury ? <Pill label={p.injury.toUpperCase()} tone="out" /> : p.is_rookie ? <Pill label="ROOKIE" tone="gold" /> : null;
  return (
    <Row first={first} onPress={() => router.push(`/player/${p.player_id}`)}>
      <Pos label={showSlot && p.slot ? p.slot : p.pos} />
      <View style={ui.flex}>
        <View style={styles.nameLine}>
          <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
          {tag}
        </View>
        <Text style={ui.sub}>
          {p.nfl_team}{p.ppg != null ? ` · ${p.ppg.toFixed(1)} PPG` : ''}
        </Text>
      </View>
      <Text style={ui.value}>{p.no_contract ? 'no contract' : `${p.years_left} · ${money(p.contract_salary)}`}</Text>
    </Row>
  );
}

function RosterTab({ team }: { team: TeamPage }) {
  const [showBench, setShowBench] = useState(false);
  const { starters, bench, taxi, ir } = team.roster;
  return (
    <>
      <Card title="Starters" right="YRS · SALARY">
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
            <Pressable onPress={() => router.push('/ir')} hitSlop={8}>
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
  const { biggest, dead_cap } = team.contracts;
  return (
    <>
      <Card
        title="Cap by year"
        right={
          <Pressable onPress={() => router.push('/league-cap')} hitSlop={8}>
            <Text style={ui.link}>Whole league ›</Text>
          </Pressable>
        }
      >
        {team.cap.by_year.map((y, i) => (
          <Row key={y.season} first={i === 0}>
            <Text style={[ui.value, styles.year]}>{y.season}</Text>
            <View style={ui.flex}><Bar fraction={y.used / team.salary_cap} /></View>
            <Text style={[ui.value, styles.left, y.space < 0 && { color: colors.danger }]}>{money(y.space)} left</Text>
          </Row>
        ))}
      </Card>
      <Card title="Biggest contracts" right="yrs left · salary">
        {biggest.map((p, i) => (
          <Row key={p.player_id} first={i === 0} onPress={() => router.push(`/player/${p.player_id}`)}>
            <Pos label={p.pos} />
            <View style={ui.flex}>
              <View style={styles.nameLine}>
                <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
                {p.years_left <= 1 ? <Pill label="EXPIRING" tone="warn" /> : null}
              </View>
              <Text style={ui.sub}>{p.years_left <= 1 ? `expires after ${team.season}` : `through ${p.through}`}</Text>
            </View>
            <Text style={ui.value}>{p.years_left} · {money(p.contract_salary)}</Text>
          </Row>
        ))}
      </Card>
      {dead_cap.length ? (
        <Card title="Dead cap" right={<Text style={[ui.value, ui.gold]}>{money(dead_cap.reduce((s, d) => s + d.amount, 0))}</Text>}>
          {dead_cap.map((d, i) => (
            <Row key={`${d.player}-${i}`} first={i === 0}>
              <Text style={[ui.value, ui.flex]}>{d.player}</Text>
              <Text style={ui.value}>{money(d.amount)}</Text>
            </Row>
          ))}
        </Card>
      ) : null}
    </>
  );
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

const styles = StyleSheet.create({
  nameLine: { flexDirection: 'row', alignItems: 'center', gap: 6 },
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
