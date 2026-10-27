import Ionicons from '@expo/vector-icons/Ionicons';
import { Tabs } from 'expo-router';
import type { ComponentProps } from 'react';
import type { ColorValue } from 'react-native';

import { colors } from '../../lib/theme';

type IconName = ComponentProps<typeof Ionicons>['name'];

const icon = (name: IconName) => ({ color, size }: { color: ColorValue; size: number }) => (
  <Ionicons name={name} color={color as string} size={size} />
);

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
      <Tabs.Screen name="index" options={{ title: 'Home', tabBarIcon: icon('home') }} />
      <Tabs.Screen name="matchups" options={{ title: 'Matchups', headerShown: false, tabBarIcon: icon('american-football') }} />
      <Tabs.Screen name="my-team" options={{ title: 'My Team', tabBarIcon: icon('shirt') }} />
      <Tabs.Screen name="league" options={{ title: 'League', headerShown: false, tabBarIcon: icon('trophy') }} />
      <Tabs.Screen name="front-office" options={{ title: 'Front Office', tabBarIcon: icon('chatbubbles') }} />
    </Tabs>
  );
}
