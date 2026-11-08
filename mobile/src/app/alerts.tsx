import { useEffect, useState } from 'react';
import { Linking, Pressable, StyleSheet, Switch, Text, View } from 'react-native';

import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Card, Note, Row, Screen, Title, ui } from '../components/ui';
import { apiSend, useApi } from '../lib/api';
import { alertsAllowed, registerForPush } from '../lib/push';
import { colors } from '../lib/theme';

type Kind = { kind: string; label: string; note: string; on: boolean };
type AlertSettings = { kinds: Kind[]; tz: string | null; quiet_hours: string; weekly_limit: number };

// Settings > Alerts: one switch per kind of alert (the website has the same switches), quiet hours, and a test.
export default function Alerts() {
  const { data, error, loading, reload } = useApi<AlertSettings>('/me/alerts');
  const [kinds, setKinds] = useState<Kind[] | null>(null);
  const [allowed, setAllowed] = useState(true);
  const [note, setNote] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => setKinds(data?.kinds ?? null), [data]);
  useEffect(() => {
    alertsAllowed().then(setAllowed);
  }, []);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data || !kinds) return null;

  const flip = async (kind: string, on: boolean) => {
    const next = kinds.map((k) => (k.kind === kind ? { ...k, on } : k));
    setKinds(next);
    try {
      await apiSend('POST', '/me/alerts', { off: next.filter((k) => !k.on).map((k) => k.kind) });
    } catch (e) {
      setKinds(kinds);
      setNote(e instanceof Error ? e.message : 'That change wasn’t saved. Try again.');
    }
  };

  const test = async () => {
    setTesting(true);
    setNote(null);
    try {
      await registerForPush();
      const res = await apiSend<{ message: string }>('POST', '/me/alerts/test');
      setNote(res.message);
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'The test didn’t go out. Try again.');
    } finally {
      setTesting(false);
    }
  };

  return (
    <Screen loading={loading}>
      <Back label="Settings" />
      <Title>Alerts</Title>
      {!allowed ? (
        <Card gold>
          <Text style={ui.name}>Alerts are off for this phone</Text>
          <Note>Turn on notifications for Legacy Dynasty in your iPhone’s Settings to get them.</Note>
          <Pressable onPress={() => Linking.openSettings()} style={styles.button}>
            <Text style={styles.buttonText}>Open iPhone Settings</Text>
          </Pressable>
        </Card>
      ) : null}
      <Card>
        {kinds.map((k, i) => (
          <Row key={k.kind} first={i === 0}>
            <View style={ui.flex}>
              <Text style={ui.value}>{k.label}</Text>
              <Text style={ui.sub}>{k.note}</Text>
            </View>
            <Switch value={k.on} onValueChange={(on) => flip(k.kind, on)} trackColor={{ true: colors.green, false: colors.card }} />
          </Row>
        ))}
      </Card>
      <Card>
        <Note>
          Quiet hours: nothing arrives {data.quiet_hours} where you are, except a starter ruled inactive right before kickoff.
          At most {data.weekly_limit} alerts a week (over-the-cap warnings don’t count).
        </Note>
        <Pressable onPress={test} disabled={testing} style={[styles.button, testing && styles.dim]}>
          <Text style={styles.buttonText}>{testing ? 'Sending…' : 'Send me a test'}</Text>
        </Pressable>
        {note ? <Note>{note}</Note> : null}
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  button: { alignSelf: 'flex-start', backgroundColor: colors.gold, borderRadius: 10, paddingVertical: 9, paddingHorizontal: 14, marginTop: 10 },
  buttonText: { color: colors.bg, fontWeight: '800', fontSize: 14 },
  dim: { opacity: 0.6 },
});
