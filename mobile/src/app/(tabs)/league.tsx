import { useState } from 'react';

import { MatchupsView } from '../../components/league/MatchupsView';
import { StandingsView } from '../../components/league/StandingsView';
import { Screen, Segmented, Title } from '../../components/ui';

// Team pages open from a tap on a Standings row.
const VIEWS = ['Standings', 'Matchups'] as const;

export default function League() {
  const [view, setView] = useState<(typeof VIEWS)[number]>('Standings');
  return (
    <Screen>
      <Title>League</Title>
      <Segmented options={VIEWS} value={view} onChange={setView} />
      {view === 'Standings' ? <StandingsView /> : <MatchupsView />}
    </Screen>
  );
}
