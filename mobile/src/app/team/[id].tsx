import { useLocalSearchParams } from 'expo-router';

import { ErrorState, Loading } from '../../components/ScreenState';
import { TeamView } from '../../components/team/TeamView';
import { Back, Screen } from '../../components/ui';
import { type TeamPage, useApi } from '../../lib/api';

export { ScreenError as ErrorBoundary } from '../../components/ScreenState';

export default function Team() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, error, loading, reload } = useApi<TeamPage>(`/teams/${id}`);

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
