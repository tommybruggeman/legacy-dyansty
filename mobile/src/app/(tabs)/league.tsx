import { useLocalSearchParams, useNavigation } from 'expo-router';
import { useEffect, useState } from 'react';

import { ActivityView } from '../../components/league/ActivityView';
import { MatchupsView } from '../../components/league/MatchupsView';
import { StandingsView } from '../../components/league/StandingsView';
import { TeamsView } from '../../components/league/TeamsView';
import { Screen, Segmented, SwipeTabs, Title } from '../../components/ui';

const VIEWS = ['Standings', 'Matchups', 'Teams', 'Activity'] as const;

export default function League() {
  const [view, setView] = useState<(typeof VIEWS)[number]>('Matchups');
  const navigation = useNavigation();
  const { at, view: asked } = useLocalSearchParams<{ at?: string; view?: string }>();
  // Opening League from the tab bar starts on Matchups; Home's standings card opens Standings and its
  // "Latest in the league" strip opens Activity; coming back from a game or team page keeps the view you were on.
  useEffect(() => navigation.addListener('tabPress' as never, () => setView('Matchups')), [navigation]);
  useEffect(() => {
    if (at) setView(asked === 'Activity' ? 'Activity' : 'Standings');
  }, [at, asked]);
  return (
    <Screen>
      <Title>League</Title>
      <Segmented options={VIEWS} value={view} onChange={setView} />
      <SwipeTabs
        options={VIEWS}
        value={view}
        onChange={setView}
        render={(v) => (v === 'Standings' ? <StandingsView /> : v === 'Matchups' ? <MatchupsView /> : v === 'Teams' ? <TeamsView /> : <ActivityView />)}
      />
    </Screen>
  );
}
