import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { StyleSheet, Text } from 'react-native';
import { Pressable } from '../../components/Pressable';

import { ErrorState, Loading } from '../../components/ScreenState';
import { TeamView, refreshEvery } from '../../components/team/TeamView';
import { Back, Screen } from '../../components/ui';
import { type TeamPage, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

export { ScreenError as ErrorBoundary } from '../../components/ScreenState';

export default function Team() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [every, setEvery] = useState(0);
  const { data, error, loading, reload, refresh } = useApi<TeamPage>(`/teams/${id}`, every);
  useEffect(() => setEvery(refreshEvery(data)), [data]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  return (
    <Screen loading={loading}>
      <Back label="League" />
      <TeamView team={data} onChange={refresh} badge={data.is_me ? undefined : (
        // Opens the trade page with this team (or the closed screen after the deadline).
        <Pressable onPress={() => router.push({ pathname: '/trade/[teamId]', params: { teamId: data.team_id, from: data.team_name } })} hitSlop={8}>
          <Text style={styles.trade}>Trade</Text>
        </Pressable>
      )} />
    </Screen>
  );
}

const styles = StyleSheet.create({
  trade: { fontSize: 11, fontWeight: '900', color: colors.bg, backgroundColor: colors.gold, borderRadius: 9, overflow: 'hidden', paddingHorizontal: 9, paddingVertical: 3 },
});
