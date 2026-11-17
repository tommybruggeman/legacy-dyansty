import { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { type MoveCard as Card, apiSend } from '../../lib/api';
import { colors } from '../../lib/theme';

// A move the GM proposed (lineup, trade offer, bid, add, drop). Nothing happens until the owner taps Confirm;
// the server then makes it through the same checks as the owner's own taps.
export function MoveCard({ card, conversationId, onChange }: { card: Card; conversationId: string | null; onChange: (c: Card) => void }) {
  const [busy, setBusy] = useState(false);
  const act = async (action: 'confirm' | 'dismiss') => {
    if (!conversationId || busy) return;
    setBusy(true);
    try {
      const res = await apiSend<{ ok: boolean; move: Card }>('POST', `/front-office/moves/${card.id}/${action}`, { conversation_id: conversationId });
      onChange(res.move);
    } catch (e) {
      onChange({ ...card, status: 'failed', result: e instanceof Error ? e.message : "That didn't go through." });
    } finally {
      setBusy(false);
    }
  };
  return (
    <View style={styles.card}>
      <Text style={styles.title}>{card.title}</Text>
      {card.lines.map((l) => <Text key={l} style={styles.line}>{l}</Text>)}
      {card.status === 'pending' ? (
        <View style={styles.row}>
          <Pressable style={[styles.btn, styles.confirm]} onPress={() => act('confirm')} disabled={busy}>
            {busy ? <ActivityIndicator color={colors.bg} /> : <Text style={styles.confirmText}>Confirm</Text>}
          </Pressable>
          <Pressable style={[styles.btn, styles.dismiss]} onPress={() => act('dismiss')} disabled={busy}>
            <Text style={styles.dismissText}>Not now</Text>
          </Pressable>
        </View>
      ) : (
        <Text style={[styles.result, card.status === 'done' ? styles.ok : card.status === 'failed' ? styles.bad : null]}>
          {card.status === 'done' ? `✓ ${card.result ?? 'Done'}` : card.status === 'failed' ? (card.result ?? "That didn't go through.") : 'Dismissed'}
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { marginTop: 10, borderWidth: 1, borderColor: colors.gold, borderRadius: 12, padding: 12, backgroundColor: colors.card },
  title: { color: colors.text, fontWeight: '800', fontSize: 15, marginBottom: 4 },
  line: { color: colors.muted, fontSize: 14, marginTop: 2 },
  row: { flexDirection: 'row', gap: 8, marginTop: 10 },
  btn: { flex: 1, borderRadius: 10, paddingVertical: 10, alignItems: 'center' },
  confirm: { backgroundColor: colors.gold },
  confirmText: { color: colors.bg, fontWeight: '800', fontSize: 14 },
  dismiss: { borderWidth: 1, borderColor: colors.line },
  dismissText: { color: colors.muted, fontWeight: '700', fontSize: 14 },
  result: { marginTop: 8, color: colors.muted, fontSize: 13, fontWeight: '600' },
  ok: { color: colors.live },
  bad: { color: colors.danger },
});
