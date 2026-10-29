import { router, useLocalSearchParams } from 'expo-router';
import { useMemo, useState } from 'react';
import { Alert, Linking, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { PlayerFace } from '../../components/PlayerFace';
import { ErrorState, Loading } from '../../components/ScreenState';
import { Back, Note, Screen, Title, firstName, money, ui } from '../../components/ui';
import { type TradeAsset, type TradeBuilder, type TradePick, type TradeSideData, apiSend, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

export { ScreenError as ErrorBoundary } from '../../components/ScreenState';

const SHOWN = 4;
const FAIR_BAND = 0.05;
const ids = (value?: string) => new Set((value ?? '').split(',').filter(Boolean));

// Your share of the value that moves (same read as api/trade_offers.grade): today's dynasty market value.
function grade(gives: number, gets: number) {
  const total = gives + gets;
  if (total <= 0) return null;
  const share = gets / total;
  const lean = Math.abs(share - 0.5) <= FAIR_BAND ? 'fair' : share > 0.5 ? 'you' : 'them';
  return { share, lean, pct: Math.round(Math.abs(share - 0.5) * 200) };
}

// The trade builder: opened from a player page ("Trade"), from Transactions ("Counter"), or by the GM.
// Pick what each side gets; the read and cap check update as you go. Accepted offers go through right away.
export default function TradeBuilderPage() {
  const { teamId, add, give, get, parent } = useLocalSearchParams<{ teamId: string; add?: string; give?: string; get?: string; parent?: string }>();
  const { data, error, loading, reload } = useApi<TradeBuilder>(`/trade-builder/${teamId}${add ? `?add=${add}` : ''}`);
  const [mine, setMine] = useState<Set<string>>(() => ids(give));
  const [theirs, setTheirs] = useState<Set<string>>(() => new Set([...ids(get), ...(add ? [`p:${add}`] : [])]));
  const [note, setNote] = useState('');
  const [sending, setSending] = useState(false);

  const picked = useMemo(() => {
    if (!data) return null;
    const take = (side: TradeSideData, chosen: Set<string>) => ({
      players: side.players.filter((p) => chosen.has(p.contract_id) || chosen.has(`p:${p.player_id}`)),
      picks: side.picks.filter((p) => chosen.has(p.pick_id)),
    });
    return { give: take(data.me, mine), get: take(data.them, theirs) };
  }, [data, mine, theirs]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data || !picked) return null;

  const value = (s: { players: TradeAsset[]; picks: TradePick[] }) => s.players.reduce((n, p) => n + p.value, 0) + s.picks.reduce((n, p) => n + p.value, 0);
  const salary = (s: { players: TradeAsset[] }) => s.players.reduce((n, p) => n + p.salary, 0);
  const g = grade(value(picked.give), value(picked.get));
  const them = firstName(data.them.owner_name);
  const myCapAfter = data.me.cap_space + salary(picked.give) - salary(picked.get);
  const theirCapAfter = data.them.cap_space + salary(picked.get) - salary(picked.give);
  const ready = (picked.give.players.length + picked.give.picks.length) > 0 && (picked.get.players.length + picked.get.picks.length) > 0;
  const read = !g ? 'Pick what each side gets.'
    : g.lean === 'fair' ? `About even on today's dynasty value. ${them} should see it as fair.`
      : g.lean === 'you' ? `About ${g.pct}% in your favor on today's dynasty value. ${them} may counter.`
        : `About ${g.pct}% in ${them}'s favor on today's dynasty value. You're paying a premium.`;

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, key: string, alt?: string) => {
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
        note: note.trim() || null,
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
      <Title eyebrow={parent ? 'COUNTER OFFER' : 'TRADE'}>{`Trade with ${them}`}</Title>
      {!data.enabled ? <Note>Trades in the app turn on after the league leaves Sleeper. Until then, Send opens Sleeper.</Note> : null}
      <SidePicker title="You give" meta={`${money(salary(picked.give))} salary out`} side={data.me} chosen={mine} onToggle={(k, alt) => toggle(mine, setMine, k, alt)} />
      <Text style={styles.arrow}>⇅</Text>
      <SidePicker title="You get" meta={`${money(salary(picked.get))} salary in`} side={data.them} chosen={theirs} onToggle={(k, alt) => toggle(theirs, setTheirs, k, alt)} />

      <View style={styles.gm}>
        <View style={styles.gmHead}><View style={styles.gmDot}><Text style={styles.gmDotText}>GM</Text></View><Text style={styles.gmTitle}>GM READ</Text></View>
        <Text style={styles.gmText}>{read}</Text>
        {g ? (
          <>
            <View style={styles.meter}><View style={[styles.meterFill, { width: `${Math.round(g.share * 100)}%` }]} /><View style={styles.meterMid} /></View>
            <View style={styles.meterLabels}><Text style={ui.sub}>You lose</Text><Text style={ui.sub}>Fair</Text><Text style={ui.sub}>You win</Text></View>
          </>
        ) : null}
        <Text style={[ui.sub, styles.cap, (myCapAfter < 0 || theirCapAfter < 0) && styles.capBad]}>
          {`Cap space after: you ${money(myCapAfter)}, ${them} ${money(theirCapAfter)}${myCapAfter < 0 || theirCapAfter < 0 ? ' · over the cap' : ''}`}
        </Text>
      </View>

      <TextInput value={note} onChangeText={setNote} placeholder={`Note to ${them} (optional)`} placeholderTextColor={colors.muted} style={styles.note} maxLength={280} />
      <Pressable disabled={!ready || sending} onPress={send} style={[styles.send, (!ready || sending) && styles.sendOff]}>
        <Text style={styles.sendText}>{sending ? 'Sending…' : data.enabled ? (parent ? 'Send counter' : 'Send offer') : 'Open in Sleeper'}</Text>
      </Pressable>
    </Screen>
  );
}

function SidePicker({ title, meta, side, chosen, onToggle }: {
  title: string; meta: string; side: TradeSideData; chosen: Set<string>; onToggle: (key: string, alt?: string) => void;
}) {
  const [all, setAll] = useState(false);
  const isOn = (p: TradeAsset) => chosen.has(p.contract_id) || chosen.has(`p:${p.player_id}`);
  const players = all ? side.players : side.players.filter(isOn);
  const picks = all ? side.picks : side.picks.filter((p) => chosen.has(p.pick_id));
  const rest = side.players.length + side.picks.length - players.length - picks.length;
  return (
    <View style={styles.side}>
      <View style={styles.sideHead}><Text style={styles.sideTitle}>{title}</Text><Text style={ui.sub}>{meta}</Text></View>
      {players.map((p) => (
        <Pressable key={p.contract_id} onPress={() => onToggle(p.contract_id, `p:${p.player_id}`)} style={styles.row}>
          <View style={[styles.check, isOn(p) && styles.checkOn]}><Text style={styles.checkText}>{isOn(p) ? '✓' : ''}</Text></View>
          <PlayerFace name={p.name} team={p.nfl_team} pos={p.pos} size={36} />
          <View style={ui.flex}>
            <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
            <Text style={ui.sub}>{[money(p.salary), `${p.years} yr${p.years === 1 ? '' : 's'}`, p.ppg != null ? `${p.ppg.toFixed(1)} ppg` : null].filter(Boolean).join(' · ')}</Text>
          </View>
        </Pressable>
      ))}
      {picks.map((p) => (
        <Pressable key={p.pick_id} onPress={() => onToggle(p.pick_id)} style={styles.row}>
          <View style={[styles.check, chosen.has(p.pick_id) && styles.checkOn]}><Text style={styles.checkText}>{chosen.has(p.pick_id) ? '✓' : ''}</Text></View>
          <View style={styles.pick}><Text style={styles.pickText}>{`R${p.round}`}</Text></View>
          <Text style={[ui.name, ui.flex]}>{p.label}</Text>
        </Pressable>
      ))}
      {!all && rest > 0 ? (
        <Pressable onPress={() => setAll(true)} style={styles.more}><Text style={ui.link}>{players.length + picks.length ? `+ Add players or picks (${rest}) ›` : `Choose from ${rest} players and picks ›`}</Text></Pressable>
      ) : all && side.players.length + side.picks.length > SHOWN ? (
        <Pressable onPress={() => setAll(false)} style={styles.more}><Text style={ui.link}>Show only picked</Text></Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  side: { backgroundColor: colors.card, borderRadius: 18, borderWidth: 1, borderColor: colors.line, padding: 14, marginBottom: 6 },
  sideHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 },
  sideTitle: { color: colors.gold, fontSize: 14, fontWeight: '800' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 6 },
  check: { width: 22, height: 22, borderRadius: 7, borderWidth: 1.5, borderColor: '#4A5A50', alignItems: 'center', justifyContent: 'center' },
  checkOn: { backgroundColor: colors.gold, borderColor: colors.gold },
  checkText: { color: colors.bg, fontSize: 13, fontWeight: '900' },
  pick: { width: 36, height: 36, borderRadius: 18, borderWidth: 1, borderColor: colors.gold, alignItems: 'center', justifyContent: 'center' },
  pickText: { color: colors.gold, fontSize: 10, fontWeight: '900' },
  more: { paddingTop: 8 },
  arrow: { color: colors.gold, fontSize: 18, fontWeight: '900', textAlign: 'center', marginVertical: 2 },
  gm: { borderRadius: 16, borderWidth: 1, borderColor: colors.gold, backgroundColor: 'rgba(200,155,74,0.10)', padding: 13, marginTop: 8, marginBottom: 10 },
  gmHead: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 },
  gmDot: { width: 26, height: 26, borderRadius: 13, backgroundColor: colors.gold, alignItems: 'center', justifyContent: 'center' },
  gmDotText: { color: colors.bg, fontSize: 10, fontWeight: '900' },
  gmTitle: { color: colors.gold, fontSize: 12, fontWeight: '900', letterSpacing: 1 },
  gmText: { color: colors.text, fontSize: 13, lineHeight: 18 },
  meter: { height: 8, borderRadius: 4, backgroundColor: '#22382D', marginTop: 8, overflow: 'hidden' },
  meterFill: { height: 8, backgroundColor: colors.gold },
  meterMid: { position: 'absolute', left: '50%', top: 0, bottom: 0, width: 2, backgroundColor: colors.text },
  meterLabels: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 4 },
  cap: { marginTop: 6 },
  capBad: { color: '#E07C7C' },
  note: { borderWidth: 1, borderColor: colors.line, borderRadius: 12, color: colors.text, paddingHorizontal: 12, paddingVertical: 10, marginBottom: 10 },
  send: { backgroundColor: colors.gold, borderRadius: 14, paddingVertical: 13, alignItems: 'center', marginBottom: 20 },
  sendOff: { opacity: 0.4 },
  sendText: { color: colors.bg, fontSize: 15, fontWeight: '900' },
});
