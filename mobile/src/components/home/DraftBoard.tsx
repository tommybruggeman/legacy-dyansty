import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { type DraftBoardData, type DraftPickOwner } from '../../lib/api';
import { colors } from '../../lib/theme';
import { Avatar } from '../Pictures';
import { Card, firstName } from '../ui';

// Tuesday/Wednesday in the playoffs: the next rookie draft, one round at a time (Tommy picked this layout,
// 2026-11-22). Green pick number = locked; dashed gold = still moving with the bracket, shown as the most likely
// owner and odds, the other possible teams in small print, "via X" on traded picks.
export function DraftBoard({ board }: { board: DraftBoardData }) {
  const [round, setRound] = useState(1);
  const rnd = board.rounds.find((r) => r.round === round) ?? board.rounds[0];
  if (!rnd) return null;
  return (
    <Card>
      <View style={styles.between}>
        <Text style={styles.eyebrow}>{`${board.year} ROOKIE DRAFT`}</Text>
        <Text style={styles.dim}>{`${board.locked} of ${board.rounds[0]?.picks.length ?? 0} locked`}</Text>
      </View>
      <View style={styles.seg}>
        {board.rounds.map((r) => (
          <Pressable key={r.round} onPress={() => setRound(r.round)} style={[styles.segBtn, r.round === round && styles.segOn]}>
            <Text style={[styles.segText, r.round === round && styles.segTextOn]}>{`Round ${r.round}`}</Text>
          </Pressable>
        ))}
      </View>
      {rnd.picks.map((p) => (
        <View key={p.pick} style={styles.row}>
          <Text style={[styles.no, p.locked ? styles.noLocked : styles.noMoving]}>{p.pick}</Text>
          <Avatar uri={p.owner.avatar} name={p.owner.name} size={24} me={p.owner.is_me} />
          <View style={styles.who}>
            <Text style={[styles.name, p.owner.is_me && styles.me]} numberOfLines={1}>
              {p.locked ? firstName(p.owner.name) : `Most likely ${firstName(p.owner.name)}`}
            </Text>
            {p.owner.via ? <Text style={styles.small} numberOfLines={1}>{`via ${firstName(p.owner.via)}`}</Text> : null}
            {!p.locked && p.others?.length ? <Text style={styles.small} numberOfLines={2}>{p.others.map(other).join(' · ')}</Text> : null}
          </View>
          {p.locked ? <Text style={styles.lock}>LOCKED</Text> : (
            <View style={styles.chance}>
              <Text style={styles.chanceText}>{`${p.owner.pct ?? 0}%`}</Text>
              <View style={styles.bar}><View style={[styles.fill, { width: `${p.owner.pct ?? 0}%` }]} /></View>
            </View>
          )}
        </View>
      ))}
    </Card>
  );
}

const other = (o: DraftPickOwner) => `${firstName(o.name)}${o.via ? ` via ${firstName(o.via)}` : ''} ${o.pct ?? 0}%`;

const styles = StyleSheet.create({
  between: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  eyebrow: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1.2 },
  dim: { color: colors.muted, fontSize: 12 },
  seg: { flexDirection: 'row', backgroundColor: colors.card, borderRadius: 10, padding: 3, marginTop: 10, marginBottom: 4 },
  segBtn: { flex: 1, borderRadius: 8, paddingVertical: 6, alignItems: 'center' },
  segOn: { backgroundColor: colors.gold },
  segText: { color: colors.muted, fontSize: 12, fontWeight: '700' },
  segTextOn: { color: colors.bg },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8, paddingHorizontal: 2, borderTopWidth: 1, borderTopColor: colors.card },
  no: { width: 42, fontWeight: '900', fontSize: 14, textAlign: 'center', borderRadius: 8, paddingVertical: 4, overflow: 'hidden' },
  noLocked: { backgroundColor: 'rgba(19,148,94,0.22)', color: '#BFEBD3', borderWidth: 1, borderColor: 'rgba(19,148,94,0.75)' },
  noMoving: { color: '#E2BC5B', borderWidth: 1, borderColor: colors.gold, borderStyle: 'dashed' },
  who: { flex: 1, minWidth: 0 },
  name: { color: colors.text, fontWeight: '700', fontSize: 13 },
  me: { color: '#E2BC5B' },
  small: { color: colors.muted, fontSize: 10.5 },
  lock: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 0.6 },
  chance: { width: 72, alignItems: 'flex-end', gap: 3 },
  chanceText: { color: '#E2BC5B', fontSize: 11.5, fontWeight: '800' },
  bar: { height: 4, borderRadius: 2, backgroundColor: '#2B3A2F', width: '100%', overflow: 'hidden' },
  fill: { height: '100%', backgroundColor: colors.gold },
});
