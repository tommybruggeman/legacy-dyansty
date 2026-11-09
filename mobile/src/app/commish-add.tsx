import { router } from 'expo-router';
import { useMemo, useState } from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Card, Note, Row, Screen, Title, ui } from '../components/ui';
import { type FreeAgent, type TeamSummary, apiSend, clearApiCache, useApi } from '../lib/api';
import { colors } from '../lib/theme';

// Commissioner: sign any free agent to any team at a salary and length you set.
export default function CommishAdd() {
  const fa = useApi<{ players: FreeAgent[] }>('/market/free-agents?limit=3000');
  const teams = useApi<{ teams: TeamSummary[] }>('/teams');
  const [query, setQuery] = useState('');
  const [player, setPlayer] = useState<FreeAgent | null>(null);
  const [teamId, setTeamId] = useState<string | null>(null);
  const [salary, setSalary] = useState('1');
  const [years, setYears] = useState('1');
  const [busy, setBusy] = useState(false);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q.length < 2) return [];
    return (fa.data?.players ?? []).filter((p) => p.name.toLowerCase().includes(q)).slice(0, 12);
  }, [fa.data, query]);

  if ((fa.loading && !fa.data) || (teams.loading && !teams.data)) return <Loading />;
  if (fa.error && !fa.data) return <ErrorState message={fa.error} onRetry={fa.reload} />;

  const add = async () => {
    if (!player || !teamId) return;
    setBusy(true);
    try {
      await apiSend('POST', '/commissioner/add', { team_id: teamId, player_id: player.player_id, salary: Number(salary), years: Number(years) });
      await clearApiCache();
      Alert.alert('Added', `${player.name} is signed.`);
      router.back();
    } catch (e: any) {
      Alert.alert("Didn't add", e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen>
      <Back label="League tools" />
      <Title>Add a player</Title>
      <Card title="Player">
        {player ? (
          <Row first onPress={() => setPlayer(null)}>
            <Text style={[ui.value, ui.flex]}>{player.name} · {player.pos} · {player.nfl_team}</Text>
            <Text style={ui.link}>Change</Text>
          </Row>
        ) : (
          <>
            <TextInput style={styles.input} value={query} onChangeText={setQuery} placeholder="Search free agents"
                       placeholderTextColor={colors.muted} autoCorrect={false} />
            {matches.map((p, i) => (
              <Row key={p.player_id} first={i === 0} onPress={() => setPlayer(p)}>
                <Text style={[ui.value, ui.flex]}>{p.name}</Text>
                <Text style={ui.sub}>{p.pos} · {p.nfl_team}</Text>
              </Row>
            ))}
          </>
        )}
      </Card>
      <Card title="Team">
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
          {(teams.data?.teams ?? []).map((t) => (
            <Pressable key={t.team_id} onPress={() => setTeamId(t.team_id)} style={[styles.chip, teamId === t.team_id && styles.chipOn]}>
              <Text style={[styles.chipText, teamId === t.team_id && styles.chipTextOn]}>{t.team_name}</Text>
            </Pressable>
          ))}
        </ScrollView>
      </Card>
      <Card title="Contract">
        <View style={styles.fields}>
          <View style={ui.flex}>
            <Text style={styles.label}>Salary ($)</Text>
            <TextInput style={styles.input} value={salary} onChangeText={setSalary} keyboardType="decimal-pad" />
          </View>
          <View style={ui.flex}>
            <Text style={styles.label}>Years</Text>
            <TextInput style={styles.input} value={years} onChangeText={(v) => setYears(v.replace(/\D/g, '').slice(0, 2))} keyboardType="number-pad" />
          </View>
        </View>
        <Note>Shows in the league's activity as a commissioner add.</Note>
      </Card>
      <Pressable style={[styles.button, (!player || !teamId || busy) && styles.off]} disabled={!player || !teamId || busy} onPress={add}>
        <Text style={styles.buttonText}>Add player</Text>
      </Pressable>
    </Screen>
  );
}

const styles = StyleSheet.create({
  input: { backgroundColor: colors.card, color: colors.text, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 15, marginBottom: 4 },
  chips: { gap: 8, paddingVertical: 4 },
  chip: { borderWidth: 1, borderColor: colors.line, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 6 },
  chipOn: { backgroundColor: colors.gold, borderColor: colors.gold },
  chipText: { color: colors.muted, fontWeight: '700', fontSize: 13 },
  chipTextOn: { color: colors.bg },
  fields: { flexDirection: 'row', gap: 10 },
  label: { color: colors.muted, fontSize: 12, fontWeight: '700', marginBottom: 6 },
  button: { backgroundColor: colors.gold, borderRadius: 12, paddingVertical: 14, alignItems: 'center' },
  buttonText: { color: colors.bg, fontWeight: '800', fontSize: 16 },
  off: { opacity: 0.4 },
});
