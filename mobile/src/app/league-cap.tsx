import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Card, Screen, Title, money, ui } from '../components/ui';
import { type TeamList, useApi } from '../lib/api';
import { colors } from '../lib/theme';

// Heat grid (Tommy's pick): every owner's cap space for this season and the next three in one place.
// Darker cells are tighter, red is over the cap. Tap a year to sort by it, tap an owner for their team.
export default function LeagueCap() {
  const { data, error, loading, reload } = useApi<TeamList>('/teams');
  const [sortBy, setSortBy] = useState(0);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const seasons = (data.teams[0]?.cap_by_year ?? []).map((y) => y.season);
  const rows = [...data.teams].sort((a, b) => (b.cap_by_year[sortBy]?.space ?? 0) - (a.cap_by_year[sortBy]?.space ?? 0));

  return (
    <Screen loading={loading} onRefresh={reload}>
      <Back label="Contracts" />
      <Title>League cap</Title>
      <Card>
        <View style={styles.head}>
          <Text style={[styles.headText, ui.flex]}>SPACE LEFT</Text>
          {seasons.map((s, i) => (
            <Pressable key={s} onPress={() => setSortBy(i)} hitSlop={6} style={styles.cell}>
              <Text style={[styles.headText, i === sortBy && ui.gold]}>{s}{i === sortBy ? ' ▾' : ''}</Text>
            </Pressable>
          ))}
        </View>
        {rows.map((t) => (
          <Pressable key={t.team_id} onPress={() => router.push(`/team/${t.team_id}`)} style={styles.row}>
            <Text style={[ui.name, ui.flex, t.is_me && ui.gold]} numberOfLines={1}>{t.owner_name.split(' ')[0]}</Text>
            {seasons.map((s, i) => {
              const space = t.cap_by_year[i]?.space ?? 0;
              const [bg, fg] = heat(space, data.salary_cap);
              return (
                <View key={s} style={[styles.cell, styles.box, { backgroundColor: bg }]}>
                  <Text style={[styles.amount, { color: fg }]} numberOfLines={1} adjustsFontSizeToFit>{money(space)}</Text>
                </View>
              );
            })}
          </Pressable>
        ))}
      </Card>
    </Screen>
  );
}

// Shades by how much of the cap is still free: red over the cap, then dark to full gold.
function heat(space: number, cap: number): [string, string] {
  if (space < 0) return [colors.danger, colors.text];
  const share = cap ? space / cap : 0;
  if (share < 0.05) return ['#2A2A1C', colors.text];
  if (share < 0.18) return ['#5C4A22', colors.text];
  if (share < 0.36) return ['#8A6A2C', colors.text];
  return [colors.gold, colors.bg];
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', paddingBottom: 8 },
  headText: { color: colors.muted, fontSize: 10, fontWeight: '800', letterSpacing: 1 },
  row: { flexDirection: 'row', alignItems: 'center', paddingVertical: 6, borderTopWidth: 1, borderTopColor: colors.row },
  cell: { width: 58, marginLeft: 5, alignItems: 'center' },
  box: { borderRadius: 7, paddingVertical: 6, paddingHorizontal: 3 },
  amount: { fontSize: 12, fontWeight: '800' },
});
