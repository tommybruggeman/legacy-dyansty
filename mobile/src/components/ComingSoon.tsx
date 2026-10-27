import { StyleSheet, Text, View } from 'react-native';

import { colors } from '../lib/theme';

export function ComingSoon({ title, note }: { title: string; note: string }) {
  return (
    <View style={styles.screen}>
      <Text style={styles.title}>{title}</Text>
      <Text style={styles.note}>{note}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg, alignItems: 'center', justifyContent: 'center', padding: 24 },
  title: { color: colors.text, fontSize: 22, fontWeight: '700', marginBottom: 8 },
  note: { color: colors.muted, fontSize: 15, textAlign: 'center' },
});
