import { router } from 'expo-router';
import { useState } from 'react';
import { Alert, Pressable, Share, StyleSheet, Switch, Text, TextInput, View } from 'react-native';

import { Avatar } from '../components/Pictures';
import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Card, Note, Row, Screen, Title, ui } from '../components/ui';
import { type CommishHome, type CommishTeam, apiSend, useApi } from '../lib/api';
import { colors } from '../lib/theme';

// The commissioner's tools in the app. Drafts, the FA auction and season rollover stay on the website.
export default function Commissioner() {
  const { data, error, loading, reload, refresh } = useApi<CommishHome>('/commissioner');
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;
  return (
    <Screen>
      <Back label="Settings" />
      <Title eyebrow="COMMISSIONER">League tools</Title>
      <Card title="Rosters and lineups">
        <Row first onPress={() => router.push('/commish-team')}>
          <View style={ui.flex}>
            <Text style={ui.value}>Drop a player or fix a contract</Text>
            <Text style={ui.sub}>Pick a team, then a player</Text>
          </View>
          <Text style={ui.link}>›</Text>
        </Row>
        <Row onPress={() => router.push('/commish-add')}>
          <View style={ui.flex}>
            <Text style={ui.value}>Add a player</Text>
            <Text style={ui.sub}>Sign a free agent to any team</Text>
          </View>
          <Text style={ui.link}>›</Text>
        </Row>
        <Row onPress={() => router.push('/(tabs)/league')}>
          <View style={ui.flex}>
            <Text style={ui.value}>Set a lineup for an away owner</Text>
            <Text style={ui.sub}>Open their team in League › Teams and tap a slot</Text>
          </View>
          <Text style={ui.link}>›</Text>
        </Row>
      </Card>
      <Card title="Rules">
        <Row first onPress={() => router.push('/commish-rules')}>
          <View style={ui.flex}>
            <Text style={ui.value}>Edit league rules and scoring</Text>
            <Text style={ui.sub}>Cap, roster, waivers, playoffs, points per stat</Text>
          </View>
          <Text style={ui.link}>›</Text>
        </Row>
      </Card>
      <OwnersCard teams={data.teams} onChange={refresh} />
      <SwitchCard on={data.switch.enabled} fromWeek={data.switch.own_from_week} onChange={refresh} />
    </Screen>
  );
}

function OwnersCard({ teams, onChange }: { teams: CommishTeam[]; onChange: () => void }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Card title="Owners">
      {teams.map((t, i) => (
        <View key={t.team_id}>
          <Row first={i === 0} onPress={() => setOpen(open === t.team_id ? null : t.team_id)}>
            <Avatar uri={t.avatar} name={t.team_name || t.owner_name} size={32} />
            <View style={ui.flex}>
              <Text style={ui.value} numberOfLines={1}>{t.team_name}</Text>
              <Text style={ui.sub} numberOfLines={1}>
                {t.people.length ? t.people.map((p) => p.email ?? 'Signed up').join(', ') : 'Nobody signed up yet'}
              </Text>
            </View>
            <Text style={ui.link}>{open === t.team_id ? '−' : '+'}</Text>
          </Row>
          {open === t.team_id ? <TeamOwners team={t} onChange={onChange} /> : null}
        </View>
      ))}
    </Card>
  );
}

function TeamOwners({ team, onChange }: { team: CommishTeam; onChange: () => void }) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<'owner' | 'co_owner'>(team.people.some((p) => p.role === 'owner' || p.role === 'commissioner') ? 'co_owner' : 'owner');
  const invite = async () => {
    try {
      const res = await apiSend<{ email: string; link: string }>('POST', '/commissioner/invite', { team_id: team.team_id, email, role });
      setEmail('');
      onChange();
      await Share.share({ message: `You're invited to ${team.team_name} in Legacy Dynasty. Sign up with ${res.email}: ${res.link}` });
    } catch (e: any) {
      Alert.alert("Couldn't invite", e.message);
    }
  };
  const remove = (userId: string, who: string) =>
    Alert.alert(`Take ${who} off ${team.team_name}?`, 'They lose access to this team. You can invite someone new after.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Remove', style: 'destructive', onPress: () => apiSend('DELETE', `/commissioner/member/${userId}`).then(onChange).catch((e) => Alert.alert("Couldn't remove", e.message)) },
    ]);
  return (
    <View style={styles.inner}>
      {team.people.map((p) => (
        <View key={p.user_id} style={styles.line}>
          <Text style={[ui.value, ui.flex]} numberOfLines={1}>{p.email ?? 'Member'} · {p.role.replace('_', '-')}</Text>
          {p.role !== 'commissioner' ? (
            <Pressable onPress={() => remove(p.user_id, p.email ?? 'them')} hitSlop={8}><Text style={styles.danger}>Remove</Text></Pressable>
          ) : null}
        </View>
      ))}
      {team.invites.map((inv) => (
        <View key={inv.id} style={styles.line}>
          <Text style={[ui.sub, ui.flex]} numberOfLines={1}>Invite waiting: {inv.email} ({inv.role.replace('_', '-')})</Text>
          <Pressable onPress={() => apiSend('DELETE', `/commissioner/invite/${inv.id}`).then(onChange).catch(() => undefined)} hitSlop={8}>
            <Text style={styles.danger}>Cancel</Text>
          </Pressable>
        </View>
      ))}
      <View style={styles.chips}>
        {(['owner', 'co_owner'] as const).map((r) => (
          <Pressable key={r} onPress={() => setRole(r)} style={[styles.chip, role === r && styles.chipOn]}>
            <Text style={[styles.chipText, role === r && styles.chipTextOn]}>{r === 'owner' ? 'Owner' : 'Co-owner'}</Text>
          </Pressable>
        ))}
      </View>
      <View style={styles.line}>
        <TextInput style={[styles.input, ui.flex]} value={email} onChangeText={setEmail} placeholder="Email to invite"
                   placeholderTextColor={colors.muted} keyboardType="email-address" autoCapitalize="none" autoCorrect={false} />
        <Pressable style={[styles.button, !email && styles.off]} disabled={!email} onPress={invite}>
          <Text style={styles.buttonText}>Invite</Text>
        </Pressable>
      </View>
    </View>
  );
}

function SwitchCard({ on, fromWeek, onChange }: { on: boolean; fromWeek: number | null; onChange: () => void }) {
  const [week, setWeek] = useState(fromWeek ? String(fromWeek) : '');
  const flip = (next: boolean) =>
    Alert.alert(
      next ? 'Run the league in the app?' : 'Turn the app off?',
      next ? 'Trades, waivers, pickups and lineups move into the app for every owner, and our own scoring takes over. Sleeper becomes a stats feed only.'
           : 'Owners go back to making moves in Sleeper.',
      [{ text: 'Cancel', style: 'cancel' }, {
        text: next ? 'Turn on' : 'Turn off', style: next ? 'default' : 'destructive',
        onPress: () => apiSend('POST', '/commissioner/switch', { enabled: next, own_from_week: week ? Number(week) : null })
          .then(onChange).catch((e) => Alert.alert("Didn't save", e.message)),
      }],
    );
  return (
    <Card title="League switch" gold={on}>
      <View style={styles.line}>
        <View style={ui.flex}>
          <Text style={ui.value}>Run the league in the app</Text>
          <Text style={ui.sub}>{on ? 'On: trades, waivers and lineups happen here' : 'Off: moves still happen in Sleeper'}</Text>
        </View>
        <Switch value={on} onValueChange={flip} trackColor={{ true: colors.gold }} />
      </View>
      <View style={styles.line}>
        <Text style={[ui.value, ui.flex]}>Our own scoring from week</Text>
        <TextInput style={[styles.input, styles.week]} value={week} onChangeText={(v) => setWeek(v.replace(/\D/g, '').slice(0, 2))}
                   keyboardType="number-pad" placeholder="1" placeholderTextColor={colors.muted} />
      </View>
      <Note>The week only saves when you flip the switch.</Note>
    </Card>
  );
}

const styles = StyleSheet.create({
  inner: { paddingLeft: 42, paddingBottom: 10, gap: 8 },
  line: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 4 },
  chips: { flexDirection: 'row', gap: 8 },
  chip: { borderWidth: 1, borderColor: colors.line, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 5 },
  chipOn: { backgroundColor: colors.gold, borderColor: colors.gold },
  chipText: { color: colors.muted, fontWeight: '700', fontSize: 12 },
  chipTextOn: { color: colors.bg },
  input: { backgroundColor: colors.card, color: colors.text, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 9, fontSize: 15 },
  week: { width: 60, textAlign: 'center' },
  button: { backgroundColor: colors.gold, borderRadius: 10, paddingHorizontal: 14, paddingVertical: 10 },
  buttonText: { color: colors.bg, fontWeight: '800' },
  off: { opacity: 0.4 },
  danger: { color: colors.danger, fontWeight: '700', fontSize: 13 },
});
