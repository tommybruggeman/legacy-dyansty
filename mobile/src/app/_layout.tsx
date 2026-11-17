import { useEffect, useState } from 'react';
import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';

import { LaunchIntro } from '../components/LaunchIntro';
import { AppLockProvider } from '../lib/appLock';
import { listenForTaps, registerIfAllowed } from '../lib/push';
import { SessionProvider, useSession } from '../lib/session';
import { installCrashReporter, useScreenTime } from '../lib/telemetry';
import { colors } from '../lib/theme';

export { ScreenError as ErrorBoundary } from '../components/ScreenState';

// The native splash is just the background color; the crest animation takes over from it.
SplashScreen.preventAutoHideAsync().catch(() => {});
installCrashReporter();

function RootStack() {
  const { session, loading } = useSession();
  const signedIn = !!session;
  useScreenTime(signedIn);
  useEffect(() => {
    if (!signedIn) return;
    registerIfAllowed();
    return listenForTaps();
  }, [signedIn]);
  if (loading) return null;

  return (
    <AppLockProvider active={signedIn}>
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
        <Stack.Screen name="delete-account" />
        <Stack.Screen name="profile" />
        <Stack.Screen name="league-rules" />
        <Stack.Screen name="commissioner" />
        <Stack.Screen name="commish-team" />
        <Stack.Screen name="commish-add" />
        <Stack.Screen name="commish-rules" />
        <Stack.Screen name="search" options={{ animation: 'fade_from_bottom' }} />
      </Stack.Protected>
      <Stack.Protected guard={!session}>
        <Stack.Screen name="sign-in" />
      </Stack.Protected>
    </Stack>
    </AppLockProvider>
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
