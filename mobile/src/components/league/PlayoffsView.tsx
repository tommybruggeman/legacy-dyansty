import { LinearGradient } from 'expo-linear-gradient';
import { router } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';
import { Pressable } from '../Pressable';

import { Avatar } from '../Pictures';
import { teamLabel } from '../ui';
import type { PlayoffGame, PlayoffSlot, PlayoffTeam, PlayoffWeek } from '../../lib/api';
import { colors } from '../../lib/theme';

const first = (name: string) => name.split(' ')[0];
const isTeam = (s: PlayoffSlot): s is PlayoffTeam => 'roster_id' in s;
const weeksText = (w: number[]) => (w.length > 1 ? `WK ${w[0]}-${w[w.length - 1]}` : `WK ${w[0]}`);

// Playoff weeks on League > Matchups (Tommy picked design B, 2026-11-02): a mini bracket on top with your path lit,
// then this round's games (your game and the title game in gold), then the loser bracket as compact rows.
// Rounds can last two weeks; scores are the round total with each week underneath.
export function PlayoffsView({ view }: { view: PlayoffWeek }) {
  const legText = view.legs > 1 ? ` · WEEK ${view.leg} OF ${view.legs}` : '';
  return (
    <View style={styles.wrap}>
      {view.projected ? (
        <Text style={styles.note}>If the season ended today. Seeds lock after week {view.start - 1}.</Text>
      ) : null}
      <Tree view={view} />
      {/* The first round lives only in the bracket above (tap a game there); cards start with the final. */}
      {view.round > 1 && view.games.map((g) => {
        // Gold for the title game and for your game on the road to it (a 3rd-place game just gets the gold border).
        const big = g.place === 1 || (g.mine && g.place == null);
        const title = g.place === 1 ? `🏆 ${g.label.toUpperCase()}` : g.mine ? `YOUR ${g.label.toUpperCase()}` : g.label.toUpperCase();
        return (
          <View key={g.m}>
            <Text style={[styles.label, big && styles.bigLabel]}>{title}{legText}</Text>
            <GameCard game={g} big={big} week={view.week} />
          </View>
        );
      })}
      {view.losers.length ? <Losers view={view} /> : null}
    </View>
  );
}

function Tree({ view }: { view: PlayoffWeek }) {
  const rounds = [...new Set(view.tree.map((g) => g.round))].sort((a, b) => a - b);
  const last = rounds[rounds.length - 1];
  return (
    <View style={styles.tree}>
      {rounds.map((r, i) => {
        // The bracket shows the road to the title only (the 3rd-place game lives in the cards below).
        const games = view.tree.filter((g) => g.round === r && (r !== last || g.place === 1 || g.place == null));
        return (
          <View key={r} style={styles.treeRow}>
            {i > 0 ? <View style={styles.connector} /> : null}
            <View style={styles.treeCol}>
              <Text style={styles.treeHead}>{r === last ? 'FINAL' : games[0]?.label.toUpperCase() + 'S'} · {weeksText(games[0]?.weeks ?? [])}</Text>
              {games.map((g) => (
                <Pressable key={g.m} disabled={g.matchup_id == null || g.state === 'pre'}
                  onPress={() => router.push(`/game/${view.week}/${g.matchup_id}`)}
                  style={[styles.treeGame, g.mine && styles.treeLit, g.place === 1 && styles.treeCup]}>
                  {g.teams.map((t, k) => <TreeSlot key={k} slot={t} game={g} />)}
                </Pressable>
              ))}
            </View>
          </View>
        );
      })}
      <Text style={styles.trophy}>🏆</Text>
    </View>
  );
}

function TreeSlot({ slot, game }: { slot: PlayoffSlot; game: PlayoffGame }) {
  if (!isTeam(slot)) return <Text style={styles.treePlaceholder} numberOfLines={1}>{slot.placeholder ?? 'TBD'}</Text>;
  const lost = game.winner != null && game.winner !== slot.roster_id;
  const champ = game.place === 1 && game.state === 'final' && game.winner === slot.roster_id;
  return (
    <View style={[styles.treeSlot, lost && styles.lost]}>
      <Seed n={slot.seed} small />
      <Text style={[styles.treeName, slot.is_me && styles.treeMe]} numberOfLines={1}>{teamLabel(slot, true)}{champ ? ' 🏆' : ''}</Text>
      {game.state !== 'pre' || slot.proj != null ? (
        <View style={styles.treeSc}>
          <Text style={[styles.treeScore, game.state === 'pre' && styles.scPre]}>{slot.score.toFixed(1)}</Text>
          {slot.proj != null ? <Text style={styles.treePj}>{slot.proj.toFixed(1)}</Text> : null}
        </View>
      ) : null}
    </View>
  );
}

function GameCard({ game, big, week }: { game: PlayoffGame; big: boolean; week: number }) {
  const body = (
    <>
      {game.teams.map((t, i) => <TeamRow key={i} slot={t} game={game} big={big} />)}
    </>
  );
  const open = game.matchup_id != null ? () => router.push(`/game/${week}/${game.matchup_id}`) : undefined;
  const style = [styles.card, game.mine && styles.myCard];
  return (
    <Pressable onPress={open} disabled={!open}>
      {big ? <LinearGradient colors={['#2C2512', '#132D20']} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={style}>{body}</LinearGradient>
        : <View style={style}>{body}</View>}
    </Pressable>
  );
}

function TeamRow({ slot, game, big }: { slot: PlayoffSlot; game: PlayoffGame; big: boolean }) {
  if (!isTeam(slot)) {
    return (
      <View style={styles.teamRow}>
        <Seed n={null} />
        <Text style={styles.placeholder}>{slot.placeholder ?? 'To be decided'}</Text>
      </View>
    );
  }
  const lost = game.winner != null && game.winner !== slot.roster_id;
  const weeks = game.weeks.length > 1 && game.state !== 'pre'
    ? game.weeks.map((w, i) => `Wk ${w} ${slot.legs[i] == null ? '–' : (slot.legs[i] as number).toFixed(1)}`).join(' · ')
    : null;
  const legs = [weeks, game.state !== 'final' && slot.proj != null ? `proj ${slot.proj.toFixed(1)}` : null].filter(Boolean).join(' · ') || null;
  return (
    <View style={[styles.teamRow, big && styles.bigRow, lost && styles.lost]}>
      <Seed n={slot.seed} />
      <Avatar uri={slot.avatar} name={teamLabel(slot)} size={big ? 38 : 30} me={slot.is_me} />
      <View style={styles.nameBox}>
        <View style={styles.nameLine}>
          <Text style={styles.teamName} numberOfLines={1}>{teamLabel(slot, true)}</Text>
          {slot.is_me ? <Text style={styles.you}>YOU</Text> : null}
        </View>
        {legs ? <Text style={styles.legs}>{legs}</Text> : null}
      </View>
      <View style={styles.scoreLine}>
        <Text style={[styles.score, game.state === 'pre' && styles.scorePre]}>{slot.score.toFixed(2)}</Text>
        {game.winner === slot.roster_id ? <Text style={styles.winArrow}>◀</Text> : null}
      </View>
    </View>
  );
}

// Seeds 5-10 (Tommy, 2026-11-22): one row per game this week, owner names with the score and a faint projection
// under it, "vs" in the middle. Games that decide where both teams go next sit together in a faded box headed by
// what's at stake; placement games name their place; the two bye teams get a "BYE WEEK · No winner" row since
// their scores still count (Shotgun Watch, top 5).
function Losers({ view }: { view: PlayoffWeek }) {
  const allPlace = view.losers.every((g) => g.place != null);
  const sub = view.byes.length ? `${view.byes.map((t) => first(t.name)).join(' and ')} on a bye` : '';
  const groups: { stakes: NonNullable<PlayoffGame['stakes']>; games: PlayoffGame[] }[] = [];
  for (const g of view.losers) {
    if (!g.stakes) continue;
    const same = groups.find((x) => x.stakes.win === g.stakes!.win && x.stakes.lose === g.stakes!.lose);
    if (same) same.games.push(g); else groups.push({ stakes: g.stakes, games: [g] });
  }
  const rest = view.losers.filter((g) => !g.stakes);
  return (
    <View style={styles.cons}>
      <View style={styles.losersHead}>
        <Text style={styles.consTitle}>CONSOLATION BRACKET</Text>
        <Text style={styles.losersCount}>Seeds {view.losers.length ? Math.min(...view.losers.flatMap((g) => g.teams.filter(isTeam).map((t) => t.seed ?? 99)), ...view.byes.map((t) => t.seed ?? 99)) : ''}–{Math.max(0, ...view.losers.flatMap((g) => g.teams.filter(isTeam).map((t) => t.seed ?? 0)), ...view.byes.map((t) => t.seed ?? 0))}</Text>
      </View>
      <View style={styles.cHead}>
        <Text style={styles.cHeadMain}>WEEK {view.week} · {allPlace ? 'PLACE GAMES' : `ROUND ${view.week - view.start + 1}`}</Text>
        {sub ? <Text style={styles.cHeadSub}>{sub}</Text> : null}
      </View>
      {groups.map((grp, i) => (
        <View key={i} style={styles.cBox}>
          <View style={styles.cGrp}>
            <Text style={styles.cGrpWin}>{grp.stakes.win.toUpperCase()}</Text>
            <Text style={styles.cGrpLose}>{grp.stakes.lose}</Text>
          </View>
          {grp.games.map((g) => <ConsRow key={g.m} game={g} week={view.week} />)}
        </View>
      ))}
      {rest.map((g) => <ConsRow key={g.m} game={g} week={view.week} />)}
      {view.byes.length === 2 ? <ByeRow teams={view.byes} week={view.week} final={view.losers.length > 0 && view.losers.every((g) => g.state === 'final')} /> : null}
    </View>
  );
}

// A score with its projection faint underneath (before kickoff the score is a grey 0.0).
function ScoreBox({ score, proj, state, lead, right }: { score: number; proj?: number | null; state: PlayoffGame['state']; lead: boolean; right?: boolean }) {
  return (
    <View style={[styles.scBox, right && styles.scBoxRight]}>
      <Text style={[styles.sbSc, lead && styles.sbWin, state === 'pre' && styles.scPre]}>{score.toFixed(1)}</Text>
      {proj != null ? <Text style={styles.pj}>{proj.toFixed(1)}</Text> : null}
    </View>
  );
}

function ConsRow({ game, week }: { game: PlayoffGame; week: number }) {
  const [a, b] = game.teams;
  const open = game.matchup_id != null && game.state !== 'pre' ? () => router.push(`/game/${week}/${game.matchup_id}`) : undefined;
  const side = (slot: PlayoffSlot, i: number) => {
    const right = i === 1;
    if (!isTeam(slot)) return <View style={[styles.sbSide, right && styles.sbRight]} />;
    const other = game.teams[1 - i];
    const won = game.state === 'final' && game.winner === slot.roster_id;
    const lead = won || (game.state === 'live' && isTeam(other) && slot.score > other.score);
    const lost = game.state === 'final' && !won;
    const name = <Text key="n" style={[styles.nm, won && styles.nmWin, slot.is_me && styles.nmMe]} numberOfLines={1}>{first(slot.name)}</Text>;
    const box = <ScoreBox key="s" score={slot.score} proj={slot.proj} state={game.state} lead={lead} right={right} />;
    return <View style={[styles.sbSide, right && styles.sbRight, lost && styles.lost]}>{right ? [box, name] : [name, box]}</View>;
  };
  const status = game.state === 'final' ? <Text style={styles.st}>FINAL</Text> : game.state === 'live' ? <Text style={[styles.st, styles.stLive]}>● LIVE</Text> : null;
  return (
    <Pressable onPress={open} disabled={!open} style={[styles.sb, game.mine && styles.mineRow]}>
      {side(a, 0)}
      <View style={styles.sbMid}>
        {game.place != null ? <Text style={styles.stk}>{game.label.toUpperCase()}</Text> : null}
        <Text style={styles.sbVs}>vs</Text>
        {status}
      </View>
      {side(b, 1)}
    </Pressable>
  );
}

function ByeRow({ teams, week, final }: { teams: PlayoffTeam[]; week: number; final: boolean }) {
  const mine = teams.some((t) => t.is_me);
  const state: PlayoffGame['state'] = final ? 'final' : teams.some((t) => t.score > 0) ? 'live' : 'pre';
  return (
    <View style={[styles.sb, styles.byeRow, mine && styles.mineRow]}>
      {teams.map((t, i) => {
        const right = i === 1;
        const name = <Text key="n" style={[styles.nm, t.is_me && styles.nmMe]} numberOfLines={1}>{first(t.name)}</Text>;
        const box = <ScoreBox key="s" score={t.score} proj={t.proj} state={state} lead={false} right={right} />;
        const side = <View key={t.roster_id} style={[styles.sbSide, right && styles.sbRight]}>{right ? [box, name] : [name, box]}</View>;
        return i === 0 ? [side, (
          <View key="mid" style={styles.sbMid}>
            <Text style={styles.stk}>BYE WEEK</Text>
            <Text style={styles.stkP}>No winner</Text>
          </View>
        )] : side;
      })}
    </View>
  );
}

function Seed({ n, small }: { n: number | null; small?: boolean }) {
  return (
    <View style={[styles.seed, small && styles.seedSmall]}>
      <Text style={[styles.seedText, small && styles.seedTextSmall]}>{n ?? '–'}</Text>
    </View>
  );
}

const PJ = '#6F8478'; // the faint projection under a score

const styles = StyleSheet.create({
  wrap: { gap: 10 },
  note: { color: colors.muted, fontSize: 12, opacity: 0.8, marginHorizontal: 2 },
  label: { color: colors.gold, fontSize: 10.5, fontWeight: '800', letterSpacing: 1.3, marginHorizontal: 4, marginBottom: 6 },
  bigLabel: { color: '#E2BC5B', fontSize: 11.5 },
  tree: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#0C1F17', borderRadius: 16, padding: 10, gap: 6 },
  treeRow: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 6 },
  treeCol: { flex: 1, gap: 6 },
  treeHead: { color: colors.muted, fontSize: 9, fontWeight: '800', letterSpacing: 1 },
  treeGame: { backgroundColor: colors.card, borderRadius: 10, paddingHorizontal: 6, paddingVertical: 4, borderWidth: 1, borderColor: '#1D3328' },
  treeLit: { borderColor: colors.gold },
  treeCup: { backgroundColor: '#2A2414' },
  treeSlot: { flexDirection: 'row', alignItems: 'center', gap: 5, paddingVertical: 3 },
  treeName: { color: colors.muted, fontSize: 11.5, fontWeight: '700', flexShrink: 1 },
  treeMe: { color: colors.text },
  treeScore: { color: colors.text, fontSize: 11.5, fontWeight: '800', marginLeft: 'auto' },
  treePlaceholder: { color: colors.muted, opacity: 0.55, fontSize: 11, paddingVertical: 6 },
  connector: { width: 10, height: 60, borderWidth: 2, borderLeftWidth: 0, borderColor: '#2B3A2F', borderTopRightRadius: 8, borderBottomRightRadius: 8 },
  trophy: { fontSize: 22 },
  card: { borderRadius: 16, backgroundColor: colors.row, overflow: 'hidden', borderWidth: 1, borderColor: '#1B2E24' },
  myCard: { borderColor: colors.gold, borderWidth: 1.5 },
  teamRow: { flexDirection: 'row', alignItems: 'center', paddingLeft: 16, paddingRight: 14, paddingTop: 10, paddingBottom: 8, gap: 10 },
  bigRow: { paddingTop: 13, paddingBottom: 11 },
  lost: { opacity: 0.5 },
  nameBox: { flexShrink: 1, flex: 1 },
  nameLine: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  teamName: { color: colors.text, fontSize: 16, fontWeight: '800', flexShrink: 1 },
  you: { color: colors.gold, fontSize: 9.5, fontWeight: '800', letterSpacing: 0.6, backgroundColor: '#2A2A1A', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 6, overflow: 'hidden' },
  record: { color: colors.muted, opacity: 0.6, fontSize: 11, fontWeight: '600' },
  legs: { color: colors.muted, opacity: 0.6, fontSize: 10.5, fontWeight: '600', marginTop: 1 },
  scoreLine: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  score: { color: colors.text, fontSize: 21, fontWeight: '900' },
  scorePre: { opacity: 0.4 },
  winArrow: { color: colors.text, fontSize: 10 },
  placeholder: { color: colors.muted, opacity: 0.6, fontSize: 14, fontWeight: '700' },
  seed: { width: 24, height: 24, borderRadius: 12, alignItems: 'center', justifyContent: 'center', backgroundColor: '#173225', borderWidth: 1, borderColor: 'rgba(200,155,74,0.5)' },
  seedSmall: { width: 20, height: 20, borderRadius: 10, borderColor: '#2B3A2F' },
  seedText: { color: colors.gold, fontSize: 11, fontWeight: '900' },
  seedTextSmall: { color: colors.muted, fontSize: 10 },
  losers: { backgroundColor: '#0C1F17', borderRadius: 14, paddingHorizontal: 12, paddingVertical: 10, marginTop: 2 },
  losersHead: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 },
  losersTitle: { color: colors.muted, fontSize: 10.5, fontWeight: '800', letterSpacing: 1.3 },
  losersCount: { color: colors.muted, opacity: 0.6, fontSize: 10.5, fontWeight: '600' },
  mini: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 7, borderBottomWidth: 1, borderBottomColor: '#18291F' },
  miniSide: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 6 },
  miniRight: { justifyContent: 'flex-end' },
  miniName: { color: colors.muted, fontSize: 12.5, fontWeight: '700', flexShrink: 1 },
  right: { textAlign: 'right' },
  miniScore: { color: colors.text, fontSize: 12.5, fontWeight: '800', marginHorizontal: 4 },
  dash: { color: colors.muted, opacity: 0.5 },
  treeSc: { marginLeft: 'auto', alignItems: 'flex-end', minWidth: 30 },
  treePj: { color: PJ, fontSize: 7.5, fontWeight: '600' },
  scPre: { color: PJ },
  cons: { backgroundColor: colors.row, borderWidth: 1, borderColor: colors.line, borderRadius: 16, padding: 12, marginTop: 2 },
  consTitle: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1.2 },
  cHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 10, marginBottom: 4 },
  cHeadMain: { color: colors.text, fontSize: 10.5, fontWeight: '800', letterSpacing: 1 },
  cHeadSub: { color: colors.muted, opacity: 0.7, fontSize: 10.5 },
  cBox: { backgroundColor: 'rgba(255,255,255,0.03)', borderRadius: 12, paddingHorizontal: 8, paddingTop: 8, paddingBottom: 2, marginVertical: 6 },
  cGrp: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 2 },
  cGrpWin: { color: colors.gold, fontSize: 9.5, fontWeight: '800', letterSpacing: 0.8 },
  cGrpLose: { color: colors.muted, opacity: 0.7, fontSize: 10 },
  sb: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 9, paddingHorizontal: 2, borderTopWidth: 1, borderTopColor: colors.card },
  mineRow: { backgroundColor: 'rgba(200,155,74,0.08)', borderWidth: 1, borderColor: colors.gold, borderRadius: 12, paddingHorizontal: 8, marginVertical: 4 },
  byeRow: { borderStyle: 'dashed', borderTopColor: '#2B3A2F' },
  sbSide: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 6, minWidth: 0 },
  sbRight: { justifyContent: 'flex-end' },
  nm: { color: colors.muted, fontSize: 12.5, fontWeight: '700', flexShrink: 1 },
  nmWin: { color: colors.text },
  nmMe: { color: '#E2BC5B' },
  scBox: { marginLeft: 'auto', marginRight: 10, alignItems: 'flex-end' },
  scBoxRight: { marginLeft: 10, marginRight: 'auto', alignItems: 'flex-start' },
  sbSc: { color: colors.text, fontSize: 13, fontWeight: '800' },
  sbWin: { color: '#E2BC5B' },
  pj: { color: PJ, fontSize: 8.5, fontWeight: '600' },
  sbMid: { width: 64, alignItems: 'center', gap: 1 },
  sbVs: { color: colors.muted, fontSize: 11, fontWeight: '800', letterSpacing: 0.8 },
  stk: { color: colors.gold, fontSize: 9, fontWeight: '800', letterSpacing: 0.8 },
  stkP: { color: colors.muted, opacity: 0.7, fontSize: 9.5 },
  st: { color: colors.muted, fontSize: 8.5, fontWeight: '800', letterSpacing: 0.8 },
  stLive: { color: colors.live },
  bye: { color: colors.muted, opacity: 0.6, fontSize: 11, marginTop: 6 },
});
