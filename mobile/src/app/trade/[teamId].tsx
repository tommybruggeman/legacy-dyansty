import { router, useLocalSearchParams } from 'expo-router';
import { useMemo, useState } from 'react';
import { Alert, Linking, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { Avatar, Headshot } from '../../components/Pictures';
import { ErrorState, Loading } from '../../components/ScreenState';
import { Back, Screen, Title, firstName, money, ui } from '../../components/ui';
import { type TradeAsset, type TradeBuilder, type TradePick, type TradeSideData, type TradeWindow, apiSend, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

export { ScreenError as ErrorBoundary } from '../../components/ScreenState';

const FAIR_BAND = 0.05;
const POSITIONS = ['All', 'QB', 'RB', 'WR', 'TE'] as const;
const ids = (value?: string) => new Set((value ?? '').split(',').filter(Boolean));

// Your share of the value that moves (same read as api/trade_offers.grade): today's dynasty market value.
function grade(gives: number, gets: number) {
  const total = gives + gets;
  if (total <= 0) return null;
  const share = gets / total;
  const lean = Math.abs(share - 0.5) <= FAIR_BAND ? 'fair' : share > 0.5 ? 'you' : 'them';
  return { share, lean, pct: Math.round(Math.abs(share - 0.5) * 200) };
}

// The trade page: opened from a player page or a team page ("Trade"), from Transactions ("Counter"), or by the GM.
// Two columns side by side: what you receive and what they receive. "+ Add" opens a sheet with that team's players
// and picks. After the league's trade deadline it shows only the closed screen.
export default function TradePage() {
  const { teamId, add, give, get, parent, from } = useLocalSearchParams<{ teamId: string; add?: string; give?: string; get?: string; parent?: string; from?: string }>();
  const { data, error, loading, reload } = useApi<TradeBuilder>(`/trade-builder/${teamId}${add ? `?add=${add}` : ''}`);
  const [mine, setMine] = useState<Set<string>>(() => ids(give));
  const [theirs, setTheirs] = useState<Set<string>>(() => new Set([...ids(get), ...(add ? [`p:${add}`] : [])]));
  const [adding, setAdding] = useState<'me' | 'them' | null>(null);
  const [sending, setSending] = useState(false);

  const picked = useMemo(() => {
    if (!data?.me || !data?.them) return null;
    const take = (side: TradeSideData, chosen: Set<string>) => ({
      players: side.players.filter((p) => chosen.has(p.contract_id) || chosen.has(`p:${p.player_id}`)),
      picks: side.picks.filter((p) => chosen.has(p.pick_id)),
    });
    return { give: take(data.me, mine), get: take(data.them, theirs) };
  }, [data, mine, theirs]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;
  if (data.window && !data.window.open) return <Closed window={data.window} from={from} />;
  if (!picked) return null;

  const value = (s: { players: TradeAsset[]; picks: TradePick[] }) => s.players.reduce((n, p) => n + p.value, 0) + s.picks.reduce((n, p) => n + p.value, 0);
  const salary = (s: { players: TradeAsset[] }) => s.players.reduce((n, p) => n + p.salary, 0);
  const g = grade(value(picked.give), value(picked.get));
  const me = firstName(data.me.owner_name);
  const them = firstName(data.them.owner_name);
  const myCapAfter = data.me.cap_space + salary(picked.give) - salary(picked.get);
  const theirCapAfter = data.them.cap_space + salary(picked.get) - salary(picked.give);
  const over = myCapAfter < 0 || theirCapAfter < 0;
  const ready = (picked.give.players.length + picked.give.picks.length) > 0 && (picked.get.players.length + picked.get.picks.length) > 0;
  const lean = !g ? '' : g.lean === 'fair' ? 'Fair' : g.lean === 'you' ? `${me} +${g.pct}%` : `${them} +${g.pct}%`;

  const toggle = (side: 'me' | 'them', key: string, alt?: string) => {
    const [set, setter] = side === 'me' ? [mine, setMine] : [theirs, setTheirs];
    const next = new Set(set);
    if (next.has(key) || (alt && next.has(alt))) { next.delete(key); if (alt) next.delete(alt); } else next.add(key);
    setter(next);
  };

  const send = async () => {
    if (!data.enabled) {
      Linking.openURL(`https://sleeper.com/leagues/${data.sleeper_league_id}`);
      return;
    }
    setSending(true);
    try {
      await apiSend('POST', '/trade-offers', {
        to_team_id: data.them.team_id,
        give: [...picked.give.players.map((p) => ({ contract_id: p.contract_id, player_id: p.player_id, name: p.name })), ...picked.give.picks.map((p) => ({ pick_id: p.pick_id, label: p.label }))],
        get: [...picked.get.players.map((p) => ({ contract_id: p.contract_id, player_id: p.player_id, name: p.name })), ...picked.get.picks.map((p) => ({ pick_id: p.pick_id, label: p.label }))],
        parent_offer_id: parent ?? null,
      });
      router.replace('/transactions');
    } catch (e) {
      Alert.alert('Offer not sent', e instanceof Error ? e.message : 'Try again.');
    } finally {
      setSending(false);
    }
  };

  return (
    <Screen>
      <Back label="Back" />
      <Title eyebrow={parent ? 'COUNTER OFFER' : undefined}>Trade</Title>
      <View style={styles.heads}>
        <View style={styles.head}><Avatar uri={data.me.avatar} name={data.me.team_name || data.me.owner_name} size={30} /><Text style={styles.headText} numberOfLines={1}>{`${me} Receives`}</Text></View>
        <View style={styles.swapGap} />
        <View style={styles.head}><Avatar uri={data.them.avatar} name={data.them.team_name || data.them.owner_name} size={30} /><Text style={styles.headText} numberOfLines={1}>{`${them} Receives`}</Text></View>
      </View>
      <View style={styles.cols}>
        <Column players={picked.get.players} picks={picked.get.picks} season={data.season} onRemove={(k, alt) => toggle('them', k, alt)} onAdd={() => setAdding('them')} />
        <View style={styles.swap}><Text style={styles.swapText}>⇄</Text></View>
        <Column players={picked.give.players} picks={picked.give.picks} season={data.season} onRemove={(k, alt) => toggle('me', k, alt)} onAdd={() => setAdding('me')} />
      </View>

      <View style={styles.gm}>
        <View style={styles.gmTop}><Text style={styles.gmTitle}>GM READ</Text><Text style={styles.gmLean}>{lean}</Text></View>
        <View style={styles.meter}>
          <View style={[styles.meterFill, { width: `${Math.round((g?.share ?? 0) * 100)}%` }]} />
          <View style={styles.meterMid} />
        </View>
        <Text style={[ui.sub, styles.capLine, over && styles.capBad]}>
          {`Cap after: you ${money(myCapAfter)} · ${them} ${money(theirCapAfter)}${over ? ' · over the cap' : ''}`}
        </Text>
      </View>

      <Pressable disabled={!ready || sending} onPress={send} style={[styles.send, (!ready || sending) && styles.sendOff]}>
        <Text style={styles.sendText}>{sending ? 'Sending…' : data.enabled ? (parent ? 'Send counter' : 'Send offer') : 'Open in Sleeper'}</Text>
      </Pressable>

      <AddSheet
        visible={adding != null}
        title={adding === 'me' ? `Add from your team` : `Add from ${them}`}
        side={adding === 'me' ? data.me : data.them}
        chosen={adding === 'me' ? mine : theirs}
        season={data.season}
        onToggle={(k, alt) => adding && toggle(adding, k, alt)}
        onClose={() => setAdding(null)}
      />
    </Screen>
  );
}

const thru = (p: TradeAsset, season?: number) => (season ? `thru ${season + Math.max(1, p.years) - 1}` : `${p.years} yr${p.years === 1 ? '' : 's'}`);

function Column({ players, picks, season, onRemove, onAdd }: {
  players: TradeAsset[]; picks: TradePick[]; season?: number; onRemove: (key: string, alt?: string) => void; onAdd: () => void;
}) {
  return (
    <View style={styles.col}>
      {players.map((p) => (
        <Pressable key={p.contract_id} onPress={() => onRemove(p.contract_id, `p:${p.player_id}`)} style={styles.piece}>
          <Headshot playerId={p.player_id} pos={p.pos} team={p.nfl_team} size={56} />
          <Text style={styles.pieceName} numberOfLines={1}>{p.name}</Text>
          <Text style={styles.pieceSub}>{`${money(p.salary)} · ${thru(p, season)}`}</Text>
          <Text style={styles.remove}>×</Text>
        </Pressable>
      ))}
      {picks.map((p) => (
        <Pressable key={p.pick_id} onPress={() => onRemove(p.pick_id)} style={styles.piece}>
          <View style={styles.pickBig}><Text style={styles.pickBigText}>{`R${p.round}`}</Text></View>
          <Text style={styles.pieceName} numberOfLines={1}>{`${p.year} R${p.round}`}</Text>
          <Text style={styles.pieceSub} numberOfLines={1}>{p.label.includes('(') ? p.label.slice(p.label.indexOf('(') + 1, -1) : 'own pick'}</Text>
          <Text style={styles.remove}>×</Text>
        </Pressable>
      ))}
      <Pressable onPress={onAdd} style={styles.add}><Text style={styles.addText}>+ Add</Text></Pressable>
    </View>
  );
}

function AddSheet({ visible, title, side, chosen, season, onToggle, onClose }: {
  visible: boolean; title: string; side: TradeSideData; chosen: Set<string>; season?: number;
  onToggle: (key: string, alt?: string) => void; onClose: () => void;
}) {
  const [tab, setTab] = useState<'Players' | 'Picks'>('Players');
  const [pos, setPos] = useState<(typeof POSITIONS)[number]>('All');
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  const players = side.players.filter((p) => (pos === 'All' || p.pos === pos) && (!q || p.name.toLowerCase().includes(q)));
  const years = [...new Set(side.picks.map((p) => p.year))];
  const isOn = (p: TradeAsset) => chosen.has(p.contract_id) || chosen.has(`p:${p.player_id}`);
  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <Pressable style={styles.shade} onPress={onClose} />
      <View style={styles.sheet}>
        <View style={styles.grab} />
        <View style={styles.sheetTop}><Text style={styles.sheetTitle}>{title}</Text><Pressable onPress={onClose} hitSlop={8}><Text style={styles.done}>Done</Text></Pressable></View>
        <View style={styles.tabs}>
          {(['Players', 'Picks'] as const).map((t) => (
            <Pressable key={t} onPress={() => setTab(t)} style={[styles.tab, tab === t && styles.tabOn]}><Text style={[styles.tabText, tab === t && styles.tabTextOn]}>{t}</Text></Pressable>
          ))}
        </View>
        {tab === 'Players' ? (
          <>
            <TextInput value={query} onChangeText={setQuery} placeholder="Search players" placeholderTextColor={colors.muted} style={styles.search} />
            <View style={styles.chips}>
              {POSITIONS.map((p) => (
                <Pressable key={p} onPress={() => setPos(p)} style={[styles.chip, pos === p && styles.chipOn]}><Text style={[styles.chipText, pos === p && styles.chipTextOn]}>{p}</Text></Pressable>
              ))}
            </View>
          </>
        ) : null}
        <ScrollView style={styles.list} contentContainerStyle={{ paddingBottom: 30 }}>
          {tab === 'Players' ? players.map((p) => (
            <Pressable key={p.contract_id} onPress={() => onToggle(p.contract_id, `p:${p.player_id}`)} style={styles.row}>
              <Headshot playerId={p.player_id} pos={p.pos} team={p.nfl_team} size={40} />
              <View style={ui.flex}>
                <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
                <Text style={ui.sub}>{[p.pos, money(p.salary), thru(p, season), p.ppg != null ? `${p.ppg.toFixed(1)} ppg` : null].filter(Boolean).join(' · ')}</Text>
              </View>
              <View style={[styles.check, isOn(p) && styles.checkOn]}><Text style={styles.checkText}>{isOn(p) ? '✓' : '+'}</Text></View>
            </Pressable>
          )) : years.map((y) => (
            <View key={y}>
              <Text style={styles.year}>{`${y} DRAFT`}</Text>
              {side.picks.filter((p) => p.year === y).map((p) => (
                <Pressable key={p.pick_id} onPress={() => onToggle(p.pick_id)} style={styles.row}>
                  <View style={styles.pick}><Text style={styles.pickText}>{`R${p.round}`}</Text></View>
                  <Text style={[ui.name, ui.flex]}>{p.label}</Text>
                  <View style={[styles.check, chosen.has(p.pick_id) && styles.checkOn]}><Text style={styles.checkText}>{chosen.has(p.pick_id) ? '✓' : '+'}</Text></View>
                </Pressable>
              ))}
            </View>
          ))}
          {tab === 'Players' && !players.length ? <Text style={[ui.sub, styles.empty]}>No players match.</Text> : null}
          {tab === 'Picks' && !years.length ? <Text style={[ui.sub, styles.empty]}>No tradeable picks.</Text> : null}
        </ScrollView>
      </View>
    </Modal>
  );
}

// After the deadline the Trade button opens only this.
function Closed({ window, from }: { window: TradeWindow; from?: string }) {
  return (
    <Screen>
      <Back label="Back" />
      <View style={styles.closed}>
        <View style={styles.lock}><Text style={styles.lockText}>🔒</Text></View>
        <Text style={styles.closedTitle}>Trades are closed</Text>
        <Text style={styles.closedText}>{`The trade deadline was Week ${window.deadline_week}. Trading reopens the day after the championship.`}</Text>
        <View style={styles.dates}>
          <View><Text style={styles.dateLabel}>DEADLINE</Text><Text style={styles.dateValue}>{`Week ${window.deadline_week}`}</Text></View>
          <View style={styles.dateLine} />
          <View><Text style={styles.dateLabel}>REOPENS</Text><Text style={[styles.dateValue, styles.green]}>{`After Week ${window.reopens_after_week}`}</Text></View>
        </View>
        <Pressable onPress={() => router.back()} style={styles.backBtn}><Text style={styles.backText}>{from ? `Back to ${from}` : 'Go back'}</Text></Pressable>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  heads: { flexDirection: 'row', alignItems: 'center', marginBottom: 8 },
  head: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 7 },
  headText: { color: colors.text, fontSize: 14, fontWeight: '900', flexShrink: 1 },
  swapGap: { width: 22 },
  cols: { flexDirection: 'row', alignItems: 'stretch', gap: 4 },
  col: { flex: 1, minWidth: 0, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.line, borderRadius: 16, padding: 8 },
  swap: { justifyContent: 'center', width: 18, alignItems: 'center' },
  swapText: { color: colors.gold, fontSize: 18, fontWeight: '900' },
  piece: { backgroundColor: '#0E201A', borderRadius: 14, paddingVertical: 10, paddingHorizontal: 6, marginBottom: 7, alignItems: 'center' },
  pieceName: { color: colors.text, fontSize: 13, fontWeight: '800', marginTop: 8 },
  pieceSub: { color: colors.muted, fontSize: 10, fontWeight: '600' },
  remove: { position: 'absolute', top: 4, right: 8, color: colors.muted, fontSize: 15, fontWeight: '800' },
  pickBig: { width: 56, height: 56, borderRadius: 28, borderWidth: 1.5, borderColor: colors.gold, alignItems: 'center', justifyContent: 'center' },
  pickBigText: { color: colors.gold, fontSize: 14, fontWeight: '900' },
  add: { borderWidth: 1.5, borderStyle: 'dashed', borderColor: '#3A5446', borderRadius: 12, paddingVertical: 8, alignItems: 'center', marginTop: 4 },
  addText: { color: colors.gold, fontSize: 12, fontWeight: '800' },
  gm: { borderRadius: 16, borderWidth: 1, borderColor: colors.gold, padding: 12, marginTop: 10 },
  gmTop: { flexDirection: 'row', justifyContent: 'space-between' },
  gmTitle: { color: colors.gold, fontSize: 11, fontWeight: '900', letterSpacing: 1.3 },
  gmLean: { color: colors.text, fontSize: 12, fontWeight: '800' },
  meter: { height: 6, borderRadius: 3, backgroundColor: '#22382D', marginTop: 10, marginBottom: 6, overflow: 'hidden' },
  meterFill: { height: 6, backgroundColor: colors.gold },
  meterMid: { position: 'absolute', left: '50%', top: 0, bottom: 0, width: 2, backgroundColor: colors.text },
  capLine: { textAlign: 'center' },
  capBad: { color: '#E07C7C' },
  send: { backgroundColor: colors.gold, borderRadius: 14, paddingVertical: 13, alignItems: 'center', marginTop: 10, marginBottom: 20 },
  sendOff: { opacity: 0.4 },
  sendText: { color: colors.bg, fontSize: 15, fontWeight: '900' },
  shade: { flex: 1, backgroundColor: 'rgba(0,0,0,0.55)' },
  sheet: { position: 'absolute', left: 0, right: 0, bottom: 0, height: '78%', backgroundColor: colors.bg, borderTopLeftRadius: 22, borderTopRightRadius: 22, borderWidth: 1, borderColor: colors.line, paddingHorizontal: 16 },
  grab: { alignSelf: 'center', width: 40, height: 5, borderRadius: 3, backgroundColor: colors.line, marginTop: 8, marginBottom: 10 },
  sheetTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 },
  sheetTitle: { color: colors.text, fontSize: 18, fontWeight: '900' },
  done: { color: colors.gold, fontSize: 15, fontWeight: '800' },
  tabs: { flexDirection: 'row', backgroundColor: colors.card, borderRadius: 12, padding: 3, marginBottom: 10 },
  tab: { flex: 1, alignItems: 'center', paddingVertical: 7, borderRadius: 10 },
  tabOn: { backgroundColor: colors.gold },
  tabText: { color: colors.muted, fontSize: 13, fontWeight: '800' },
  tabTextOn: { color: colors.bg },
  search: { borderWidth: 1, borderColor: colors.line, borderRadius: 12, color: colors.text, paddingHorizontal: 12, paddingVertical: 9, marginBottom: 8 },
  chips: { flexDirection: 'row', gap: 6, marginBottom: 6 },
  chip: { borderWidth: 1, borderColor: colors.line, borderRadius: 14, paddingHorizontal: 11, paddingVertical: 5 },
  chipOn: { backgroundColor: colors.gold, borderColor: colors.gold },
  chipText: { color: colors.muted, fontSize: 12, fontWeight: '800' },
  chipTextOn: { color: colors.bg },
  list: { flex: 1 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 7, borderTopWidth: 1, borderTopColor: '#1A3127' },
  check: { width: 26, height: 26, borderRadius: 13, borderWidth: 1.5, borderColor: colors.gold, alignItems: 'center', justifyContent: 'center' },
  checkOn: { backgroundColor: colors.gold },
  checkText: { color: colors.gold, fontSize: 14, fontWeight: '900' },
  pick: { width: 40, height: 40, borderRadius: 20, borderWidth: 1.5, borderColor: colors.gold, alignItems: 'center', justifyContent: 'center' },
  pickText: { color: colors.gold, fontSize: 11, fontWeight: '900' },
  year: { color: colors.muted, fontSize: 11, fontWeight: '900', letterSpacing: 1.2, marginTop: 10, marginBottom: 4 },
  empty: { textAlign: 'center', marginTop: 20 },
  closed: { alignItems: 'center', paddingHorizontal: 20, paddingTop: 110 },
  lock: { width: 96, height: 96, borderRadius: 48, borderWidth: 2, borderColor: '#E07C7C', backgroundColor: 'rgba(224,124,124,0.12)', alignItems: 'center', justifyContent: 'center', marginBottom: 22 },
  lockText: { fontSize: 38 },
  closedTitle: { color: colors.text, fontSize: 28, fontWeight: '900', marginBottom: 10 },
  closedText: { color: colors.muted, fontSize: 15, lineHeight: 22, textAlign: 'center', maxWidth: 300 },
  dates: { flexDirection: 'row', gap: 22, marginTop: 24, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.line, borderRadius: 14, paddingVertical: 12, paddingHorizontal: 18 },
  dateLine: { width: 1, backgroundColor: colors.line },
  dateLabel: { color: colors.muted, fontSize: 10, fontWeight: '900', letterSpacing: 1.2 },
  dateValue: { color: colors.text, fontSize: 16, fontWeight: '900', marginTop: 3 },
  green: { color: colors.live },
  backBtn: { marginTop: 28, borderWidth: 1, borderColor: colors.gold, borderRadius: 14, paddingVertical: 12, paddingHorizontal: 36 },
  backText: { color: colors.gold, fontSize: 15, fontWeight: '900' },
});
