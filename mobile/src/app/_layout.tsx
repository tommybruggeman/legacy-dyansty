import { useState } from 'react';
import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';

import { LaunchIntro } from '../components/LaunchIntro';
import { SessionProvider, useSession } from '../lib/session';
import { colors } from '../lib/theme';

export { ScreenError as ErrorBoundary } from '../components/ScreenState';

// The native splash is just the background color; the crest animation takes over from it.
SplashScreen.preventAutoHideAsync().catch(() => {});

function RootStack() {
  const { session, loading } = useSession();
  if (loading) return null;

  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.bg } }}>
      <Stack.Protected guard={!!session}>
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="team/[id]" />
        <Stack.Screen name="game/[week]/[matchup]" />
        <Stack.Screen name="player/[id]" />
        <Stack.Screen name="player-games/[id]" />
        <Stack.Screen name="league-cap" />
        <Stack.Screen name="ir" />
        <Stack.Screen name="chats" />
        <Stack.Screen name="report-card" />
        <Stack.Screen name="settings" />
      </Stack.Protected>
      <Stack.Protected guard={!session}>
        <Stack.Screen name="sign-in" />
      </Stack.Protected>
    </Stack>
  );
}

export default function RootLayout() {
  const [intro, setIntro] = useState(true);
  return (
    <SessionProvider>
      <StatusBar style="light" />
      <RootStack />
      {intro ? <LaunchIntro onReady={() => SplashScreen.hideAsync().catch(() => {})} onDone={() => setIntro(false)} /> : null}
    </SessionProvider>
  );
}
