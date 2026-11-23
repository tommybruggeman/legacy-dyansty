import { router } from 'expo-router';
import { useState } from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';
import { Pressable } from '../Pressable';

import { type Prospect, type ProspectPage, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const editionDay = (iso: string) => {
  const [y, m, d] = iso.split('-').map(Number);
  return `${MONTHS[(m || 1) - 1]} ${d}, ${y}`;
};

const initials = (name: string) => name.split(' ').filter(Boolean).map((w) => w[0]).slice(0, 2).join('').toUpperCase();

// A prospect's college headshot, or his initials on a gold-green tile when there isn't one.
export function ProspectPhoto({ p, size, square }: { p: Prospect; size: number; square?: boolean }) {
  const [failed, setFailed] = useState(false);
  const box = { width: size, height: size, borderRadius: square ? 12 : size / 2 };
  if (p.photo && !failed) {
    return <Image source={{ uri: p.photo }} style={[box, styles.photoBg]} onError={() => setFailed(true)} />;
  }
  return (
    <View style={[box, styles.photoBg, styles.center]}>
      <Text style={[styles.initials, { fontSize: size * 0.34 }]}>{initials(p.name)}</Text>
    </View>
  );
}

export function Move({ p }: { p: Prospect }) {
  if (p.move === 'up') return <Text style={styles.up}>▲{p.was ? ` ${p.was - p.rank}` : ''}</Text>;
  if (p.move === 'down') return <Text style={styles.down}>▼{p.was ? ` ${p.rank - p.was}` : ''}</Text>;
  if (p.move === 'new') return <Text style={styles.newTag}>NEW</Text>;
  return <Text style={styles.same}>–</Text>;
}

// Home card for the week's Prospect Power Rankings: headline and the top three; tap opens the article.
export function ProspectCard() {
  const { data } = useApi<ProspectPage>('/prospects/rankings');
  const ed = data?.edition;
  if (!ed || !ed.players.length) return null;
  return (
    <Pressable style={styles.card} onPress={() => router.push('/prospects')}>
      <View style={styles.between}>
        <Text style={styles.eyebrow}>PROSPECT POWER RANKINGS</Text>
        <Text style={styles.no}>NO. {ed.number}</Text>
      </View>
      <Text style={styles.head}>{ed.headline}</Text>
      {ed.dek ? <Text style={styles.dek} numberOfLines={2}>{ed.dek}</Text> : null}
      <Text style={styles.by}>Legacy GM · {editionDay(ed.date)} · {ed.players.length} prospects</Text>
      <View style={styles.top}>
        {ed.players.slice(0, 3).map((p) => (
          <View key={p.athlete_id} style={styles.mini}>
            <ProspectPhoto p={p} size={36} />
            <Text style={styles.rank}>{p.rank}</Text>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={styles.name} numberOfLines={1}>{p.name}</Text>
              <Text style={styles.sub} numberOfLines={1}>{p.pos} · {p.school}</Text>
            </View>
            <Move p={p} />
          </View>
        ))}
      </View>
      <Text style={styles.more}>{data.starred.length ? `★ ${data.starred.length} starred · ` : ''}Read the rankings ›</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: colors.row, borderRadius: 16, borderWidth: 1, borderColor: colors.line, padding: 14, marginTop: 14 },
  between: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  eyebrow: { color: colors.gold, fontSize: 11, fontWeight: '900', letterSpacing: 1.4 },
  no: { color: colors.muted, fontSize: 10, fontWeight: '900', letterSpacing: 1 },
  head: { color: colors.text, fontSize: 20, fontWeight: '900', lineHeight: 23, marginTop: 8 },
  dek: { color: colors.muted, fontSize: 13, lineHeight: 18, marginTop: 6 },
  by: { color: colors.muted, opacity: 0.75, fontSize: 11, fontWeight: '700', marginTop: 8 },
  top: { gap: 6, marginTop: 10 },
  mini: { flexDirection: 'row', alignItems: 'center', gap: 10, backgroundColor: colors.card, borderRadius: 10, paddingVertical: 8, paddingHorizontal: 10 },
  rank: { color: colors.gold, fontWeight: '900', fontSize: 16, width: 18, textAlign: 'center' },
  name: { color: colors.text, fontSize: 13, fontWeight: '800' },
  sub: { color: colors.muted, fontSize: 11 },
  more: { color: colors.gold, fontWeight: '800', fontSize: 13, marginTop: 10, textAlign: 'right' },
  photoBg: { backgroundColor: '#2C2512' },
  center: { alignItems: 'center', justifyContent: 'center' },
  initials: { color: colors.gold, fontWeight: '900' },
  up: { color: colors.live, fontSize: 11, fontWeight: '900' },
  down: { color: '#E08A8A', fontSize: 11, fontWeight: '900' },
  newTag: { color: colors.muted, fontSize: 9, fontWeight: '900', letterSpacing: 0.6, opacity: 0.7 },
  same: { color: colors.muted, fontSize: 11, opacity: 0.6 },
});
