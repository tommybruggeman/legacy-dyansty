import { useState } from 'react';
import { ActivityIndicator, Alert, Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiSend, type Lineup, type Player } from '../../lib/api';
import { colors } from '../../lib/theme';
import { PlayerFace } from '../PlayerFace';
import { ui } from '../ui';

// What the sheet was opened for: one starting slot, or a bench player looking for a slot.
export type Picking = { kind: 'slot'; index: number } | { kind: 'bench'; player: Player } | null;

// The gold slot chip next to each player on My Team. Tapping it opens the sheet; a lock shows once his game starts.
// Without onPress it's a plain read-only tag (other teams, or lineups still set in Sleeper).
export function SlotChip({ label, locked, bench, onPress }: { label: string; locked?: boolean; bench?: boolean; onPress?: () => void }) {
  return (
    <Pressable onPress={onPress} disabled={!onPress} hitSlop={6} style={[styles.chip, bench && styles.chipBench, locked && styles.chipLocked]}>
      <Text style={[styles.chipText, bench && styles.chipTextBench, locked && styles.chipTextLocked]}>{locked ? '🔒' : label}</Text>
    </Pressable>
  );
}

// Swap player into slot `index`. If he was starting elsewhere, the player he replaces takes his old slot when he
// fits it (otherwise that slot goes empty and he heads to the bench).
export function place(lineup: Lineup, ids: (string | null)[], posOf: (id: string) => string, index: number, playerId: string | null) {
  const next = [...ids];
  const from = playerId ? next.indexOf(playerId) : -1;
  const out = next[index];
  next[index] = playerId;
  if (from >= 0 && from !== index) {
    const slot = lineup.slots[from].slot;
    next[from] = out && (lineup.fits[slot] ?? [slot]).includes(posOf(out)) ? out : null;
  }
  return next;
}

export function LineupSheet({ lineup, players, picking, onClose, onSaved }: {
  lineup: Lineup;
  players: Player[]; // starters and bench (everyone who can start)
  picking: Picking;
  onClose: () => void;
  onSaved: () => void;
}) {
  const insets = useSafeAreaInsets();
  const [saving, setSaving] = useState(false);
  const byId = new Map(players.filter((p) => p.player_id).map((p) => [p.player_id, p]));
  const posOf = (id: string) => byId.get(id)?.pos ?? '';
  const ids = lineup.slots.map((s) => s.player_id);
  const locked = new Set(lineup.locked);
  const fits = (slot: string, id: string) => (lineup.fits[slot] ?? [slot]).includes(posOf(id));

  async function save(next: (string | null)[]) {
    setSaving(true);
    try {
      await apiSend('POST', '/my-team/lineup', { week: lineup.week, slots: next });
      onClose();
      onSaved();
    } catch (e) {
      Alert.alert('Lineup not saved', e instanceof Error ? e.message : 'Try again.');
    } finally {
      setSaving(false);
    }
  }

  let title = '';
  let rows: { key: string; player: Player | null; note: string; disabled: boolean; current: boolean; onPick: () => void }[] = [];
  if (picking?.kind === 'slot') {
    const s = lineup.slots[picking.index];
    const current = s.player_id;
    title = `Who plays ${s.label}?`;
    const options = players.filter((p) => p.player_id && fits(s.slot, p.player_id));
    // Current starter first, then bench players by PPG, then starters in other slots (a swap).
    options.sort((a, b) => Number(b.player_id === current) - Number(a.player_id === current)
      || Number(ids.includes(a.player_id)) - Number(ids.includes(b.player_id)) || (b.ppg ?? 0) - (a.ppg ?? 0));
    rows = options.map((p) => {
      const at = ids.indexOf(p.player_id);
      const isLocked = locked.has(p.player_id) || (at >= 0 && at !== picking.index && !!ids[picking.index] && locked.has(ids[picking.index]!));
      return {
        key: p.player_id, player: p, current: p.player_id === current, disabled: isLocked || p.player_id === current || !!(current && locked.has(current)),
        note: p.player_id === current ? 'Starting' : locked.has(p.player_id) ? 'Locked' : at >= 0 ? `Starting at ${lineup.slots[at].label}` : 'Bench',
        onPick: () => save(place(lineup, ids, posOf, picking.index, p.player_id)),
      };
    });
    if (current && !locked.has(current)) {
      rows.push({ key: 'empty', player: null, note: 'Move him to the bench', disabled: false, current: false, onPick: () => save(place(lineup, ids, posOf, picking.index, null)) });
    }
  } else if (picking?.kind === 'bench') {
    const p = picking.player;
    title = `Start ${p.name} at…`;
    rows = lineup.slots.filter((s) => fits(s.slot, p.player_id)).map((s) => {
      const there = s.player_id ? byId.get(s.player_id) ?? null : null;
      const isLocked = locked.has(p.player_id) || (!!s.player_id && locked.has(s.player_id));
      return {
        key: String(s.index), player: there, current: false, disabled: isLocked,
        note: `${s.label} · ${isLocked ? 'Locked' : there ? 'goes to the bench' : 'empty slot'}`,
        onPick: () => save(place(lineup, ids, posOf, s.index, p.player_id)),
      };
    });
  }

  const blocked = picking?.kind === 'slot' && !!lineup.slots[picking.index].player_id && locked.has(lineup.slots[picking.index].player_id!);
  return (
    <Modal visible={!!picking} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <View style={[styles.sheet, { paddingBottom: insets.bottom + 12 }]}>
        <View style={styles.grab} />
        <View style={styles.head}>
          <Text style={styles.title} numberOfLines={1}>{title}</Text>
          {saving ? <ActivityIndicator color={colors.gold} /> : <Pressable onPress={onClose} hitSlop={10}><Text style={ui.link}>Done</Text></Pressable>}
        </View>
        <Text style={[ui.sub, styles.subtitle]}>Week {lineup.week} · players lock when their game kicks off</Text>
        {blocked ? <Text style={styles.lockedNote}>His game has started, so this slot is locked for the week.</Text> : null}
        <ScrollView>
          {rows.map((r, i) => (
            <Pressable key={r.key} disabled={r.disabled || saving} onPress={r.onPick} style={[styles.row, i === 0 && styles.first, r.disabled && !r.current && styles.dim]}>
              {r.player ? <PlayerFace name={r.player.name} team={r.player.nfl_team} pos={r.player.pos} /> : <View style={styles.emptyFace}><Text style={styles.emptyPlus}>{picking?.kind === 'bench' ? '+' : '–'}</Text></View>}
              <View style={ui.flex}>
                <Text style={ui.name} numberOfLines={1}>{r.player ? r.player.name : picking?.kind === 'bench' ? 'Open slot' : 'Leave it empty'}</Text>
                <Text style={[ui.sub, r.current && styles.currentNote]}>{r.player && picking?.kind === 'slot' ? `${r.player.nfl_team || 'FA'} · ` : ''}{r.note}</Text>
              </View>
              {r.player && picking?.kind === 'slot' ? (
                <Text style={styles.ppg}>{r.player.ppg != null ? r.player.ppg.toFixed(1) : '—'}<Text style={styles.ppgUnit}> PPG</Text></Text>
              ) : null}
            </Pressable>
          ))}
          {!rows.length ? <Text style={[ui.sub, styles.subtitle]}>Nobody on your roster fits this slot.</Text> : null}
        </ScrollView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  chip: { minWidth: 40, paddingHorizontal: 6, height: 28, borderRadius: 8, backgroundColor: colors.gold, alignItems: 'center', justifyContent: 'center', marginRight: 10 },
  chipBench: { backgroundColor: 'transparent', borderWidth: 1, borderColor: colors.line },
  chipLocked: { backgroundColor: '#2A3A30' },
  chipText: { color: colors.bg, fontSize: 12, fontWeight: '900' },
  chipTextBench: { color: colors.muted },
  chipTextLocked: { fontSize: 12 },
  sheet: { flex: 1, backgroundColor: '#0C2419', paddingHorizontal: 18, paddingTop: 10 },
  grab: { alignSelf: 'center', width: 40, height: 5, borderRadius: 3, backgroundColor: colors.line, marginBottom: 10 },
  head: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 12 },
  title: { color: colors.text, fontSize: 20, fontWeight: '800', flexShrink: 1 },
  subtitle: { marginTop: 4, marginBottom: 10 },
  lockedNote: { color: colors.gold, fontSize: 13, marginBottom: 8 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 11, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  first: { borderTopWidth: 0 },
  dim: { opacity: 0.45 },
  currentNote: { color: colors.gold, fontWeight: '700' },
  emptyFace: { width: 44, height: 44, borderRadius: 22, borderWidth: 1, borderStyle: 'dashed', borderColor: colors.muted, alignItems: 'center', justifyContent: 'center' },
  emptyPlus: { color: colors.muted, fontSize: 20, fontWeight: '700' },
  ppg: { color: colors.text, fontSize: 17, fontWeight: '800' },
  ppgUnit: { color: colors.muted, fontSize: 10, fontWeight: '600' },
});
