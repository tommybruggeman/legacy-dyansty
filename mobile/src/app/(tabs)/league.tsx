import { useLocalSearchParams, useNavigation } from 'expo-router';
import { useEffect, useState } from 'react';

import { MatchupsView } from '../../components/league/MatchupsView';
import { StandingsView } from '../../components/league/StandingsView';
import { TeamsView } from '../../components/league/TeamsView';
import { Screen, Segmented, SwipeTabs, Title } from '../../components/ui';

const VIEWS = ['Standings', 'Matchups', 'Teams'] as const;

export default function League() {
  const [view, setView] = useState<(typeof VIEWS)[number]>('Matchups');
  const navigation = useNavigation();
  const { at, tab } = useLocalSearchParams<{ at?: string; tab?: string }>();
  // Opening League from the tab bar starts on Matchups; Home's standings card opens Standings (its bracket, Matchups);
  // coming back from a game or team page keeps the view you were on.
  // The reset happens while League is hidden (another tab was picked), not on the tap itself, so the tab
  // never opens on the old view and then swaps to Matchups in front of you. Opening a game or team page on
  // top doesn't change tabs, so the view you were on is kept for the trip back.
  useEffect(
    () =>
      navigation.addListener('state' as never, (e: any) => {
        const state = e?.data?.state;
        const current = state?.routes?.[state.index]?.name;
        if (current && current !== 'league') setView('Matchups');
      }),
    [navigation],
  );
  useEffect(() => {
    if (at) setView(tab === 'Matchups' ? 'Matchups' : 'Standings');
  }, [at, tab]);
  return (
    <Screen>
      <Title>League</Title>
      <Segmented options={VIEWS} value={view} onChange={setView} />
      <SwipeTabs
        options={VIEWS}
        value={view}
        onChange={setView}
        render={(v) => (v === 'Standings' ? <StandingsView /> : v === 'Matchups' ? <MatchupsView /> : <TeamsView />)}
      />
    </Screen>
  );
}
