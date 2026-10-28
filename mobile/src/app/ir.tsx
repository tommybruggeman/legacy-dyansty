import { router } from 'expo-router';
import { useState } from 'react';
import { ActivityIndicator, Alert, Platform, Pressable, StyleSheet, Text, View } from 'react-native';

import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Card, Note, Pill, Pos, Row, Screen, Title, money, ui } from '../components/ui';
import { type Player, type TeamPage, apiSend, useApi } from '../lib/api';
import { colors } from '../lib/theme';

const confirm = (message: string) =>
  Platform.OS === 'web'
    ? Promise.resolve(window.confirm(message))
    : new Promise<boolean>((resolve) =>
        Alert.alert('Confirm', message, [
          { text: 'Cancel', style: 'cancel', onPress: () => resolve(false) },
          { text: 'Yes', onPress: () => resolve(true) },
        ]),
      );

// IR and Taxi from My Team. One IR player at a time; an IR player counts half
// salary. Taxi is set when a rookie is drafted, so it is shown read only here.
export default function InjuredReserve() {
  const { data, error, loading, reload } = useApi<TeamPage>('/my-team');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const { ir, taxi, starters, bench } = data.roster;
  const current = ir[0];
  const injuredFirst = (p: Player) => (p.injury ? 0 : 1);
  const candidates = [...starters, ...bench].sort((a, b) => injuredFirst(a) - injuredFirst(b) || a.name.localeCompare(b.name));

  const run = async (action: () => Promise<unknown>, done: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await action();
      setMessage(done);
      await reload();
    } catch (e: any) {
      setMessage(e?.message ?? 'Something went wrong.');
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!current || !(await confirm(`Take ${current.name} off IR? His full salary counts again.`))) return;
    run(() => apiSend('DELETE', '/my-team/ir'), `${current.name} is off IR.`);
  };

  const place = async (p: Player) => {
    if (!(await confirm(`Move ${p.name} to IR? He'll count ${money(p.contract_salary / 2)} instead of ${money(p.contract_salary)}.`))) return;
    run(() => apiSend('POST', '/my-team/ir', { player_id: p.player_id }), `${p.name} is on IR.`);
  };

  return (
    <Screen loading={loading} onRefresh={reload}>
      <Back label="My Team" />
      <Title>IR &amp; Taxi</Title>
      {message ? <Text style={styles.message}>{message}</Text> : null}
      {busy ? <ActivityIndicator color={colors.gold} style={styles.spinner} /> : null}

      <Card title="IR · 1 spot" right="counts half salary" gold>
        {current ? (
          <>
            <Row first onPress={() => router.push(`/player/${current.player_id}`)}>
              <Pos label={current.pos} />
              <View style={ui.flex}>
                <Text style={ui.name}>{current.name}</Text>
                <Text style={ui.sub}>{current.injury ?? 'On IR'}</Text>
              </View>
              <Text style={ui.value}>{money(current.contract_salary)} → {money(current.contract_salary / 2)}</Text>
            </Row>
            <Pressable disabled={busy} onPress={remove} style={styles.button}>
              <Text style={styles.buttonText}>Take off IR</Text>
            </Pressable>
            <Note>To swap, take him off first, then pick the new player below.</Note>
          </>
        ) : (
          <Text style={ui.sub}>Open. Pick a player below.</Text>
        )}
      </Card>

      {!current ? (
        <Card title="Move to IR" right="injured first">
          {candidates.map((p, i) => (
            <Row key={p.player_id} first={i === 0} onPress={busy ? undefined : () => place(p)}>
              <Pos label={p.pos} />
              <View style={ui.flex}>
                <View style={styles.nameLine}>
                  <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
                  {p.injury ? <Pill label={p.injury.toUpperCase()} tone="out" /> : null}
                </View>
                <Text style={ui.sub}>{p.nfl_team}</Text>
              </View>
              <Text style={ui.link}>{money(p.contract_salary)} ›</Text>
            </Row>
          ))}
        </Card>
      ) : null}

      <Card title="Taxi" right="set at the rookie draft">
        {taxi.length ? taxi.map((p, i) => (
          <Row key={p.player_id} first={i === 0}>
            <Pos label={p.pos} />
            <View style={ui.flex}>
              <Text style={ui.name}>{p.name}</Text>
              <Text style={ui.sub}>Locked for the season</Text>
            </View>
            <Text style={ui.value}>{money(p.contract_salary)} → {money(p.salary)}</Text>
          </Row>
        )) : <Text style={ui.sub}>No taxi player this season.</Text>}
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  message: { color: colors.gold, fontSize: 14, marginBottom: 10 },
  spinner: { marginBottom: 10 },
  nameLine: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  button: { marginTop: 10, borderWidth: 1, borderColor: colors.line, borderRadius: 12, padding: 12, alignItems: 'center' },
  buttonText: { color: colors.text, fontWeight: '700' },
});
