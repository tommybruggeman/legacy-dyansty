import { useState } from 'react';
import { ActivityIndicator, Image, KeyboardAvoidingView, Linking, Platform, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { supabase } from '../lib/supabase';
import { colors } from '../lib/theme';

const WEBSITE = 'https://legacy-dynasty.streamlit.app';

// Same email and password owners use on the website.
export default function SignIn() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const insets = useSafeAreaInsets();
  const ready = !!email.trim() && !!password && !busy;

  async function signIn() {
    if (!ready) return;
    setBusy(true);
    setError(null);
    const { error } = await supabase.auth.signInWithPassword({ email: email.trim(), password });
    if (error) setError(error.message === 'Invalid login credentials' ? 'That email and password don’t match.' : error.message);
    setBusy(false);
  }

  return (
    <KeyboardAvoidingView style={[styles.screen, { paddingTop: insets.top, paddingBottom: insets.bottom + 16 }]} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={styles.hero}>
        <Image source={require('../../assets/mark.png')} style={styles.crest} resizeMode="contain" />
        <Text style={styles.subtitle}>Sign in with your league account</Text>
      </View>
      <View style={styles.form}>
        <TextInput
          style={styles.input}
          placeholder="Email"
          placeholderTextColor={colors.muted}
          autoCapitalize="none"
          autoComplete="email"
          keyboardType="email-address"
          textContentType="username"
          returnKeyType="next"
          value={email}
          onChangeText={setEmail}
        />
        <TextInput
          style={styles.input}
          placeholder="Password"
          placeholderTextColor={colors.muted}
          secureTextEntry
          autoComplete="current-password"
          textContentType="password"
          returnKeyType="go"
          value={password}
          onChangeText={setPassword}
          onSubmitEditing={signIn}
        />
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <Pressable style={[styles.button, !ready && styles.buttonOff]} onPress={signIn} disabled={!ready}>
          {busy ? <ActivityIndicator color={colors.bg} /> : <Text style={styles.buttonText}>Sign in</Text>}
        </Pressable>
        <Pressable onPress={() => Linking.openURL(WEBSITE)} hitSlop={8} style={styles.help}>
          <Text style={styles.helpText}>Forgot your password? Reset it on the website</Text>
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg, paddingHorizontal: 24, justifyContent: 'center' },
  hero: { alignItems: 'center', marginBottom: 28 },
  crest: { width: 150, height: 182 },
  subtitle: { color: colors.muted, fontSize: 15, marginTop: 16 },
  form: { gap: 12 },
  input: { backgroundColor: colors.row, borderWidth: 1, borderColor: colors.line, color: colors.text, borderRadius: 14, padding: 15, fontSize: 16 },
  error: { color: '#E39A9A', fontSize: 14 },
  button: { backgroundColor: colors.gold, borderRadius: 14, padding: 15, alignItems: 'center', marginTop: 4 },
  buttonOff: { opacity: 0.5 },
  buttonText: { color: colors.bg, fontSize: 16, fontWeight: '800' },
  help: { alignSelf: 'center', marginTop: 8 },
  helpText: { color: colors.muted, fontSize: 13 },
});
