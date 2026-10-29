import { router, useFocusEffect } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import { Alert, Pressable, StyleSheet, Text, View } from 'react-native';

import { PlayerFace } from '../components/PlayerFace';
import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Note, Screen, Title, ui } from '../components/ui';
import { type Offer, type OfferPiece, type Transactions, apiSend, useApi } from '../lib/api';
import { colors } from '../lib/theme';

export { ScreenError as ErrorBoundary } from '../components/ScreenState';

const ago = (iso: string | null) => {
  if (!iso) return '';
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  return mins < 60 ? `${mins}m ago` : mins < 1440 ? `${Math.round(mins / 60)}h ago` : `${Math.round(mins / 1440)}d ago`;
};
const keys = (pieces: OfferPiece[]) => pieces.map((p) => p.contract_id ?? p.pick_id).filter(Boolean).join(',');

// My Team's transaction center: offers you need to answer, trades you sent, your bids, and the last week's results.
export default function TransactionsPage() {
  const { data, error, loading, reload, refresh } = useApi<Transactions>('/transactions');
  const [busy, setBusy] = useState<string | null>(null);
  const seen = useRef(false);
  useFocusEffect(useCallback(() => {
    if (seen.current) refresh();
    seen.current = true;
  }, [refresh]));

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const act = (offer: Offer, action: 'accept' | 'decline' | 'cancel') => {
    const words = { accept: ['Accept this trade?', 'It goes through right away.'], decline: ['Decline this trade?', `${offer.partner} will see it was declined.`], cancel: ['Cancel this offer?', ''] }[action];
    Alert.alert(words[0], words[1], [
      { text: 'Not now', style: 'cancel' },
      { text: action === 'accept' ? 'Accept' : action === 'decline' ? 'Decline' : 'Cancel offer', style: action === 'accept' ? 'default' : 'destructive', onPress: async () => {
        setBusy(offer.id);
        try {
          await apiSend('POST', `/trade-offers/${offer.id}/${action}`);
          refresh();
        } catch (e) {
          Alert.alert('Not saved', e instanceof Error ? e.message : 'Try again.');
        } finally {
          setBusy(null);
        }
      } },
    ]);
  };
  const counter = (offer: Offer) => router.push({ pathname: '/trade/[teamId]', params: { teamId: offer.partner_team_id, parent: offer.id, give: keys(offer.you_give), get: keys(offer.you_get) } });

  return (
    <Screen loading={loading} onRefresh={reload}>
      <Back label="My Team" />
      <Title>Transactions</Title>
      {!data.enabled ? <Note>Trades and bids in the app turn on after the league leaves Sleeper.</Note> : null}

      {data.needs_answer.length ? <Section title="Needs your answer" count={data.needs_answer.length} /> : null}
      {data.needs_answer.map((o) => (
        <OfferCard key={o.id} offer={o} gold label={`${o.is_counter ? 'COUNTER' : 'FROM'} ${o.partner.toUpperCase()}`} right={ago(o.created_at)}>
          <View style={styles.buttons}>
            <Button label="Decline" onPress={() => act(o, 'decline')} disabled={busy === o.id} />
            <Button label="Counter" line onPress={() => counter(o)} disabled={busy === o.id} />
            <Button label="Accept" gold onPress={() => act(o, 'accept')} disabled={busy === o.id} />
          </View>
        </OfferCard>
      ))}

      {data.sent.length ? <Section title="You sent" count={data.sent.length} /> : null}
      {data.sent.map((o) => (
        <OfferCard key={o.id} offer={o} label={`TO ${o.partner.toUpperCase()}`} right="WAITING">
          <View style={styles.buttons}><Button label="Cancel offer" onPress={() => act(o, 'cancel')} disabled={busy === o.id} /></View>
        </OfferCard>
      ))}

      {data.enabled && !data.pending ? <Note>Nothing pending. Start a trade from any player's page.</Note> : null}

      {data.results.length ? (
        <>
          <Section title="Results" right="Last 7 days" />
          <View style={styles.card}>
            {data.results.map((r, i) => (
              <View key={r.id} style={[styles.result, i > 0 && styles.resultNext]}>
                <View style={[styles.icon, { backgroundColor: r.status === 'accepted' ? 'rgba(200,155,74,0.18)' : 'rgba(224,124,124,0.18)' }]}>
                  <Text style={[styles.iconText, { color: r.status === 'accepted' ? colors.gold : '#E07C7C' }]}>{r.status === 'accepted' ? '✓' : '✕'}</Text>
                </View>
                <View style={ui.flex}>
                  <Text style={styles.resultLine}>{r.line}</Text>
                  <Text style={ui.sub} numberOfLines={1}>{[...r.you_give, ...r.you_get].map((p) => p.name).join(' · ')}</Text>
                </View>
              </View>
            ))}
          </View>
        </>
      ) : null}
    </Screen>
  );
}

function Section({ title, count, right }: { title: string; count?: number; right?: string }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      <Text style={ui.sub}>{right ?? (count != null ? String(count) : '')}</Text>
    </View>
  );
}

function OfferCard({ offer, label, right, gold, children }: { offer: Offer; label: string; right: string; gold?: boolean; children: React.ReactNode }) {
  return (
    <View style={[styles.card, gold && styles.cardGold]}>
      <View style={styles.head}>
        <Text style={styles.label}>{label}</Text>
        <Text style={right === 'WAITING' ? styles.waiting : ui.sub}>{right}</Text>
      </View>
      <Text style={styles.owner}>You get</Text>
      {offer.you_get.map((p, i) => <Piece key={`g${i}`} p={p} />)}
      <View style={styles.split} />
      <Text style={styles.owner}>You give</Text>
      {offer.you_give.map((p, i) => <Piece key={`y${i}`} p={p} />)}
      {offer.note ? <Text style={[ui.sub, styles.note]}>{`“${offer.note}”`}</Text> : null}
      {children}
    </View>
  );
}

function Piece({ p }: { p: OfferPiece }) {
  return (
    <Pressable disabled={p.kind !== 'player'} onPress={() => p.player_id && router.push(`/player/${p.player_id}`)} style={styles.row}>
      {p.kind === 'player'
        ? <PlayerFace name={p.name} team={p.nfl_team} pos={p.pos} size={34} />
        : <View style={styles.pick}><Text style={styles.pickText}>PICK</Text></View>}
      <Text style={[ui.name, ui.flex]} numberOfLines={1}>{p.name}</Text>
    </Pressable>
  );
}

function Button({ label, onPress, gold, line, disabled }: { label: string; onPress: () => void; gold?: boolean; line?: boolean; disabled?: boolean }) {
  return (
    <Pressable disabled={disabled} onPress={onPress} style={[styles.button, gold && styles.buttonGold, line && styles.buttonLine, disabled && styles.off]}>
      <Text style={[styles.buttonText, gold && styles.buttonTextGold, line && styles.buttonTextLine]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  section: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 10, marginBottom: 8 },
  sectionTitle: { color: colors.text, fontSize: 16, fontWeight: '900' },
  card: { backgroundColor: colors.card, borderRadius: 18, borderWidth: 1, borderColor: colors.line, padding: 13, marginBottom: 10 },
  cardGold: { borderColor: colors.gold },
  head: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  label: { color: colors.gold, fontSize: 11, fontWeight: '900', letterSpacing: 1 },
  waiting: { color: '#F0C674', fontSize: 11, fontWeight: '800' },
  owner: { color: colors.gold, fontSize: 13, fontWeight: '800', marginTop: 4 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 4 },
  pick: { width: 34, height: 34, borderRadius: 17, borderWidth: 1, borderColor: colors.gold, alignItems: 'center', justifyContent: 'center' },
  pickText: { color: colors.gold, fontSize: 8, fontWeight: '900' },
  split: { height: 1, backgroundColor: colors.row, marginVertical: 6 },
  note: { marginTop: 6, fontStyle: 'italic' },
  buttons: { flexDirection: 'row', gap: 8, marginTop: 10 },
  button: { flex: 1, borderRadius: 12, borderWidth: 1, borderColor: '#3A4A40', paddingVertical: 9, alignItems: 'center' },
  buttonGold: { backgroundColor: colors.gold, borderColor: colors.gold },
  buttonLine: { borderColor: colors.gold },
  buttonText: { color: colors.muted, fontSize: 13, fontWeight: '900' },
  buttonTextGold: { color: colors.bg },
  buttonTextLine: { color: colors.gold },
  off: { opacity: 0.5 },
  result: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8 },
  resultNext: { borderTopWidth: 1, borderTopColor: colors.row },
  resultLine: { color: colors.text, fontSize: 13, fontWeight: '700' },
  icon: { width: 30, height: 30, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  iconText: { fontSize: 13, fontWeight: '900' },
});
