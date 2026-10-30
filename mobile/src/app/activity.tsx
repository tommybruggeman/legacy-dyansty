import { useLocalSearchParams } from 'expo-router';

import { ActivityView } from '../components/league/ActivityView';
import { Back, Screen, Title } from '../components/ui';

// Everything that happens in the league, from League > Teams → "See all", a trade alert (focus = that trade)
// or a dead cap row (player + team + source: the drop or trade that charge came from).
export default function Activity() {
  const { focus, player, team, source } = useLocalSearchParams<{ focus?: string; player?: string; team?: string; source?: string }>();
  return (
    <Screen>
      <Back label="League" />
      <Title>Activity</Title>
      <ActivityView focus={focus} from={player && team ? { player, team, trade: source === 'trade' } : undefined} />
    </Screen>
  );
}
