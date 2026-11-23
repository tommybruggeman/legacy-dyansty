import { type ReactNode, useState } from 'react';
import { ActivityIndicator, Alert, Modal, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Pressable } from '../Pressable';
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

export function LineupSheet({ lineup, players, picking, onClose, onSaved, game, points }: {
  lineup: Lineup;
  players: Player[]; // starters and bench (everyone who can start)
  picking: Picking;
  // This week's opponent / kickoff and projected, live or final points, the same as the My Team rows.
  game?: (p: Player) => ReactNode;
  points?: (p: Player) => ReactNode;
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
      if (lineup.commissioner_team_id) {
        await apiSend('POST', '/commissioner/lineup', { team_id: lineup.commissioner_team_id, week: lineup.week, slots: next });
      } else {
        await apiSend('POST', '/my-team/lineup', { week: lineup.week, slots: next });
      }
      onClose();
      onSaved();
    } catch (e) {
      Alert.alert('Lineup not saved', e instanceof Error ? e.message : 'Try again.');
    } finally {
      setSaving(false);
    }
  }

  let title = '';
  type Row = { key: string; player: Player | null; note: string; disabled: boolean; current: boolean; onPick: () => void;
    chip: string; section: 'current' | 'starter' | 'bench' };
  let rows: Row[] = [];
  let pinned: Row | null = null;
  if (picking?.kind === 'slot') {
    const s = lineup.slots[picking.index];
    const current = s.player_id;
    title = 'Swap Player';
    const options = players.filter((p) => p.player_id && fits(s.slot, p.player_id));
    // Current starter first, then bench players by PPG, then starters in other slots (a swap).
    options.sort((a, b) => Number(b.player_id === current) - Number(a.player_id === current)
      || Number(ids.includes(a.player_id)) - Number(ids.includes(b.player_id)) || (b.ppg ?? 0) - (a.ppg ?? 0));
    rows = options.map((p) => {
      const at = ids.indexOf(p.player_id);
      const isLocked = locked.has(p.player_id) || (at >= 0 && at !== picking.index && !!ids[picking.index] && locked.has(ids[picking.index]!));
      return {
        chip: p.player_id === current ? s.label : at >= 0 ? lineup.slots[at].label : 'BN',
        section: (p.player_id === current ? 'current' : at >= 0 ? 'starter' : 'bench') as Row['section'],
        key: p.player_id, player: p, current: p.player_id === current, disabled: isLocked || p.player_id === current || !!(current && locked.has(current)),
        note: p.player_id === current ? 'Starting' : locked.has(p.player_id) ? 'Locked' : at >= 0 ? `Starting at ${lineup.slots[at].label}` : 'Bench',
        onPick: () => save(place(lineup, ids, posOf, picking.index, p.player_id)),
      };
    });
    if (current && !locked.has(current)) {
      // Always possible: send him to the bench and leave the slot open.
      rows.push({ key: 'empty', player: null, note: `Move ${byId.get(current)?.name ?? 'him'} to the bench`, disabled: false, current: false,
        chip: 'BN', section: 'bench', onPick: () => save(place(lineup, ids, posOf, picking.index, null)) });
    }
    if (!current) {
      pinned = { key: 'open', player: null, note: 'Empty slot', disabled: true, current: true, chip: s.label, section: 'current', onPick: () => undefined };
    }
  } else if (picking?.kind === 'bench') {
    const p = picking.player;
    title = 'Swap Player';
    pinned = { key: 'me', player: p, note: 'Bench', disabled: true, current: true, chip: 'BN', section: 'current', onPick: () => undefined };
    rows = lineup.slots.filter((s) => fits(s.slot, p.player_id)).map((s) => {
      const there = s.player_id ? byId.get(s.player_id) ?? null : null;
      const isLocked = locked.has(p.player_id) || (!!s.player_id && locked.has(s.player_id));
      return {
        key: String(s.index), player: there, current: false, disabled: isLocked, chip: s.label, section: 'starter' as const,
        note: isLocked ? 'Locked' : there ? 'Goes to the bench' : 'Empty slot',
        onPick: () => save(place(lineup, ids, posOf, s.index, p.player_id)),
      };
    });
  }

  const blocked = picking?.kind === 'slot' && !!lineup.slots[picking.index].player_id && locked.has(lineup.slots[picking.index].player_id!);
  pinned = pinned ?? rows.find((r) => r.section === 'current') ?? null;
  const listed = rows.filter((r) => r !== pinned);
  const starters = listed.filter((r) => r.section === 'starter');
  const benchRows = listed.filter((r) => r.section === 'bench');
  const divider = picking?.kind === 'slot' ? `⇅ Choose who plays ${lineup.slots[picking.index].label}` : picking?.kind === 'bench' ? `⇅ Choose where ${picking.player.name.split(' ').slice(-1)[0]} starts` : '';

  const line = (r: Row) => (
    <Pressable key={r.key} disabled={r.disabled || saving} onPress={r.onPick} style={[styles.row, r.disabled && !r.current && styles.dim]}>
      <SlotChip label={r.chip} bench={r.chip === 'BN'} locked={!!r.player && locked.has(r.player.player_id)} />
      {r.player ? <PlayerFace name={r.player.name} team={r.player.nfl_team} pos={r.player.pos} /> : <View style={styles.emptyFace}><Text style={styles.emptyPlus}>+</Text></View>}
      <View style={ui.flex}>
        <Text style={ui.name} numberOfLines={1}>{r.player ? r.player.name : 'Empty'}</Text>
        {r.player && game ? game(r.player) : null}
        {!r.player || !game || r.section === 'starter' || r.disabled ? <Text style={[ui.sub, r.current && styles.currentNote]}>{r.note}</Text> : null}
      </View>
      {r.player ? (points ? points(r.player) : <Text style={styles.ppg}>{r.player.ppg != null ? r.player.ppg.toFixed(1) : '—'}<Text style={styles.ppgUnit}> PPG</Text></Text>) : null}
    </Pressable>
  );

  // A half sheet over the roster (dimmed behind): who you're replacing on top, then the starters and bench
  // players who can play that spot. "Empty" on the bench is always there to just sit him.
  return (
    <Modal visible={!!picking} animationType="slide" transparent onRequestClose={onClose}>
      <Pressable style={styles.shade} onPress={onClose} />
      <View style={[styles.sheet, { paddingBottom: insets.bottom + 12 }]}>
        <View style={styles.grab} />
        <View style={styles.head}>
          <Text style={styles.title}>{title}</Text>
          {saving ? <ActivityIndicator color={colors.gold} style={styles.headRight} /> : <Pressable onPress={onClose} hitSlop={10} style={styles.headRight}><Text style={ui.link}>Done</Text></Pressable>}
        </View>
        {blocked ? <Text style={styles.lockedNote}>His game has started, so this slot is locked for the week.</Text> : null}
        <ScrollView>
          {pinned ? line(pinned) : null}
          <View style={styles.divider}><Text style={styles.dividerText}>{divider}</Text></View>
          {starters.map(line)}
          {benchRows.length ? <Text style={styles.section}>Bench</Text> : null}
          {benchRows.map(line)}
          {!listed.length ? <Text style={[ui.sub, styles.subtitle]}>Nobody else on your roster fits this spot.</Text> : null}
          <Text style={[ui.sub, styles.subtitle]}>{`Week ${lineup.week} · players lock when their game kicks off`}</Text>
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
  shade: { flex: 1, backgroundColor: 'rgba(0,0,0,0.55)' },
  sheet: { position: 'absolute', left: 0, right: 0, bottom: 0, maxHeight: '78%', backgroundColor: '#0C2419', paddingHorizontal: 16, paddingTop: 10,
    borderTopLeftRadius: 24, borderTopRightRadius: 24, borderWidth: 1, borderColor: colors.line },
  headRight: { position: 'absolute', right: 0 },
  divider: { borderTopWidth: 1, borderTopColor: colors.line, marginTop: 14, marginBottom: 10, alignItems: 'center' },
  dividerText: { marginTop: -11, backgroundColor: '#0C2419', borderWidth: 1, borderColor: colors.line, borderRadius: 12, overflow: 'hidden',
    paddingHorizontal: 12, paddingVertical: 3, color: colors.gold, fontSize: 12, fontWeight: '800' },
  section: { color: colors.text, fontSize: 16, fontWeight: '900', marginTop: 10, marginBottom: 2 },
  grab: { alignSelf: 'center', width: 40, height: 5, borderRadius: 3, backgroundColor: colors.line, marginBottom: 10 },
  head: { flexDirection: 'row', justifyContent: 'center', alignItems: 'center', marginBottom: 8 },
  title: { color: colors.text, fontSize: 20, fontWeight: '900' },
  subtitle: { marginTop: 4, marginBottom: 10 },
  lockedNote: { color: colors.gold, fontSize: 13, marginBottom: 8 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 9 },
  dim: { opacity: 0.45 },
  currentNote: { color: colors.gold, fontWeight: '700' },
  emptyFace: { width: 44, height: 44, borderRadius: 22, borderWidth: 1, borderStyle: 'dashed', borderColor: colors.muted, alignItems: 'center', justifyContent: 'center' },
  emptyPlus: { color: colors.muted, fontSize: 20, fontWeight: '700' },
  ppg: { color: colors.text, fontSize: 17, fontWeight: '800' },
  ppgUnit: { color: colors.muted, fontSize: 10, fontWeight: '600' },
});
