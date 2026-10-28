import { useFocusEffect } from 'expo-router';
import { useCallback, useRef } from 'react';

import { ErrorState, Loading } from '../../components/ScreenState';
import { TeamView } from '../../components/team/TeamView';
import { Screen } from '../../components/ui';
import { type TeamPage, useApi } from '../../lib/api';

// The owner portal: your own team page, with IR moves and the league cap view.
export default function MyTeam() {
  const { data, error, loading, reload } = useApi<TeamPage>('/my-team');

  // Coming back from an IR move should show the change.
  const seen = useRef(false);
  useFocusEffect(useCallback(() => {
    if (seen.current) reload();
    seen.current = true;
  }, [reload]));

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  return (
    <Screen loading={loading} onRefresh={reload}>
      <TeamView team={data} eyebrow="OWNER PORTAL" />
    </Screen>
  );
}
