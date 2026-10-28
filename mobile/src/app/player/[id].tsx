import { router, useLocalSearchParams } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { ErrorState, Loading } from '../../components/ScreenState';
import { Back, Card, Note, Pill, Row, Screen, Tile, Tiles, Title, money, ui } from '../../components/ui';
import { type PlayerPage, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

// Any player, from any list: contract, who owns him, and recent weeks in
// this league's scoring.
export default function Player() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, error, loading, reload } = useApi<PlayerPage>(`/players/${id}`);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const c = data.contract;
  const best = Math.max(1, ...data.weeks.map((w) => w.points ?? 0));
  const eyebrow = [data.pos, data.nfl_team, data.age ? `AGE ${Math.floor(data.age)}` : null].filter(Boolean).join(' · ');

  return (
    <Screen loading={loading} onRefresh={reload}>
      <Back label="Back" />
      <Title
        eyebrow={eyebrow}
        right={data.is_mine ? <Pill label="YOURS" tone="gold" /> : data.injury ? <Pill label={data.injury.toUpperCase()} tone="out" /> : undefined}
      >
        {data.name}
      </Title>
      <Tiles>
        <Tile label="SALARY" value={c ? money(c.salary) : 'FA'} sub={c ? `through ${c.through}` : 'not signed'} />
        <Tile label="PPG" value={data.ppg != null ? data.ppg.toFixed(1) : '—'} sub="this season" />
        <Tile label="CAP HIT" value={c ? money(c.cap_hit) : '—'} sub={c?.designation ? `on ${c.designation.toUpperCase()}` : 'this year'} />
      </Tiles>

      <Card title="Recent weeks" right="league scoring">
        <View style={styles.chart}>
          {data.weeks.map((w) => (
            <View key={w.week} style={styles.col}>
              <Text style={styles.pts}>{w.points != null ? w.points.toFixed(0) : ''}</Text>
              <View style={[styles.bar, { height: `${((w.points ?? 0) / best) * 100}%` }, !w.started && styles.benched]} />
              <Text style={styles.wk}>W{w.week}</Text>
            </View>
          ))}
        </View>
        <Note>Faded bars were bench weeks.</Note>
      </Card>

      {data.injury ? (
        <Card title="Injury">
          <Text style={ui.value}>{data.injury}{data.injury_detail ? ` · ${data.injury_detail}` : ''}</Text>
        </Card>
      ) : null}

      <Card title="Contract">
        {c ? (
          <>
            <Row first onPress={() => router.push(`/team/${c.team_id}`)}>
              <Text style={[ui.value, ui.flex]}>Owner</Text>
              <Text style={ui.link}>{c.owner_name} ›</Text>
            </Row>
            <Row>
              <Text style={[ui.value, ui.flex]}>Years left</Text>
              <Text style={ui.value}>{c.years_left}{c.is_rookie ? ' · rookie deal' : ''}</Text>
            </Row>
          </>
        ) : (
          <Text style={ui.sub}>Not under contract. Signings happen in the free agent auction on the website.</Text>
        )}
      </Card>
      {data.is_mine ? (
        <Pressable onPress={() => router.push('/ir')} style={styles.button}>
          <Text style={styles.buttonText}>Manage IR</Text>
        </Pressable>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  chart: { flexDirection: 'row', alignItems: 'flex-end', height: 120, gap: 6, marginTop: 8 },
  col: { flex: 1, height: '100%', justifyContent: 'flex-end', alignItems: 'center' },
  bar: { width: '100%', backgroundColor: colors.gold, borderTopLeftRadius: 4, borderTopRightRadius: 4, minHeight: 2 },
  benched: { opacity: 0.35 },
  pts: { color: colors.muted, fontSize: 10, marginBottom: 2 },
  wk: { color: colors.muted, fontSize: 10, marginTop: 4 },
  button: { borderWidth: 1, borderColor: colors.line, borderRadius: 14, padding: 14, alignItems: 'center' },
  buttonText: { color: colors.text, fontWeight: '700', fontSize: 15 },
});
