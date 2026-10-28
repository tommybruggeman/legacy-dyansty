import { useState } from 'react';

import { MatchupsView } from '../../components/league/MatchupsView';
import { StandingsView } from '../../components/league/StandingsView';
import { TeamsView } from '../../components/league/TeamsView';
import { Screen, Segmented, Title } from '../../components/ui';

const VIEWS = ['Standings', 'Matchups', 'Teams'] as const;

export default function League() {
  const [view, setView] = useState<(typeof VIEWS)[number]>('Standings');
  return (
    <Screen>
      <Title>League</Title>
      <Segmented options={VIEWS} value={view} onChange={setView} />
      {view === 'Standings' ? <StandingsView /> : view === 'Matchups' ? <MatchupsView /> : <TeamsView />}
    </Screen>
  );
}
