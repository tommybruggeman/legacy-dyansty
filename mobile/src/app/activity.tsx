import { useLocalSearchParams } from 'expo-router';

import { ActivityView } from '../components/league/ActivityView';
import { Back, Screen, Title } from '../components/ui';

// Everything that happens in the league, from League > Teams → "See all" or a trade alert (focus = that trade).
export default function Activity() {
  const { focus } = useLocalSearchParams<{ focus?: string }>();
  return (
    <Screen>
      <Back label="League" />
      <Title>Activity</Title>
      <ActivityView focus={focus} />
    </Screen>
  );
}
