import { useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { Pressable } from '../Pressable';

import { type MoveCard as Card, apiSend } from '../../lib/api';
import { colors } from '../../lib/theme';

// A decision the GM laid out (lineup, trade offer, bid, add, drop): the options as buttons, the GM's pick highlighted.
// Nothing happens until the owner taps one; the server then makes it through the same checks as the owner's own taps.
export function MoveCard({ card, conversationId, onChange }: { card: Card; conversationId: string | null; onChange: (c: Card) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const send = async (action: 'confirm' | 'dismiss', option?: string) => {
    if (!conversationId || busy) return;
    setBusy(option ?? 'dismiss');
    try {
      const res = await apiSend<{ ok: boolean; move: Card }>('POST', `/front-office/moves/${card.id}/${action}`,
        { conversation_id: conversationId, ...(option ? { option } : {}) });
      onChange(res.move);
    } catch (e) {
      onChange({ ...card, status: 'failed', chosen: option ?? null, result: e instanceof Error ? e.message : "That didn't go through." });
    } finally {
      setBusy(null);
    }
  };
  const chosen = card.options.find((o) => o.id === card.chosen);
  return (
    <View style={styles.card}>
      <Text style={styles.title}>{card.title}</Text>
      {card.lines.map((l) => <Text key={l} style={styles.line}>{l}</Text>)}
      {card.status === 'pending' ? (
        <>
          <View style={styles.row}>
            {card.options.map((o) => (
              <Pressable key={o.id} style={[styles.btn, o.pick ? styles.pick : styles.other]} onPress={() => send('confirm', o.id)} disabled={!!busy}>
                {busy === o.id ? <ActivityIndicator color={o.pick ? colors.bg : colors.text} /> : (
                  <>
                    <Text style={o.pick ? styles.pickText : styles.otherText} numberOfLines={2}>{o.label}</Text>
                    {o.sub ? <Text style={o.pick ? styles.pickSub : styles.otherSub} numberOfLines={1}>{o.sub}</Text> : null}
                  </>
                )}
              </Pressable>
            ))}
          </View>
          <Pressable onPress={() => send('dismiss')} disabled={!!busy} hitSlop={8} style={styles.later}>
            <Text style={styles.laterText}>Not now</Text>
          </Pressable>
        </>
      ) : (
        <Text style={[styles.result, card.status === 'done' ? styles.ok : card.status === 'failed' ? styles.bad : null]}>
          {card.status === 'done' ? `✓ ${chosen ? `${chosen.label}. ` : ''}${card.result ?? 'Done'}`
            : card.status === 'failed' ? (card.result ?? "That didn't go through.") : 'Not now'}
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
  btn: { flex: 1, borderRadius: 10, paddingVertical: 10, paddingHorizontal: 6, alignItems: 'center', justifyContent: 'center', minHeight: 54 },
  pick: { backgroundColor: colors.gold },
  other: { borderWidth: 1, borderColor: colors.line },
  pickText: { color: colors.bg, fontWeight: '800', fontSize: 14, textAlign: 'center' },
  otherText: { color: colors.text, fontWeight: '700', fontSize: 14, textAlign: 'center' },
  pickSub: { color: colors.bg, opacity: 0.8, fontSize: 11, fontWeight: '600', marginTop: 2 },
  otherSub: { color: colors.muted, fontSize: 11, fontWeight: '600', marginTop: 2 },
  later: { alignSelf: 'center', marginTop: 8 },
  laterText: { color: colors.muted, fontSize: 13, fontWeight: '600' },
  result: { marginTop: 8, color: colors.muted, fontSize: 13, fontWeight: '600' },
  ok: { color: colors.live },
  bad: { color: colors.danger },
});
