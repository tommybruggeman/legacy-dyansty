import { useFocusEffect } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';

import { ErrorState, Loading } from '../../components/ScreenState';
import { LIVE_REFRESH_MS, TeamView, isLive } from '../../components/team/TeamView';
import { YourCalls } from '../../components/team/YourCalls';
import { PendingBadge } from '../../components/trade/PendingBadge';
import { Screen } from '../../components/ui';
import { type Expiring, type TeamPage, useApi } from '../../lib/api';

// The owner portal: your own team page, with IR moves, the league cap view and keep-or-walk calls in the offseason.
export default function MyTeam() {
  const [live, setLive] = useState(false);
  const { data, error, loading, reload, refresh } = useApi<TeamPage>('/my-team', live ? LIVE_REFRESH_MS : 0);
  useEffect(() => setLive(isLive(data)), [data]);
  // Keep-or-walk calls, only from the final game to the June 30 rollover and until every one is made.
  const calls = useApi<{ calls: Expiring['calls']; sleeper_league_id: string }>('/my-team/calls');

  // Coming back from an IR move should show the change.
  const seen = useRef(false);
  useFocusEffect(useCallback(() => {
    if (seen.current) refresh();
    seen.current = true;
  }, [refresh]));
  const open = calls.data?.calls;
  const callsCard = open && open.players.some((p) => !p.choice)
    ? <YourCalls calls={open} sleeperId={calls.data!.sleeper_league_id} onChange={calls.refresh} />
    : null;

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  return (
    <Screen loading={loading} onRefresh={reload}>
      <TeamView team={data} eyebrow="OWNER PORTAL" badge={<PendingBadge />} top={callsCard} onChange={refresh} />
    </Screen>
  );
}
