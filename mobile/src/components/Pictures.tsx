import { useState } from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';

import { colors } from '../lib/theme';

// Owner pictures and player headshots from Sleeper, with an initials or
// silhouette fallback when there is no picture or it fails to load.

const OWNER_COLORS = ['#D08B6A', '#9AC17A', '#C9A3D6', '#7FA6C9', '#E0C46C', '#8FC3C0', '#D6A3A3', '#A3B8D6', '#C2B280', '#9FD6A3'];

export const initialsOf = (name: string) =>
  name.split(' ').filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join('') || '•';

export function Avatar({ uri, name, size = 24, me }: { uri?: string | null; name: string; size?: number; me?: boolean }) {
  const [failed, setFailed] = useState(false);
  const box = { width: size, height: size, borderRadius: size / 2 };
  if (uri && !failed) {
    return <Image source={{ uri }} style={[box, me && styles.meRing]} onError={() => setFailed(true)} />;
  }
  const tint = me ? colors.gold : OWNER_COLORS[[...name].reduce((s, c) => s + c.charCodeAt(0), 0) % OWNER_COLORS.length];
  return (
    <View style={[box, styles.initials, { backgroundColor: tint }]}>
      <Text style={[styles.initialsText, { fontSize: Math.max(8, size * 0.36) }]}>{initialsOf(name)}</Text>
    </View>
  );
}

export const headshotUri = (playerId: string | null | undefined, pos?: string, team?: string) => {
  if (!playerId) return null;
  if (pos === 'DEF' || !/^\d+$/.test(playerId)) return `https://sleepercdn.com/images/team_logos/nfl/${(team || playerId).toLowerCase()}.png`;
  return `https://sleepercdn.com/content/nfl/players/thumb/${playerId}.jpg`;
};

export type Ring = 'gold' | 'live' | 'done' | 'wait' | 'red' | 'them';
const RING: Record<Ring, string> = {
  gold: colors.gold, live: colors.live, done: '#555', wait: colors.line, red: colors.danger, them: '#9AC17A',
};

export function Headshot({ playerId, pos, team, size = 64, ring = 'gold' }: { playerId?: string | null; pos?: string; team?: string; size?: number; ring?: Ring }) {
  const [failed, setFailed] = useState(false);
  const uri = headshotUri(playerId, pos, team);
  return (
    <View style={{ width: size, height: size, opacity: ring === 'done' ? 0.75 : 1 }}>
      <View style={[styles.shot, { width: size, height: size, borderRadius: size / 2, borderColor: RING[ring] }]}>
        {uri && !failed ? (
          <Image source={{ uri }} style={{ width: size - 4, height: size - 4 }} resizeMode="cover" onError={() => setFailed(true)} />
        ) : (
          <View style={styles.silhouette}>
            <View style={[styles.head, { width: size * 0.32, height: size * 0.32, borderRadius: size * 0.16 }]} />
            <View style={[styles.body, { width: size * 0.7, height: size * 0.36, borderTopLeftRadius: size * 0.35, borderTopRightRadius: size * 0.35 }]} />
          </View>
        )}
      </View>
      {pos && size >= 48 ? <Text style={styles.pos}>{pos}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  meRing: { borderWidth: 1.5, borderColor: colors.gold },
  initials: { alignItems: 'center', justifyContent: 'center' },
  initialsText: { color: colors.bg, fontWeight: '800' },
  shot: { borderWidth: 2, overflow: 'hidden', alignItems: 'center', justifyContent: 'flex-end', backgroundColor: '#1D3A2C' },
  silhouette: { alignItems: 'center', justifyContent: 'flex-end', flex: 1, width: '100%' },
  head: { backgroundColor: '#D9C6A3', marginBottom: 3 },
  body: { backgroundColor: '#3D6B55' },
  pos: {
    position: 'absolute', right: -4, bottom: -3, backgroundColor: colors.gold, color: colors.bg, fontSize: 9, fontWeight: '800',
    borderRadius: 6, overflow: 'hidden', paddingHorizontal: 4, paddingVertical: 1,
  },
});
