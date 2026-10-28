import Ionicons from '@expo/vector-icons/Ionicons';
import { Tabs } from 'expo-router';
import type { ComponentProps } from 'react';
import { StyleSheet, Text, View, type ColorValue } from 'react-native';

import { colors } from '../../lib/theme';

type IconName = ComponentProps<typeof Ionicons>['name'];

const icon = (name: IconName) => ({ color, size }: { color: ColorValue; size: number }) => (
  <Ionicons name={name} color={color as string} size={size} />
);

// The raised gold GM button in the middle of the tab bar.
function FrontOfficeIcon({ focused }: { focused: boolean }) {
  return (
    <View style={[styles.gm, focused && styles.gmOn]}>
      <Text style={styles.gmText}>GM</Text>
    </View>
  );
}

// Bottom tabs from the approved design: Home, My Team, Front Office (middle),
// League, Market. Each screen draws its own title.
export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: { backgroundColor: colors.card, borderTopColor: colors.card },
        tabBarActiveTintColor: colors.gold,
        tabBarInactiveTintColor: colors.muted,
        sceneStyle: { backgroundColor: colors.bg },
      }}
    >
      <Tabs.Screen name="index" options={{ title: 'Home', tabBarIcon: icon('home') }} />
      <Tabs.Screen name="my-team" options={{ title: 'My Team', tabBarIcon: icon('shirt') }} />
      <Tabs.Screen name="front-office" options={{ title: 'Front Office', tabBarIcon: ({ focused }) => <FrontOfficeIcon focused={focused} /> }} />
      <Tabs.Screen name="league" options={{ title: 'League', tabBarIcon: icon('trophy') }} />
      <Tabs.Screen name="market" options={{ title: 'Market', tabBarIcon: icon('cash') }} />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  gm: {
    width: 54, height: 54, borderRadius: 27, backgroundColor: colors.gold, marginTop: -26,
    alignItems: 'center', justifyContent: 'center', borderWidth: 4, borderColor: colors.bg,
  },
  gmOn: { borderColor: colors.gold },
  gmText: { color: colors.bg, fontWeight: '900', fontSize: 18 },
});
