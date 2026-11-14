import Constants from 'expo-constants';
import { router } from 'expo-router';
import { useState } from 'react';
import { Alert, Linking, Pressable, Share, StyleSheet, Switch, Text, TextInput, View } from 'react-native';

import { Avatar } from '../components/Pictures';
import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Card, Note, Row, Screen, Segmented, Title, ui } from '../components/ui';
import { type GmPrefs, type MySettings, apiSend, useApi } from '../lib/api';
import { useAppLock } from '../lib/appLock';
import { PRIVACY_URL, SUPPORT_URL, WEBSITE } from '../lib/links';
import { supabase } from '../lib/supabase';
import { colors } from '../lib/theme';

const LENGTHS = ['Short', 'Detailed'] as const;
const PLANS = ['Auto', 'Contend', 'Rebuild'] as const;

// Everything an owner decides about themselves: profile, how the GM talks to them, their co-owner, their account.
// League rules are here to read. Every owner gets every alert, so there are no alert switches.
export default function Settings() {
  const { data, error, loading, reload, refresh } = useApi<MySettings>('/me/settings');
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;
  return (
    <Screen>
      <Back label="Home" />
      <Title>Settings</Title>
      <ProfileCard data={data} />
      {data.is_commissioner ? (
        <Card title="Commissioner" gold>
          <Row first onPress={() => router.push('/commissioner')}>
            <View style={ui.flex}>
              <Text style={ui.value}>League tools</Text>
              <Text style={ui.sub}>Lineups, adds and drops, contracts, rules, owners, league switch</Text>
            </View>
            <Text style={ui.link}>›</Text>
          </Row>
        </Card>
      ) : null}
      <FrontOfficeCard gm={data.gm} onSaved={refresh} />
      {!data.is_co_owner ? <CoOwnerCard data={data} onChange={refresh} /> : null}
      <Card title="League">
        <Row first onPress={() => router.push('/league-rules')}>
          <Text style={[ui.value, ui.flex]}>League rules</Text>
          <Text style={ui.link}>›</Text>
        </Row>
        <Row onPress={() => Linking.openURL(WEBSITE)}>
          <Text style={[ui.value, ui.flex]}>Open the website</Text>
          <Text style={ui.link}>↗</Text>
        </Row>
        <Note>Rookie drafts, the free agent auction and season rollover live on the website.</Note>
      </Card>
      <Card title="Help">
        <Row first onPress={() => Linking.openURL(SUPPORT_URL)}>
          <Text style={[ui.value, ui.flex]}>Help and support</Text>
          <Text style={ui.link}>↗</Text>
        </Row>
        <Row onPress={() => Linking.openURL(PRIVACY_URL)}>
          <Text style={[ui.value, ui.flex]}>Privacy policy</Text>
          <Text style={ui.link}>↗</Text>
        </Row>
      </Card>
      <AccountCard email={data.email} />
      <Pressable style={styles.signOut} onPress={() => supabase.auth.signOut()}>
        <Text style={styles.signOutText}>Sign out</Text>
      </Pressable>
      <Text style={styles.version}>Version {Constants.expoConfig?.version ?? ''}</Text>
    </Screen>
  );
}

function ProfileCard({ data }: { data: MySettings }) {
  const p = data.profile;
  return (
    <Card>
      <Pressable style={styles.profile} onPress={() => router.push('/profile')}>
        <Avatar uri={p.picture_url} name={p.team_name || p.display_name} size={56} me />
        <View style={ui.flex}>
          <Text style={styles.teamName} numberOfLines={1}>{p.team_name || 'Name your team'}</Text>
          <Text style={ui.sub} numberOfLines={1}>{p.display_name}{p.team_abbr ? ` · ${p.team_abbr}` : ''}</Text>
        </View>
        <Text style={ui.link}>Edit</Text>
      </Pressable>
    </Card>
  );
}

function FrontOfficeCard({ gm, onSaved }: { gm: GmPrefs; onSaved: () => void }) {
  const [prefs, setPrefs] = useState(gm);
  const save = (next: Partial<GmPrefs>) => {
    const was = prefs;
    setPrefs({ ...prefs, ...next });
    apiSend('POST', '/me/gm-prefs', next).then(onSaved).catch((e) => {
      setPrefs(was);
      Alert.alert("Didn't save", e.message);
    });
  };
  const length = prefs.answer_length === 'detailed' ? 'Detailed' : 'Short';
  const plan = (prefs.team_plan[0].toUpperCase() + prefs.team_plan.slice(1)) as (typeof PLANS)[number];
  return (
    <Card title="Front Office">
      <Text style={styles.label}>Answers</Text>
      <Segmented options={LENGTHS} value={length} onChange={(v) => save({ answer_length: v === 'Detailed' ? 'detailed' : 'short' })} />
      <Text style={styles.label}>Your plan this year</Text>
      <Segmented options={PLANS} value={plan} onChange={(v) => save({ team_plan: v.toLowerCase() as GmPrefs['team_plan'] })} />
      <Note>Auto lets the GM decide from your playoff odds. You can always ask it to go deeper.</Note>
    </Card>
  );
}

function CoOwnerCard({ data, onChange }: { data: MySettings; onChange: () => void }) {
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const { co_owners, invites, can_invite } = data.people;

  const shareLink = (link: string, to: string) =>
    Share.share({ message: `Join my team in Legacy Dynasty as co-owner. Sign up with ${to}: ${link}` }).catch(() => undefined);

  const invite = async () => {
    setBusy(true);
    try {
      const res = await apiSend<{ email: string; link: string }>('POST', '/me/co-owner', { email });
      setEmail('');
      onChange();
      await shareLink(res.link, res.email);
    } catch (e: any) {
      Alert.alert("Couldn't invite", e.message);
    } finally {
      setBusy(false);
    }
  };
  const remove = (userId: string, who: string) =>
    Alert.alert('Remove co-owner?', `${who} will lose access to your team.`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Remove', style: 'destructive', onPress: () => apiSend('DELETE', `/me/co-owner/${userId}`).then(onChange).catch((e) => Alert.alert("Couldn't remove", e.message)) },
    ]);
  const cancel = (id: string) =>
    apiSend('DELETE', `/me/co-owner/invite/${id}`).then(onChange).catch((e) => Alert.alert("Couldn't cancel", e.message));

  return (
    <Card title="Co-owner">
      {co_owners.map((c, i) => (
        <Row key={c.user_id} first={i === 0}>
          <Text style={[ui.value, ui.flex]} numberOfLines={1}>{c.email ?? 'Co-owner'}</Text>
          <Pressable onPress={() => remove(c.user_id, c.email ?? 'They')} hitSlop={8}><Text style={styles.danger}>Remove</Text></Pressable>
        </Row>
      ))}
      {invites.map((inv, i) => (
        <Row key={inv.id} first={i === 0 && co_owners.length === 0}>
          <View style={ui.flex}>
            <Text style={ui.value} numberOfLines={1}>{inv.email}</Text>
            <Text style={ui.sub}>Invite waiting</Text>
          </View>
          <Pressable onPress={() => cancel(inv.id)} hitSlop={8}><Text style={styles.danger}>Cancel</Text></Pressable>
        </Row>
      ))}
      {can_invite ? (
        <View style={styles.inviteRow}>
          <TextInput
            style={[styles.input, ui.flex]} value={email} onChangeText={setEmail} placeholder="Their email"
            placeholderTextColor={colors.muted} keyboardType="email-address" autoCapitalize="none" autoCorrect={false}
          />
          <Pressable style={[styles.button, (!email || busy) && styles.buttonOff]} disabled={!email || busy} onPress={invite}>
            <Text style={styles.buttonText}>Invite</Text>
          </Pressable>
        </View>
      ) : null}
      <Note>A co-owner can set your lineup, make moves and use the GM for your team.</Note>
    </Card>
  );
}

function AccountCard({ email }: { email: string | null }) {
  const lock = useAppLock();
  const changeEmail = () =>
    Alert.prompt('Change email', 'We send a link to the new address. Your email changes once you open it.', async (value) => {
      const next = (value ?? '').trim();
      if (!next) return;
      const { error } = await supabase.auth.updateUser({ email: next });
      Alert.alert(error ? "Couldn't change email" : 'Check your email', error ? error.message : `Open the link we sent to ${next}.`);
    }, 'plain-text', '', 'email-address');
  const changePassword = () =>
    Alert.prompt('New password', 'At least 8 characters.', async (value) => {
      if (!value || value.length < 8) return Alert.alert('Too short', 'Use at least 8 characters.');
      const { error } = await supabase.auth.updateUser({ password: value });
      Alert.alert(error ? "Couldn't change password" : 'Password changed', error ? error.message : undefined);
    }, 'secure-text');
  return (
    <Card title="Account">
      <Row first onPress={changeEmail}>
        <View style={ui.flex}>
          <Text style={ui.value}>Email</Text>
          <Text style={ui.sub}>{email ?? ''}</Text>
        </View>
        <Text style={ui.link}>Change</Text>
      </Row>
      <Row onPress={changePassword}>
        <Text style={[ui.value, ui.flex]}>Password</Text>
        <Text style={ui.link}>Change</Text>
      </Row>
      {lock.available ? (
        <Row>
          <Text style={[ui.value, ui.flex]}>Open with {lock.label}</Text>
          <Switch value={lock.enabled} onValueChange={lock.setEnabled} trackColor={{ true: colors.gold }} />
        </Row>
      ) : null}
      <Row onPress={() => router.push('/delete-account')}>
        <Text style={[ui.value, ui.flex, { color: colors.danger }]}>Delete account</Text>
        <Text style={ui.link}>›</Text>
      </Row>
    </Card>
  );
}

const styles = StyleSheet.create({
  profile: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  teamName: { color: colors.text, fontSize: 18, fontWeight: '800' },
  label: { color: colors.muted, fontSize: 12, fontWeight: '700', marginTop: 8, marginBottom: 6 },
  inviteRow: { flexDirection: 'row', gap: 8, marginTop: 8 },
  input: { backgroundColor: colors.card, color: colors.text, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 15 },
  button: { backgroundColor: colors.gold, borderRadius: 10, paddingHorizontal: 16, justifyContent: 'center' },
  buttonOff: { opacity: 0.4 },
  buttonText: { color: colors.bg, fontWeight: '800' },
  danger: { color: colors.danger, fontWeight: '700', fontSize: 13 },
  signOut: { alignSelf: 'center', padding: 14, marginTop: 8 },
  signOutText: { color: colors.danger, fontSize: 15, fontWeight: '700' },
  version: { color: colors.muted, fontSize: 12, textAlign: 'center' },
});
