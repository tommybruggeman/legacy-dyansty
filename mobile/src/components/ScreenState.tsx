import { router, type ErrorBoundaryProps } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Animated, Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors } from '../lib/theme';

// Loading (Tommy's pick): a first open shows the page's shape as soft pulsing blocks, and the gold wheel
// joins only if it's still loading after 3 seconds. A slow refresh fades the saved page instead (Screen, ui.tsx).
export const SLOW_MS = 3000;

export function useSlow(loading: boolean) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!loading) return setSlow(false);
    const timer = setTimeout(() => setSlow(true), SLOW_MS);
    return () => clearTimeout(timer);
  }, [loading]);
  return slow;
}

export function Loading() {
  const insets = useSafeAreaInsets();
  const slow = useSlow(true);
  const pulse = useRef(new Animated.Value(0.45)).current;
  useEffect(() => {
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(pulse, { toValue: 0.9, duration: 700, useNativeDriver: true }),
      Animated.timing(pulse, { toValue: 0.45, duration: 700, useNativeDriver: true }),
    ]));
    loop.start();
    return () => loop.stop();
  }, [pulse]);
  return (
    <View style={[styles.skeleton, { paddingTop: insets.top + 8 }]}>
      <Animated.View style={{ opacity: pulse }}>
        <View style={[styles.bone, { width: '55%', height: 28, marginBottom: 18 }]} />
        <View style={[styles.block, { height: 120 }]} />
        {[0, 1, 2].map((i) => (
          <View key={i} style={styles.block}>
            <View style={[styles.bone, { width: '40%' }]} />
            <View style={[styles.bone, { width: '85%', marginTop: 10 }]} />
            <View style={[styles.bone, { width: '70%', marginTop: 10 }]} />
          </View>
        ))}
      </Animated.View>
      {slow ? <ActivityIndicator size="large" color={colors.gold} style={[styles.wheel, { top: insets.top + 160 }]} /> : null}
    </View>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <View style={styles.center}>
      <Text style={styles.error}>{message}</Text>
      <Pressable onPress={onRetry} style={styles.retry}>
        <Text style={styles.retryText}>Try again</Text>
      </Pressable>
    </View>
  );
}

// Shown instead of crashing the app when a screen throws while drawing.
// Route files re-export it as ErrorBoundary; the message helps us find the bug.
export function ScreenError({ error, retry }: ErrorBoundaryProps) {
  return (
    <View style={styles.center}>
      <Text style={styles.title}>This page hit a snag</Text>
      <Text style={styles.error}>{error.message}</Text>
      <View style={styles.actions}>
        <Pressable onPress={retry} style={styles.retry}>
          <Text style={styles.retryText}>Try again</Text>
        </Pressable>
        {router.canGoBack() ? (
          <Pressable onPress={() => router.back()} style={styles.retry}>
            <Text style={styles.retryText}>Go back</Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  skeleton: { flex: 1, backgroundColor: colors.bg, paddingHorizontal: 16 },
  block: { backgroundColor: colors.card, borderRadius: 14, padding: 16, marginBottom: 12 },
  bone: { backgroundColor: colors.line, borderRadius: 6, height: 12 },
  wheel: { position: 'absolute', left: 0, right: 0 },
  title: { color: colors.text, fontSize: 17, fontWeight: '800', marginBottom: 8 },
  actions: { flexDirection: 'row', gap: 16 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, backgroundColor: colors.bg },
  error: { color: colors.muted, fontSize: 15, textAlign: 'center' },
  retry: { marginTop: 12, padding: 10 },
  retryText: { color: colors.gold, fontSize: 15, fontWeight: '700' },
});
