import { router } from 'expo-router';
import { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Bar, Card, Row, Screen, Segmented, Title, money, ui } from '../components/ui';
import { type TeamList, useApi } from '../lib/api';
import { colors } from '../lib/theme';

// Every owner's cap space this year and the next two, from My Team → Contracts.
export default function LeagueCap() {
  const { data, error, loading, reload } = useApi<TeamList>('/teams');
  const [year, setYear] = useState<string | null>(null);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const seasons = (data.teams[0]?.cap_by_year ?? []).map((y) => String(y.season));
  const selected = year ?? seasons[0];
  const index = Math.max(0, seasons.indexOf(selected));
  const rows = [...data.teams].sort((a, b) => (b.cap_by_year[index]?.space ?? 0) - (a.cap_by_year[index]?.space ?? 0));

  return (
    <Screen loading={loading} onRefresh={reload}>
      <Back label="Contracts" />
      <Title>League cap</Title>
      <Segmented options={seasons} value={selected} onChange={setYear} />
      <Card title={`Cap space in ${selected}`} right="space left">
        {rows.map((t, i) => {
          const y = t.cap_by_year[index];
          return (
            <Row key={t.team_id} first={i === 0} onPress={() => router.push(`/team/${t.team_id}`)}>
              <Text style={[styles.rank, t.is_me && ui.gold]}>{i + 1}</Text>
              <View style={ui.flex}>
                <Text style={[ui.name, t.is_me && ui.gold]} numberOfLines={1}>{t.owner_name}</Text>
                <View style={styles.bar}><Bar fraction={(y?.space ?? 0) / data.salary_cap} /></View>
              </View>
              <Text style={[styles.amount, (y?.space ?? 0) < 0 && { color: colors.danger }]}>{money(y?.space)}</Text>
            </Row>
          );
        })}
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  rank: { width: 20, color: colors.muted, fontWeight: '700' },
  bar: { marginTop: 6 },
  amount: { width: 76, textAlign: 'right', color: colors.text, fontSize: 15, fontWeight: '700' },
});
