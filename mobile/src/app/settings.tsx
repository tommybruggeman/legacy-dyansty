import Constants from 'expo-constants';
import { Linking, Pressable, StyleSheet, Text } from 'react-native';

import { Back, Card, Note, Row, Screen, Title, ui } from '../components/ui';
import { useMyTeam } from '../lib/myTeam';
import { useSession } from '../lib/session';
import { supabase } from '../lib/supabase';
import { colors } from '../lib/theme';

const WEBSITE = 'https://legacy-dynasty.streamlit.app';

// Who you're signed in as, a way to the website for drafting and the FA
// auction (website only), and sign out.
export default function Settings() {
  const { session } = useSession();
  const { team } = useMyTeam(session?.user.id);

  return (
    <Screen>
      <Back label="Home" />
      <Title>Settings</Title>
      <Card title="Signed in as">
        <Text style={ui.name}>{team?.ownerName ?? session?.user.email ?? ''}</Text>
        {team?.teamName ? <Text style={ui.sub}>{team.teamName}{team.role ? ` · ${team.role}` : ''}</Text> : null}
        {session?.user.email && team?.ownerName ? <Text style={ui.sub}>{session.user.email}</Text> : null}
      </Card>
      <Card>
        <Row first onPress={() => Linking.openURL(WEBSITE)}>
          <Text style={[ui.value, ui.flex]}>Open the website</Text>
          <Text style={ui.link}>↗</Text>
        </Row>
        <Note>Rookie drafts, the free agent auction and commissioner tools live on the website.</Note>
      </Card>
      <Pressable style={styles.signOut} onPress={() => supabase.auth.signOut()}>
        <Text style={styles.signOutText}>Sign out</Text>
      </Pressable>
      <Text style={styles.version}>Version {Constants.expoConfig?.version ?? ''}</Text>
    </Screen>
  );
}

const styles = StyleSheet.create({
  signOut: { alignSelf: 'center', padding: 14, marginTop: 8 },
  signOutText: { color: colors.danger, fontSize: 15, fontWeight: '700' },
  version: { color: colors.muted, fontSize: 12, textAlign: 'center' },
});
