import { router } from 'expo-router';
import { Pressable, StyleSheet, Text } from 'react-native';

import { type Transactions, useApi } from '../../lib/api';

// "3 Pending ›" next to your name on My Team: offers to answer, trades you sent and bids.
// Hidden when nothing is pending (and always while trades in the app are off).
export function PendingBadge() {
  const { data } = useApi<Transactions>('/transactions');
  if (!data?.enabled || !data.pending) return null;
  return (
    <Pressable onPress={() => router.push('/transactions')} hitSlop={8} style={styles.badge}>
      <Text style={styles.text}>{`${data.pending} Pending ›`}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  badge: { backgroundColor: '#D64545', borderRadius: 11, paddingHorizontal: 8, paddingVertical: 3 },
  text: { color: '#fff', fontSize: 12, fontWeight: '900' },
});
