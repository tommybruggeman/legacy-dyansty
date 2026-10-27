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

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, backgroundColor: colors.bg },
  error: { color: colors.muted, fontSize: 15, textAlign: 'center' },
  retry: { marginTop: 12, padding: 10 },
  retryText: { color: colors.gold, fontSize: 15, fontWeight: '700' },
});
