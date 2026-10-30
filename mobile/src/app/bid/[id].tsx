import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { ActivityIndicator, Alert, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { PlayerFace } from '../../components/PlayerFace';
import { ErrorState, Loading } from '../../components/ScreenState';
import { Back, Card, Note, Row, Screen, Title, money, ui } from '../../components/ui';
import { apiSend, type WaiverSheet, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

export { ScreenError as ErrorBoundary } from '../../components/ScreenState';

// Bid on a free agent (runs Wednesday and Sunday at 3 AM), or on Sundays after the run add him straight away at $1.
// A full roster has to name someone to drop; each one shows what dropping him costs in dead cap.
export default function BidPage() {
  const { id, name, pos, team } = useLocalSearchParams<{ id: string; name?: string; pos?: string; team?: string }>();
  const { data, error, loading, reload } = useApi<WaiverSheet>(`/waivers/player/${id}`);
  const [bid, setBid] = useState<string | null>(null);
  const [drop, setDrop] = useState<string | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const amount = bid ?? String(data.my_bid?.bid ?? 1);
  const dropId = drop === undefined ? data.my_bid?.drop_player_id ?? null : drop;
  const dropping = data.players.find((p) => p.player_id === dropId) ?? null;
  const value = Number(amount);
  const bad = !/^\d+$/.test(amount) || value > data.faab_left;
  const needDrop = data.roster_full && !dropping;
  const who = name ?? 'this player';

  async function send(kind: 'bid' | 'add') {
    setBusy(true);
    try {
      if (kind === 'bid') {
        await apiSend('POST', '/waivers/bid', { player_id: id, bid: value, drop_player_id: dropId });
        Alert.alert('Bid placed', `$${value} on ${who}. Bids run ${data!.next_run}.`);
      } else {
        await apiSend('POST', '/waivers/add', { player_id: id, drop_player_id: dropId });
        Alert.alert('Signed', `${who} is on your roster at $1 for this season.`);
      }
      router.replace('/transactions');
    } catch (e) {
      Alert.alert('Not saved', e instanceof Error ? e.message : 'Try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen loading={loading} onRefresh={reload}>
      <Back label="Back" />
      <Title eyebrow={data.free_agency_open ? 'FREE AGENCY · OPEN TODAY' : `BIDS RUN ${data.next_run.toUpperCase()}`}>{data.free_agency_open ? `Add ${who}` : `Bid on ${who}`}</Title>
      <Card>
        <View style={styles.playerLine}>
          <PlayerFace name={who} team={team ?? 'FA'} pos={pos ?? ''} />
          <View style={ui.flex}>
            <Text style={ui.name}>{who}</Text>
            <Text style={ui.sub}>{[pos, team].filter(Boolean).join(' · ')}</Text>
          </View>
          <View style={ui.right}>
            <Text style={styles.faab}>{money(data.faab_left)}</Text>
            <Text style={ui.sub}>FAAB left</Text>
          </View>
        </View>
        {data.locked ? <Note>His game has already started, so he can't be added this week.</Note> : null}
      </Card>

      {!data.free_agency_open ? (
        <Card title="Your bid">
          <View style={styles.bidRow}>
            <Pressable style={styles.step} onPress={() => setBid(String(Math.max(0, value - 1)))} hitSlop={6}><Text style={styles.stepText}>−</Text></Pressable>
            <View style={styles.amountBox}>
              <Text style={styles.dollar}>$</Text>
              <TextInput value={amount} onChangeText={(t) => setBid(t.replace(/[^0-9]/g, ''))} keyboardType="number-pad" style={styles.amount} maxLength={3} />
            </View>
            <Pressable style={styles.step} onPress={() => setBid(String(Math.min(data.faab_left, value + 1)))} hitSlop={6}><Text style={styles.stepText}>+</Text></Pressable>
          </View>
          <Text style={[ui.sub, styles.center]}>
            {bad ? `Up to ${money(data.faab_left)}` : `Win and he signs for ${money(Math.max(1, value))} this season. Highest bid wins; a tie goes to the earlier bid.`}
          </Text>
        </Card>
      ) : (
        <Note>Sunday free agency: he signs right away for $1 this season. No bid needed.</Note>
      )}

      <Card title={data.roster_full ? 'Your roster is full. Drop someone' : 'Drop someone (optional)'}>
        {data.players.filter((p) => !p.designation).map((p, i) => (
          <Row key={p.player_id} first={i === 0} onPress={p.locked ? undefined : () => setDrop(dropId === p.player_id ? null : p.player_id)}>
            <View style={[styles.radio, dropId === p.player_id && styles.radioOn]} />
            <View style={ui.flex}>
              <Text style={[ui.name, p.locked && styles.dim]} numberOfLines={1}>{p.name}</Text>
              <Text style={[ui.sub, p.dead_cap.total ? styles.cost : null]}>{p.locked ? 'Locked: his game started' : `${p.pos} · ${money(p.salary)} · ${p.dead_cap_text}`}</Text>
            </View>
          </Row>
        ))}
      </Card>

      <Pressable disabled={busy || data.locked || needDrop || (!data.free_agency_open && bad)} onPress={() => send(data.free_agency_open ? 'add' : 'bid')}
        style={[styles.send, (busy || data.locked || needDrop || (!data.free_agency_open && bad)) && styles.sendOff]}>
        {busy ? <ActivityIndicator color={colors.bg} /> : (
          <Text style={styles.sendText}>{data.free_agency_open ? 'Add now for $1' : data.my_bid ? `Update bid to $${value || 0}` : `Bid $${value || 0}`}{dropping ? `, drop ${dropping.name}` : ''}</Text>
        )}
      </Pressable>
      {needDrop ? <Text style={[ui.sub, styles.center]}>Pick someone to drop first.</Text> : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  playerLine: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  faab: { color: colors.gold, fontSize: 20, fontWeight: '800' },
  bidRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 16, marginVertical: 8 },
  step: { width: 48, height: 48, borderRadius: 24, borderWidth: 1, borderColor: colors.gold, alignItems: 'center', justifyContent: 'center' },
  stepText: { color: colors.gold, fontSize: 26, fontWeight: '700', marginTop: -2 },
  amountBox: { flexDirection: 'row', alignItems: 'center', backgroundColor: colors.row, borderRadius: 14, paddingHorizontal: 18, paddingVertical: 6, minWidth: 120, justifyContent: 'center' },
  dollar: { color: colors.muted, fontSize: 28, fontWeight: '700', marginRight: 2 },
  amount: { color: colors.text, fontSize: 40, fontWeight: '800', minWidth: 50, textAlign: 'center' },
  center: { textAlign: 'center', marginTop: 6 },
  radio: { width: 22, height: 22, borderRadius: 11, borderWidth: 2, borderColor: colors.muted },
  radioOn: { borderColor: colors.gold, backgroundColor: colors.gold },
  dim: { color: colors.muted },
  cost: { color: '#E8A6A6' },
  send: { marginTop: 14, backgroundColor: colors.gold, borderRadius: 16, paddingVertical: 16, alignItems: 'center' },
  sendOff: { opacity: 0.4 },
  sendText: { color: colors.bg, fontSize: 17, fontWeight: '800' },
});
