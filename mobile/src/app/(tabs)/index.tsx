import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { useMyTeam } from '../../lib/myTeam';
import { useSession } from '../../lib/session';
import { supabase } from '../../lib/supabase';
import { colors } from '../../lib/theme';

// Phase 0 home: proves sign-in works and the owner's team loads through the
// same database rules the website uses.
export default function Home() {
  const { session } = useSession();
  const { team, error, loading } = useMyTeam(session?.user.id);

  return (
    <View style={styles.screen}>
      <View style={styles.card}>
        {loading ? (
          <ActivityIndicator color={colors.gold} />
        ) : error ? (
          <Text style={styles.error}>{error}</Text>
        ) : team ? (
          <>
            <Text style={styles.label}>Signed in as</Text>
            <Text style={styles.owner}>{team.ownerName ?? session?.user.email}</Text>
            <Text style={styles.team}>{team.teamName}</Text>
            {team.role ? <Text style={styles.role}>{team.role}</Text> : null}
          </>
        ) : (
          <Text style={styles.error}>This account isn't linked to a team yet.</Text>
        )}
      </View>
      <Pressable style={styles.signOut} onPress={() => supabase.auth.signOut()}>
        <Text style={styles.signOutText}>Sign out</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg, padding: 16, gap: 16 },
  card: { backgroundColor: colors.card, borderRadius: 16, padding: 20, gap: 4 },
  label: { color: colors.muted, fontSize: 13, textTransform: 'uppercase' },
  owner: { color: colors.text, fontSize: 24, fontWeight: '800' },
  team: { color: colors.gold, fontSize: 16 },
  role: { color: colors.muted, fontSize: 13, marginTop: 6 },
  error: { color: colors.danger, fontSize: 15 },
  signOut: { alignSelf: 'center', padding: 12 },
  signOutText: { color: colors.muted, fontSize: 15 },
});
