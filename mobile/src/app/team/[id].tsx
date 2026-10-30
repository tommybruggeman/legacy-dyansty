import { useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';

import { ErrorState, Loading } from '../../components/ScreenState';
import { LIVE_REFRESH_MS, TeamView, isLive } from '../../components/team/TeamView';
import { Back, Screen } from '../../components/ui';
import { type TeamPage, useApi } from '../../lib/api';

export { ScreenError as ErrorBoundary } from '../../components/ScreenState';

export default function Team() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [live, setLive] = useState(false);
  const { data, error, loading, reload } = useApi<TeamPage>(`/teams/${id}`, live ? LIVE_REFRESH_MS : 0);
  useEffect(() => setLive(isLive(data)), [data]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  return (
    <Screen loading={loading} onRefresh={reload}>
      <Back label="League" />
      <TeamView team={data} />
    </Screen>
  );
}
