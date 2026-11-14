import { useEffect, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { canAskForAlerts, registerForPush } from '../../lib/push';
import { colors } from '../../lib/theme';

const DISMISSED = 'alerts-card-dismissed';

// Apple's alert prompt only shows after the owner reads what the alerts are and taps Turn on.
// "Not now" hides the card; it doesn't come back (alerts can still be turned on in iPhone Settings).
export function AlertsCard() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    let live = true;
    (async () => {
      const dismissed = await AsyncStorage.getItem(DISMISSED).catch(() => null);
      if (!dismissed && (await canAskForAlerts()) && live) setShow(true);
    })();
    return () => {
      live = false;
    };
  }, []);

  if (!show) return null;

  const hide = () => {
    setShow(false);
    AsyncStorage.setItem(DISMISSED, '1').catch(() => undefined);
  };

  return (
    <View style={styles.card}>
      <Text style={styles.title}>Turn on league alerts?</Text>
      <Text style={styles.body}>
        Get a heads-up when a starter is ruled out, a trade offer comes in, waivers run, or your final score is in. Nothing overnight, and you can pick which alerts you get.
      </Text>
      <View style={styles.row}>
        <Pressable style={styles.later} onPress={hide} hitSlop={6}>
          <Text style={styles.laterText}>Not now</Text>
        </Pressable>
        <Pressable
          style={styles.on}
          onPress={async () => {
            hide();
            await registerForPush();
          }}
        >
          <Text style={styles.onText}>Turn on</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: colors.card, borderRadius: 16, padding: 16, marginBottom: 14, borderWidth: 1, borderColor: colors.line },
  title: { color: colors.text, fontSize: 16, fontWeight: '800', marginBottom: 6 },
  body: { color: colors.muted, fontSize: 14, lineHeight: 20 },
  row: { flexDirection: 'row', justifyContent: 'flex-end', alignItems: 'center', gap: 16, marginTop: 12 },
  later: { paddingVertical: 8 },
  laterText: { color: colors.muted, fontSize: 14, fontWeight: '700' },
  on: { backgroundColor: colors.gold, borderRadius: 12, paddingVertical: 9, paddingHorizontal: 18 },
  onText: { color: colors.bg, fontSize: 14, fontWeight: '800' },
});
