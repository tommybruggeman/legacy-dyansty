import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import type { PlayerPage } from '../../lib/api';
import { colors } from '../../lib/theme';

// "This week" recap at the top of the player page: a short paragraph written every Tuesday from our own
// NFL stats and injury data (league_ai/writeups.py). Past weeks open underneath.
type Writeup = NonNullable<PlayerPage['writeups']>[number];

function shortDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(`${iso}T12:00:00`);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

export function WriteupCard({ writeups }: { writeups: Writeup[] }) {
  const [open, setOpen] = useState(false);
  const [latest, ...past] = writeups;
  if (!latest) return null;
  const date = shortDate(latest.date);
  return (
    <View style={styles.card}>
      <View style={styles.head}>
        <Text style={styles.tag}>WEEK {latest.week} RECAP</Text>
        {date ? <Text style={styles.date}>{date}</Text> : null}
      </View>
      <Text style={styles.text}>{latest.text}</Text>
      {latest.chips?.length ? (
        <View style={styles.chips}>
          {latest.chips.map((c) => <Text key={c} style={styles.chip}>{c}</Text>)}
        </View>
      ) : null}
      {open ? past.map((w) => (
        <View key={w.week} style={styles.past}>
          <Text style={styles.pastTag}>WEEK {w.week}</Text>
          <Text style={styles.pastText}>{w.text}</Text>
        </View>
      )) : null}
      <View style={styles.foot}>
        <Text style={styles.footText}>Written from our NFL data</Text>
        {past.length ? (
          <Pressable onPress={() => setOpen(!open)} hitSlop={8}>
            <Text style={styles.more}>{open ? 'Hide past weeks' : 'Past weeks ›'}</Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: '#152F23', borderRadius: 16, padding: 16, gap: 10, borderWidth: 1, borderColor: 'rgba(200,155,74,0.35)' },
  head: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  tag: { color: colors.gold, fontSize: 10, fontWeight: '800', letterSpacing: 1.2 },
  date: { color: '#8E8676', fontSize: 11 },
  text: { color: colors.text, fontSize: 14.5, lineHeight: 22 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  chip: { color: colors.muted, fontSize: 11, fontWeight: '700', backgroundColor: colors.row, borderRadius: 10, overflow: 'hidden', paddingHorizontal: 8, paddingVertical: 4 },
  past: { borderTopWidth: 1, borderTopColor: '#1F3F31', paddingTop: 10, gap: 4 },
  pastTag: { color: '#8E8676', fontSize: 10, fontWeight: '800', letterSpacing: 1 },
  pastText: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  foot: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  footText: { color: '#8E8676', fontSize: 11 },
  more: { color: colors.gold, fontSize: 12, fontWeight: '700' },
});
