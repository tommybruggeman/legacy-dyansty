import { router } from 'expo-router';
import { useState } from 'react';
import { Alert, Linking, Pressable, StyleSheet, Text, View } from 'react-native';

import { apiSend, type Expiring, type RookieDeal } from '../../lib/api';
import { colors } from '../../lib/theme';
import { PlayerFace } from '../PlayerFace';
import { Card, Pill, money, ui } from '../ui';

const CHOICE_LABEL: Record<string, string> = { keep: '✓ KEEPING', walk: '✓ LETTING WALK', review: 'IN REVIEW' };

// My keep-or-walk calls on rookie deals, due at the June 30 rollover. Shown on top of My Team (and on Home)
// from the final game until every call is made. Trades happen in Sleeper.
export function YourCalls({ calls, sleeperId, onChange }: { calls: NonNullable<Expiring['calls']>; sleeperId: string; onChange: () => void }) {
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

const styles = StyleSheet.create({
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
});
