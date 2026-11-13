import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { Alert, Pressable, StyleSheet, Switch, Text, TextInput, View } from 'react-native';

import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Card, Note, Screen, Title, ui } from '../components/ui';
import { type CommishRules, type ScoringRow, apiSend, clearApiCache, useApi } from '../lib/api';
import { colors } from '../lib/theme';

type Values = Record<string, string | boolean>;

// Commissioner: the league's rules and points per stat. The same settings the website's League Rules form and
// Scoring editor save, so cap math, scoring and the GM all read them.
export default function CommishRulesPage() {
  const { data, error, loading, reload } = useApi<CommishRules>('/commissioner/rules');
  const [values, setValues] = useState<Values | null>(null);
  const [scoring, setScoring] = useState<ScoringRow[] | null>(null);
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!data || values) return;
    setValues(Object.fromEntries(data.fields.map((f) => [f.key, f.kind === 'bool' ? !!f.value : String(f.value ?? '')])));
    setScoring(data.scoring);
  }, [data, values]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data || !values || !scoring) return null;

  const groups = [...new Set(scoring.map((r) => r.group))];
  const save = async () => {
    setBusy(true);
    try {
      await apiSend('POST', '/commissioner/rules', { values, scoring });
      await clearApiCache();
      router.back();
    } catch (e: any) {
      Alert.alert("Didn't save", e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen>
      <Back label="League tools" />
      <Title>Rules and scoring</Title>
      <Card title="League rules">
        {data.fields.map((f) => (
          <View key={f.key} style={styles.line}>
            <Text style={[ui.value, ui.flex]}>{f.label}</Text>
            {f.kind === 'bool' ? (
              <Switch value={!!values[f.key]} onValueChange={(v) => setValues({ ...values, [f.key]: v })} trackColor={{ true: colors.gold }} />
            ) : (
              <TextInput
                style={[styles.input, f.kind === 'text' && styles.wide]} value={String(values[f.key])}
                onChangeText={(v) => setValues({ ...values, [f.key]: v })}
                keyboardType={f.kind === 'text' ? 'default' : 'decimal-pad'} autoCorrect={false}
              />
            )}
          </View>
        ))}
      </Card>
      <Card title="Points per stat">
        {groups.map((g) => (
          <View key={g}>
            <Pressable style={styles.groupHead} onPress={() => setOpenGroup(openGroup === g ? null : g)}>
              <Text style={[ui.value, ui.flex]}>{g}</Text>
              <Text style={ui.sub}>{scoring.filter((r) => r.group === g && r.points).length} used</Text>
              <Text style={ui.link}>{openGroup === g ? '−' : '+'}</Text>
            </Pressable>
            {openGroup === g ? scoring.map((r, i) => r.group !== g ? null : (
              <View key={r.key} style={styles.line}>
                <Text style={[ui.sub, ui.flex]}>{r.rule}</Text>
                <TextInput
                  style={styles.input} defaultValue={r.points ? String(r.points) : ''} placeholder="0" placeholderTextColor={colors.muted}
                  keyboardType="numbers-and-punctuation"
                  onChangeText={(v) => { const next = [...scoring]; next[i] = { ...r, points: Number(v) || 0 }; setScoring(next); }}
                />
              </View>
            )) : null}
          </View>
        ))}
        <Note>Blank or 0 means the stat isn't scored. Changes count for scores from now on.</Note>
      </Card>
      <Pressable style={[styles.button, busy && styles.off]} disabled={busy} onPress={save}>
        <Text style={styles.buttonText}>Save rules</Text>
      </Pressable>
    </Screen>
  );
}

const styles = StyleSheet.create({
  line: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 6 },
  groupHead: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 10, borderTopWidth: 1, borderTopColor: colors.card },
  input: { backgroundColor: colors.card, color: colors.text, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 7, fontSize: 15, width: 76, textAlign: 'right' },
  wide: { width: 120 },
  button: { backgroundColor: colors.gold, borderRadius: 12, paddingVertical: 14, alignItems: 'center' },
  buttonText: { color: colors.bg, fontWeight: '800', fontSize: 16 },
  off: { opacity: 0.4 },
});
