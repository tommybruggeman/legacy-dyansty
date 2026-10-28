import { router, type ErrorBoundaryProps } from 'expo-router';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { colors } from '../lib/theme';

export function Loading() {
  return (
    <View style={styles.center}>
      <ActivityIndicator color={colors.gold} />
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
  title: { color: colors.text, fontSize: 17, fontWeight: '800', marginBottom: 8 },
  actions: { flexDirection: 'row', gap: 16 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, backgroundColor: colors.bg },
  error: { color: colors.muted, fontSize: 15, textAlign: 'center' },
  retry: { marginTop: 12, padding: 10 },
  retryText: { color: colors.gold, fontSize: 15, fontWeight: '700' },
});
