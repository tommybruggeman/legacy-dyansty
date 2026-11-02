import { LinearGradient } from 'expo-linear-gradient';
import { router } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Avatar } from '../Pictures';
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
      {view.games.map((g) => {
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
                <View key={g.m} style={[styles.treeGame, g.mine && styles.treeLit, g.place === 1 && styles.treeCup]}>
                  {g.teams.map((t, k) => <TreeSlot key={k} slot={t} game={g} />)}
                </View>
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
      <Text style={[styles.treeName, slot.is_me && styles.treeMe]} numberOfLines={1}>{first(slot.name)}{champ ? ' 🏆' : ''}</Text>
      {game.state !== 'pre' ? <Text style={styles.treeScore}>{slot.score.toFixed(0)}</Text> : null}
    </View>
  );
}

function GameCard({ game, big, week }: { game: PlayoffGame; big: boolean; week: number }) {
  const body = (
    <>
      {game.state === 'live' ? <View style={styles.liveEdge} /> : null}
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
  const legs = game.weeks.length > 1 && game.state !== 'pre'
    ? game.weeks.map((w, i) => `Wk ${w} ${slot.legs[i] == null ? '–' : (slot.legs[i] as number).toFixed(1)}`).join(' · ')
    : null;
  return (
    <View style={[styles.teamRow, big && styles.bigRow, lost && styles.lost]}>
      <Seed n={slot.seed} />
      <Avatar uri={slot.avatar} name={slot.name} size={big ? 38 : 30} me={slot.is_me} />
      <View style={styles.nameBox}>
        <View style={styles.nameLine}>
          <Text style={styles.teamName} numberOfLines={1}>{first(slot.name)}</Text>
          {slot.is_me ? <Text style={styles.you}>YOU</Text> : null}
          {slot.record ? <Text style={styles.record}>{slot.record}</Text> : null}
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

function Losers({ view }: { view: PlayoffWeek }) {
  return (
    <View style={styles.losers}>
      <View style={styles.losersHead}>
        <Text style={styles.losersTitle}>LOSER BRACKET</Text>
        <Text style={styles.losersCount}>Round {view.round}</Text>
      </View>
      {view.losers.map((g) => {
        const [a, b] = g.teams;
        const open = g.matchup_id != null ? () => router.push(`/game/${view.week}/${g.matchup_id}`) : undefined;
        return (
          <Pressable key={g.m} style={styles.mini} onPress={open} disabled={!open}>
            <MiniSide slot={a} game={g} />
            <Text style={styles.dash}>–</Text>
            <MiniSide slot={b} game={g} right />
          </Pressable>
        );
      })}
      {view.byes.length ? <Text style={styles.bye}>Bye: {view.byes.map((t) => first(t.name)).join(', ')}</Text> : null}
    </View>
  );
}

function MiniSide({ slot, game, right }: { slot: PlayoffSlot; game: PlayoffGame; right?: boolean }) {
  if (!isTeam(slot)) {
    return <Text style={[styles.miniName, styles.placeholder, right && styles.right]} numberOfLines={1}>{slot.placeholder ?? 'TBD'}</Text>;
  }
  const lost = game.winner != null && game.winner !== slot.roster_id;
  const score = game.state === 'pre' ? null : <Text style={styles.miniScore}>{slot.score.toFixed(1)}</Text>;
  const name = <Text style={[styles.miniName, slot.is_me && styles.treeMe, right && styles.right]} numberOfLines={1}>{first(slot.name)}</Text>;
  return (
    <View style={[styles.miniSide, right && styles.miniRight, lost && styles.lost]}>
      {right ? <>{score}{name}<Seed n={slot.seed} small /></> : <><Seed n={slot.seed} small />{name}{score}</>}
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
  liveEdge: { position: 'absolute', left: 0, top: 0, bottom: 0, width: 4, backgroundColor: colors.live, zIndex: 1 },
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
  bye: { color: colors.muted, opacity: 0.6, fontSize: 11, marginTop: 6 },
});
