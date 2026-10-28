import { StyleSheet, Text, View } from 'react-native';

import { POS_COLORS, teamColors } from '../lib/nflColors';
import { colors } from '../lib/theme';
import { initialsOf } from './Pictures';

// Player circle in their NFL team's colors (primary fill, secondary ring) with a position badge.
export function PlayerFace({ name, team, pos, size = 44, hurt }: { name: string; team?: string | null; pos: string; size?: number; hurt?: boolean }) {
  const [fill, ring] = teamColors(team);
  return (
    <View style={{ width: size, height: size }}>
      <View style={[styles.circle, { width: size, height: size, borderRadius: size / 2, backgroundColor: fill, borderColor: ring, borderWidth: size >= 48 ? 3 : 2 }]}>
        <Text style={[styles.initials, { fontSize: Math.round(size * 0.34) }]}>{initialsOf(name)}</Text>
      </View>
      <Text style={[styles.badge, { backgroundColor: POS_COLORS[pos] ?? colors.muted }]}>{pos || '—'}</Text>
      {hurt ? <View style={styles.hurt} /> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  circle: { alignItems: 'center', justifyContent: 'center' },
  initials: { color: '#FFFFFF', fontWeight: '800' },
  badge: {
    position: 'absolute', bottom: -5, alignSelf: 'center', color: colors.bg, fontSize: 9, fontWeight: '800',
    borderRadius: 6, overflow: 'hidden', paddingHorizontal: 5, paddingVertical: 1,
  },
  hurt: { position: 'absolute', top: -1, right: -1, width: 12, height: 12, borderRadius: 6, backgroundColor: '#E66A6A', borderWidth: 2, borderColor: colors.row },
});
