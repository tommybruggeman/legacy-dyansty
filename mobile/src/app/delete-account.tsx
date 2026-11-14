import { useState } from 'react';
import { ActivityIndicator, Alert, Linking, Pressable, StyleSheet, Text, TextInput } from 'react-native';

import { Back, Card, Note, Screen, Title } from '../components/ui';
import { apiSend, clearApiCache } from '../lib/api';
import { PRIVACY_URL } from '../lib/links';
import { supabase } from '../lib/supabase';
import { colors } from '../lib/theme';

// Apple requires deleting your account from inside the app. Typing DELETE guards against a stray tap.
export default function DeleteAccount() {
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const ready = typed.trim().toUpperCase() === 'DELETE' && !busy;

  const remove = async () => {
    if (!ready) return;
    setBusy(true);
    try {
      await apiSend('DELETE', '/me');
      await clearApiCache();
      await supabase.auth.signOut().catch(() => undefined);
      Alert.alert('Account deleted', 'Your account and personal data are gone. Thanks for playing.');
    } catch (e: any) {
      Alert.alert("Couldn't delete your account", e?.message ?? 'Please try again.');
      setBusy(false);
    }
  };

  return (
    <Screen>
      <Back label="Settings" />
      <Title>Delete account</Title>
      <Card title="What gets deleted">
        <Text style={styles.body}>
          Your sign-in, your Front Office chats, saved targets, GM settings, alert settings, and your link to your team. This can’t be undone.
        </Text>
      </Card>
      <Card title="What stays">
        <Text style={styles.body}>
          League records stay in the league history, like past contracts, trades and lineups, so the other owners’ records still add up. Your team stays in
          the league until the commissioner gives it to a new owner.
        </Text>
        <Pressable onPress={() => Linking.openURL(PRIVACY_URL)} hitSlop={6}>
          <Text style={styles.link}>Privacy policy ↗</Text>
        </Pressable>
      </Card>
      <Card title="Type DELETE to confirm">
        <TextInput
          style={styles.input}
          value={typed}
          onChangeText={setTyped}
          placeholder="DELETE"
          placeholderTextColor={colors.muted}
          autoCapitalize="characters"
          autoCorrect={false}
        />
        <Pressable style={[styles.button, !ready && styles.off]} disabled={!ready} onPress={remove}>
          {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>Delete my account</Text>}
        </Pressable>
        <Note>The commissioner can’t delete their account until another owner takes over as commissioner. Email support for help with that.</Note>
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  body: { color: colors.text, fontSize: 15, lineHeight: 21 },
  link: { color: colors.gold, fontWeight: '700', marginTop: 10 },
  input: { backgroundColor: colors.row, borderWidth: 1, borderColor: colors.line, color: colors.text, borderRadius: 12, padding: 13, fontSize: 16 },
  button: { backgroundColor: colors.danger, borderRadius: 12, padding: 14, alignItems: 'center', marginTop: 12 },
  off: { opacity: 0.45 },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '800' },
});
