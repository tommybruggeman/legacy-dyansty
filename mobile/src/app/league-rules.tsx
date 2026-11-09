import { StyleSheet, Text, View } from 'react-native';

import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Card, Screen, Title } from '../components/ui';
import { type RulesView, useApi } from '../lib/api';
import { colors } from '../lib/theme';

// The league's rules for everyone to read: the same settings that drive cap math, scoring and the GM.
export default function LeagueRules() {
  const { data, error, loading, reload } = useApi<RulesView>('/league/rules');
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;
  return (
    <Screen>
      <Back label="Settings" />
      <Title>League rules</Title>
      {data.groups.map((g) => (
        <Card key={g.title} title={g.title}>
          {g.rows.map(([label, value], i) => (
            <View key={label} style={[styles.row, i === 0 && styles.first]}>
              <Text style={styles.label}>{label}</Text>
              <Text style={styles.value}>{value}</Text>
            </View>
          ))}
        </Card>
      ))}
      {data.house_rules ? (
        <Card title="House rules">
          <Text style={styles.house}>{data.house_rules}</Text>
        </Card>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', justifyContent: 'space-between', gap: 12, paddingVertical: 9, borderTopWidth: 1, borderTopColor: colors.card },
  first: { borderTopWidth: 0 },
  label: { color: colors.muted, fontSize: 14, flex: 1 },
  value: { color: colors.text, fontSize: 14, fontWeight: '700', textAlign: 'right', flexShrink: 1 },
  house: { color: colors.text, fontSize: 14, lineHeight: 20 },
});
