import { LinearGradient } from 'expo-linear-gradient';
import { router } from 'expo-router';
import { useMemo, useState } from 'react';
import { Alert, Linking, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { PlayerFace } from '../../components/PlayerFace';
import { ErrorState, Loading } from '../../components/ScreenState';
import { Card, Note, Pill, Screen, Segmented, SwipeTabs, Title, money, ui } from '../../components/ui';
import { apiSend, type Expiring, type FreeAgents, type RookieDeal, type Target, useApi } from '../../lib/api';
import { teamColors } from '../../lib/nflColors';
import { colors } from '../../lib/theme';
import { useTargets } from '../../lib/targets';

const VIEWS = ['Free Agents', 'Expiring'] as const;
const POSITIONS = ['All', 'QB', 'RB', 'WR', 'TE', 'K'] as const;
const PAGE = 40;

// Browse only: bidding and the rookie draft happen on the website, trades in Sleeper (League > Activity shows them all).
// Players show as tiles ranked by season points, with ☆ to save a transfer target.
export default function Market() {
  const [view, setView] = useState<(typeof VIEWS)[number]>('Free Agents');
  return (
    <Screen>
      <Title>Market</Title>
      <Segmented options={VIEWS} value={view} onChange={setView} />
      <SwipeTabs options={VIEWS} value={view} onChange={setView} render={(v) => (v === 'Free Agents' ? <FreeAgentList /> : <ExpiringList />)} />
    </Screen>
  );
}

function Chips<T extends string>({ options, value, onChange }: { options: readonly T[]; value: T; onChange: (v: T) => void }) {
  return (
    <View style={styles.chips}>
      {options.map((o) => (
        <Pressable key={o} onPress={() => onChange(o)} style={[styles.chip, o === value && styles.chipOn]}>
          <Text style={[styles.chipText, o === value && styles.chipTextOn]}>{o}</Text>
        </Pressable>
      ))}
    </View>
  );
}

const INJURY_SHORT: Record<string, string> = { questionable: 'Q', doubtful: 'D', out: 'OUT', ir: 'IR', pup: 'PUP', sus: 'SUS' };
const injuryLabel = (s: string) => INJURY_SHORT[s.toLowerCase()] ?? s.toUpperCase();
const shortOwner = (name: string) => {
  const [first, last] = name.split(' ');
  return last ? `${first} ${last[0]}.` : first;
};
const ppgText = (n: number | null | undefined) => (n != null ? n.toFixed(1) : '—');
const ptsText = (n: number | null | undefined) => (n != null ? `${Math.round(n)} pts` : '');

// One player as a card tinted in their NFL team's colors: circle, name, a short line, PPG and season points.
function PlayerTile({ id, name, pos, team, ppg, points, line, tag, mine }: {
  id: string; name: string; pos: string; team: string; ppg: number | null; points: number | null;
  line: string; tag?: { label: string; tone: 'out' | 'gold' } | null; mine?: boolean;
}) {
  const { isTarget, toggle } = useTargets();
  const saved = isTarget(id);
  const [fill] = teamColors(team);
  return (
    <Pressable onPress={() => router.push(`/player/${id}`)} style={[styles.tile, (saved || mine) && styles.tileGold]}>
      <LinearGradient colors={[`${fill}CC`, colors.card]} locations={[0, 0.6]} start={{ x: 0, y: 0 }} end={{ x: 0.6, y: 1 }} style={StyleSheet.absoluteFill} />
      <View style={styles.tileTop}>
        <PlayerFace name={name} team={team} pos={pos} size={48} />
        <View style={styles.tileTopRight}>
          {tag ? <Pill label={tag.label} tone={tag.tone} /> : null}
          {mine ? null : (
            <Pressable onPress={() => toggle(id)} hitSlop={10} style={[styles.star, saved && styles.starOn]}>
              <Text style={[styles.starText, saved && styles.starTextOn]}>{saved ? '★' : '☆'}</Text>
            </Pressable>
          )}
        </View>
      </View>
      <Text style={styles.tileName} numberOfLines={1}>{name}</Text>
      <Text style={styles.tileLine} numberOfLines={1}>{line}</Text>
      <View style={styles.tileBottom}>
        <Text style={styles.tilePpg}>{ppgText(ppg)}<Text style={styles.unit}> PPG</Text></Text>
        <Text style={styles.tilePts}>{ptsText(points)}</Text>
      </View>
    </Pressable>
  );
}

function FreeAgentList() {
  const { data, error, loading, reload } = useApi<FreeAgents>('/market/free-agents');
  const [search, setSearch] = useState('');
  const [pos, setPos] = useState<(typeof POSITIONS)[number]>('All');
  const [shown, setShown] = useState(PAGE);

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (data?.players ?? []).filter(
      (p) => (pos === 'All' || p.pos === pos) && (!needle || p.name.toLowerCase().includes(needle)),
    );
  }, [data, search, pos]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const rankSeason = data.season_started ? data.season : data.last_season;

  return (
    <>
      <TextInput
        value={search}
        onChangeText={setSearch}
        placeholder="Search players"
        placeholderTextColor={colors.muted}
        style={styles.search}
        autoCorrect={false}
      />
      <Chips options={POSITIONS} value={pos} onChange={(p) => { setPos(p); setShown(PAGE); }} />
      <View style={styles.sectionHead}>
        <Text style={styles.sectionTitle}>Open market</Text>
        <Text style={styles.sectionRight}>{`MOST ${rankSeason} POINTS`}</Text>
      </View>
      {rows.length === 0 ? <Note>No free agents match.</Note> : null}
      <View style={styles.grid}>
        {rows.slice(0, shown).map((p) => (
          <PlayerTile
            key={p.player_id}
            id={p.player_id}
            name={p.name}
            pos={p.pos}
            team={p.nfl_team}
            ppg={p.rank_ppg}
            points={p.points}
            line={`${p.nfl_team}${p.last_ppg != null && data.season_started ? ` · ${p.last_ppg.toFixed(1)} last yr` : ''}`}
            tag={p.injury ? { label: injuryLabel(p.injury), tone: 'out' } : p.rookie ? { label: 'ROOKIE', tone: 'gold' } : null}
          />
        ))}
      </View>
      {rows.length > shown ? (
        <Pressable onPress={() => setShown((n) => n + PAGE)} style={styles.more}>
          <Text style={ui.link}>Show {Math.min(PAGE, rows.length - shown)} more ›</Text>
        </Pressable>
      ) : null}
      <Note>Bids happen in the free agent auction on the website.</Note>
    </>
  );
}

function ExpiringList() {
  const [year, setYear] = useState<string | null>(null);
  const path = year ? `/market/expiring?year=${year}` : '/market/expiring';
  const { data, error, loading, reload, refresh } = useApi<Expiring>(path);
  useTargets(refresh); // saving a star anywhere on this page refreshes the targets card
  const [pos, setPos] = useState<string>('All');
  const options = ['All', 'QB', 'RB', 'WR', 'TE', 'Mine'] as const;

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const rows = data.players.filter((p) => pos === 'All' || (pos === 'Mine' ? p.is_mine : p.pos === pos));
  // Between the final game and the rollover, my calls come first until every one is made.
  const callsOpen = !!data.calls && data.calls.players.some((p) => !p.choice);

  return (
    <>
      {callsOpen ? <Calls calls={data.calls!} sleeperId={data.sleeper_league_id} onChange={refresh} /> : <Targets data={data} />}

      <View style={styles.sectionHead}>
        <Text style={styles.sectionTitle}>Contracts ending</Text>
        <Text style={styles.sectionRight}>TAP ☆ TO SAVE</Text>
      </View>
      <Segmented options={data.years.map(String)} value={String(data.season)} onChange={setYear} />
      <Chips options={options} value={pos as (typeof options)[number]} onChange={setPos} />
      {rows.length === 0 ? <Note>Nothing expiring here.</Note> : null}
      <View style={styles.grid}>
        {rows.map((p) => (
          <PlayerTile
            key={p.player_id}
            id={p.player_id}
            name={p.name}
            pos={p.pos}
            team={p.nfl_team}
            ppg={p.rank_ppg}
            points={p.points}
            mine={p.is_mine}
            line={`${p.is_mine ? 'You' : shortOwner(p.owner_name)} · ${money(p.salary)}`}
          />
        ))}
      </View>
      <Note>These hit free agency at the {data.season} rollover unless their owner extends them.</Note>
    </>
  );
}

const CHOICE_LABEL: Record<string, string> = { keep: '✓ KEEPING', walk: '✓ LETTING WALK', review: 'IN REVIEW' };

// My keep-or-walk calls on rookie deals, due at the June 30 rollover. Trades happen in Sleeper.
function Calls({ calls, sleeperId, onChange }: { calls: NonNullable<Expiring['calls']>; sleeperId: string; onChange: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const decided = calls.players.filter((p) => p.choice).length;
  const due = new Date(`${calls.deadline.slice(0, 10)}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

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

  const ask = `Help me decide keep or walk on ${calls.players.filter((p) => !p.choice).map((p) => p.name).join(', ')} before the rollover.`;

  return (
    <Card gold title="Your calls" right={`DUE ${due.toUpperCase()}`}>
      <View style={styles.progress}>
        {calls.players.map((p) => <View key={p.player_id} style={[styles.progressBit, p.choice && styles.progressOn]} />)}
      </View>
      <Text style={ui.sub}>{decided} of {calls.players.length} decided</Text>
      {!calls.window_open ? <Text style={[ui.sub, styles.gap]}>Keep and Let walk open when the commissioner starts the rollover. Anything you skip walks.</Text> : null}
      {calls.players.map((p) => (
        <View key={p.player_id} style={[styles.call, p.choice && styles.callDone]}>
          <View style={styles.callRow}>
            <PlayerFace name={p.name} team={p.nfl_team} pos={p.pos} size={44} />
            <View style={ui.flex}>
              <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
              <Text style={ui.sub}>{money(p.salary)}{p.ppg != null ? ` · ${p.ppg.toFixed(1)} PPG` : ''}</Text>
            </View>
            {p.choice ? <Pill label={CHOICE_LABEL[p.choice] ?? p.choice.toUpperCase()} tone="live" /> : null}
          </View>
          {p.choice ? null : (
            <View style={styles.choices}>
              {(['keep', 'walk'] as const).map((c) => (
                <Pressable key={c} disabled={!p.open || busy === p.player_id} onPress={() => choose(p, c)} style={[styles.choice, !p.open && styles.choiceOff]}>
                  <Text style={styles.choiceText}>{c === 'keep' ? 'Keep' : 'Let walk'}</Text>
                </Pressable>
              ))}
              <Pressable onPress={() => Linking.openURL(`https://sleeper.com/leagues/${sleeperId}`)} style={[styles.choice, styles.choiceGold]}>
                <Text style={[styles.choiceText, styles.choiceGoldText]}>Trade ↗</Text>
              </Pressable>
            </View>
          )}
        </View>
      ))}
      <Pressable onPress={() => router.navigate({ pathname: '/front-office', params: { q: ask, at: String(Date.now()) } })} style={styles.askLink}>
        <Text style={ui.link}>Ask GM to help me decide ›</Text>
      </Pressable>
    </Card>
  );
}

// Players I saved with ☆, and the jump into the Front Office to plan around them.
function Targets({ data }: { data: Expiring }) {
  const { toggle } = useTargets();
  const list = data.targets;
  return (
    <Card gold title="Transfer targets" right={list.length ? `${list.length} SAVED` : undefined}>
      {list.length === 0 ? (
        <Text style={[ui.sub, styles.gap]}>Tap ☆ on any player, here, on Free Agents or on a player page, to save them as a target.</Text>
      ) : null}
      {list.map((t: Target) => (
        <Pressable key={t.player_id} onPress={() => router.push(`/player/${t.player_id}`)} style={styles.targetRow}>
          <PlayerFace name={t.name} team={t.nfl_team} pos={t.pos} size={46} />
          <View style={ui.flex}>
            <Text style={ui.name} numberOfLines={1}>{t.name}</Text>
            <Text style={ui.sub} numberOfLines={1}>
              {t.owner_name ? `${t.is_mine ? 'You' : t.owner_name} · ${money(t.salary)}${t.years_left === 1 ? ' · expiring' : ''}` : `${t.nfl_team} · free agent`}
            </Text>
          </View>
          <View style={styles.targetRight}>
            <Text style={styles.targetPpg}>{ppgText(t.rank_ppg)}<Text style={styles.unit}> PPG</Text></Text>
            <Pressable onPress={() => toggle(t.player_id)} hitSlop={10}>
              <Text style={styles.targetStar}>★</Text>
            </Pressable>
          </View>
        </Pressable>
      ))}
      {list.length ? (
        <Pressable
          onPress={() => router.navigate({ pathname: '/front-office', params: { q: data.plan_question, at: String(Date.now()) } })}
          style={styles.plan}
        >
          <Text style={styles.planText}>Plan my offseason with GM ›</Text>
        </Pressable>
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  search: {
    backgroundColor: colors.card, borderWidth: 1, borderColor: colors.line, borderRadius: 22,
    paddingHorizontal: 16, paddingVertical: 11, color: colors.text, fontSize: 15, marginBottom: 10,
  },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginBottom: 12 },
  chip: { borderWidth: 1, borderColor: colors.line, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 6 },
  chipOn: { borderColor: colors.gold },
  chipText: { color: colors.muted, fontSize: 12, fontWeight: '700' },
  chipTextOn: { color: colors.gold },
  sectionHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 4, marginBottom: 10, paddingHorizontal: 2 },
  sectionTitle: { color: colors.text, fontSize: 18, fontWeight: '800' },
  sectionRight: { color: colors.muted, fontSize: 10, fontWeight: '800', letterSpacing: 1 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', rowGap: 10, marginBottom: 12 },
  tile: { width: '48.5%', borderRadius: 18, borderWidth: 1, borderColor: colors.line, padding: 12, overflow: 'hidden', backgroundColor: colors.card },
  tileGold: { borderColor: colors.gold },
  tileTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' },
  tileTopRight: { alignItems: 'flex-end', gap: 6 },
  star: { width: 28, height: 28, borderRadius: 14, backgroundColor: 'rgba(0,0,0,0.35)', alignItems: 'center', justifyContent: 'center' },
  starOn: { backgroundColor: colors.gold },
  starText: { color: colors.text, fontSize: 15 },
  starTextOn: { color: colors.bg },
  tileName: { color: colors.text, fontSize: 15, fontWeight: '800', marginTop: 12 },
  tileLine: { color: colors.muted, fontSize: 12, marginTop: 2 },
  tileBottom: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-end', marginTop: 8 },
  tilePpg: { color: colors.gold, fontSize: 24, fontWeight: '800' },
  unit: { color: colors.muted, fontSize: 10, fontWeight: '600' },
  tilePts: { color: colors.muted, fontSize: 11, marginBottom: 4 },
  more: { alignItems: 'center', paddingVertical: 8, marginBottom: 4 },
  gap: { marginTop: 6 },
  progress: { flexDirection: 'row', gap: 4, marginTop: 4, marginBottom: 6 },
  progressBit: { flex: 1, height: 6, borderRadius: 3, backgroundColor: '#24392E' },
  progressOn: { backgroundColor: colors.gold },
  call: { borderTopWidth: 1, borderTopColor: colors.row, paddingTop: 12, marginTop: 12 },
  callDone: { opacity: 0.6 },
  callRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  choices: { flexDirection: 'row', gap: 8, marginTop: 12 },
  choice: { flex: 1, borderWidth: 1, borderColor: colors.line, borderRadius: 11, paddingVertical: 9, alignItems: 'center' },
  choiceOff: { opacity: 0.4 },
  choiceText: { color: colors.text, fontWeight: '800', fontSize: 13 },
  choiceGold: { backgroundColor: colors.gold, borderColor: colors.gold },
  choiceGoldText: { color: colors.bg },
  askLink: { alignItems: 'center', marginTop: 14 },
  targetRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 9 },
  targetRight: { alignItems: 'flex-end', gap: 2 },
  targetPpg: { color: colors.text, fontSize: 19, fontWeight: '800' },
  targetStar: { color: colors.gold, fontSize: 16 },
  plan: { backgroundColor: colors.gold, borderRadius: 12, paddingVertical: 12, alignItems: 'center', marginTop: 10 },
  planText: { color: colors.bg, fontWeight: '800', fontSize: 15 },
});
