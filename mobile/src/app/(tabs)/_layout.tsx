import { Tabs } from 'expo-router';

import { colors } from '../../lib/theme';

// Bottom tabs from the plan: Home, Matchups, My Team, League, Front Office.
export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: colors.bg },
        headerTintColor: colors.text,
        tabBarStyle: { backgroundColor: colors.card, borderTopColor: colors.card },
        tabBarActiveTintColor: colors.gold,
        tabBarInactiveTintColor: colors.muted,
        sceneStyle: { backgroundColor: colors.bg },
      }}
    >
      <Tabs.Screen name="index" options={{ title: 'Home' }} />
      <Tabs.Screen name="matchups" options={{ title: 'Matchups' }} />
      <Tabs.Screen name="my-team" options={{ title: 'My Team' }} />
      <Tabs.Screen name="league" options={{ title: 'League' }} />
      <Tabs.Screen name="front-office" options={{ title: 'Front Office' }} />
    </Tabs>
  );
}
