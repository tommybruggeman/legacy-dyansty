import { router } from 'expo-router';
import { useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import { type ActivityFeed, type ActivityItem, type Draft, type Move, type MovePlayer, type Trade, type TradeSide, type WaiverBid, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';
import { PlayerFace } from '../PlayerFace';
import { ErrorState, Loading } from '../ScreenState';
import { Card, Note, Pill, money, ui } from '../ui';

const FILTERS = ['All', 'Trades', 'Adds & drops', 'Draft', 'Mine'] as const;
const PAGE = 30;

export const shortOwner = (name: string | null | undefined) => {
  const [first, last] = String(name ?? '').split(' ');
  return last ? `${first} ${last[0]}.` : first;
};
const itemDate = (d: string) => (d ? new Date(`${d}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '');
// "Today 3:12 PM", "Yesterday 8:05 PM", else the date.
const itemWhen = (at: string, date: string) => {
  const t = at && at.length > 10 ? new Date(at) : null;
  if (!t || Number.isNaN(t.getTime())) return itemDate(date);
  const days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(t).setHours(0, 0, 0, 0)) / 86400000);
  const time = t.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  return days === 0 ? `Today ${time}` : days === 1 ? `Yesterday ${time}` : itemDate(date);
};
const deadCapText = (p: MovePlayer, season: number) =>
  p.dead_cap ? `$${Number(p.dead_cap.toFixed(2))} dead cap ${!p.dead_through || p.dead_through <= season ? 'this season' : `through ${p.dead_through}`}` : null;
const signedMoney = (n: number) => `${n > 0 ? '+' : ''}${money(n)}`;
const openTeam = (id: string | null | undefined) => (id ? () => router.push(`/team/${id}`) : undefined);

// One place for everything that happens in the league: trades, pickups, drops and the draft, newest first.
// The newest drop by that team that let the player go (or trade that sent him away), for a dead cap row.
function sourceOf(items: ActivityItem[], from: { player: string; team: string; trade: boolean }): string | undefined {
  const hit = items.find((i) =>
    from.trade
      ? i.kind === 'trade' && i.sides.some((s) => s.team_id !== from.team && s.players.some((p) => p.player_id === from.player))
      : i.kind === 'move' && i.team_id === from.team && i.drops.some((p) => p.player_id === from.player));
  return hit?.id;
}

export function ActivityView({ focus: focusId, from }: { focus?: string; from?: { player: string; team: string; trade: boolean } }) {
  const { data, error, loading, reload } = useApi<ActivityFeed>('/league/activity');
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('All');
  const [shown, setShown] = useState(PAGE);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const me = data.me;
  const focus = focusId ?? (from ? sourceOf(data.items, from) : undefined);
  // A trade alert or a dead cap row opens the page on that item: it goes first, outlined.
  const items = focus ? [...data.items.filter((i) => i.id === focus), ...data.items.filter((i) => i.id !== focus)] : data.items;
  const rows = items.filter((i) =>
    filter === 'All' ? true
      : filter === 'Trades' ? i.kind === 'trade'
        : filter === 'Adds & drops' ? i.kind === 'move'
          : filter === 'Draft' ? i.kind === 'draft'
            : !!me && i.owners.includes(me));

  return (
    <>
      <View style={styles.chips}>
        {FILTERS.filter((f) => f !== 'Mine' || me).map((f) => (
          <Pressable key={f} onPress={() => { setFilter(f); setShown(PAGE); }} style={[styles.chip, f === filter && styles.chipOn]}>
            <Text style={[styles.chipText, f === filter && styles.chipTextOn]}>{f}</Text>
          </Pressable>
        ))}
      </View>
      {rows.length === 0 ? <Note>Nothing here yet.</Note> : null}
      {rows.slice(0, shown).map((i) => <ActivityCard key={`${i.kind}-${i.id}`} item={i} me={me} focused={i.id === focus} />)}
      {rows.length > shown ? (
        <Pressable onPress={() => setShown((n) => n + PAGE)} style={styles.more}>
          <Text style={ui.link}>Show {Math.min(PAGE, rows.length - shown)} more ›</Text>
        </Pressable>
      ) : null}
      {filter === 'All' || filter === 'Trades' ? (
        <Note>Trades: winning = more points scored since the trade by what each side got. Value = today's dynasty market value.</Note>
      ) : null}
    </>
  );
}

export function ActivityCard({ item, me, focused }: { item: ActivityItem; me: string | null; focused?: boolean }) {
  if (item.kind === 'trade') return <TradeCard trade={item} me={me} focused={focused} />;
  if (item.kind === 'draft') return <DraftCard draft={item} me={me} />;
  return <MoveCard move={item} me={me} focused={focused} />;
}

// ---- the newest few, under League > Teams ------------------------------------

const ICON: Record<string, [string, string]> = {
  trade: ['TR', colors.gold], waiver: ['WA', '#7FB6FF'], free_agent: ['FA', colors.live], drop: ['DR', '#E07C7C'], commissioner: ['CM', colors.muted], draft: ['DF', '#D9A6FF'],
};

export function LatestActivity() {
  const { data } = useApi<ActivityFeed>('/league/activity?limit=5');
  const items = data?.items ?? [];
  if (!items.length) return null;
  const me = data?.me ?? null;
  return (
    <Card title="League activity" right={<Pressable onPress={() => router.push('/activity')} hitSlop={8}><Text style={ui.link}>See all ›</Text></Pressable>}>
      {items.map((i, n) => {
        const [code, color] = ICON[i.kind === 'move' ? (i.adds.length ? i.type : 'drop') : i.kind];
        return (
          <Pressable key={`${i.kind}-${i.id}`} onPress={() => router.push({ pathname: '/activity', params: { focus: i.id } })} style={[styles.line, n > 0 && styles.lineNext]}>
            <View style={[styles.icon, { backgroundColor: `${color}2E` }]}><Text style={[styles.iconText, { color }]}>{code}</Text></View>
            <View style={ui.flex}>
              <Text style={styles.lineText} numberOfLines={2}>{activityLine(i, me)}</Text>
              <Text style={ui.sub}>{itemWhen(i.at, i.date)}</Text>
            </View>
          </Pressable>
        );
      })}
    </Card>
  );
}

const who = (name: string | null | undefined, me: string | null) => (name === me ? 'You' : shortOwner(name));
export function activityLine(i: ActivityItem, me: string | null): string {
  if (i.kind === 'trade') {
    const got = (s: TradeSide) => [...s.players.map((p) => p.name), ...s.picks.map((p) => p.label), ...(s.faab > 0 ? [`${money(s.faab)} cap`] : [])].join(', ') || 'nothing';
    return i.sides.map((s) => `${who(s.owner, me)} get${s.owner === me ? '' : 's'} ${got(s)}`).join(' · ');
  }
  if (i.kind === 'draft') return `${i.season} draft: ${i.picks.length} picks`;
  const name = who(i.owner, me);
  if (i.type === 'waiver' && i.adds.length) {
    const p = i.adds[0];
    const bids = p.bids?.length ? ` · ${p.bids.length} bid${p.bids.length === 1 ? '' : 's'}` : '';
    return `${name} claimed ${p.name}${p.faab != null ? ` · $${p.faab} FAAB` : ''}${bids}${i.drops.length ? `, dropped ${i.drops.map((d) => d.name).join(', ')}` : ''}`;
  }
  const adds = i.adds.map((p) => p.name).join(', ');
  const drops = i.drops.map((p) => p.name).join(', ');
  return `${name} ${[adds && `added ${adds}`, drops && `dropped ${drops}`].filter(Boolean).join(', ')}`;
}

// ---- trades ----------------------------------------------------------------

function TradeCard({ trade, me, focused }: { trade: Trade; me: string | null; focused?: boolean }) {
  const mine = !!me && trade.owners.includes(me);
  const verdict = trade.winning
    ? { label: `${trade.winning.owner === me ? 'YOU' : shortOwner(trade.winning.owner).toUpperCase()} +${Math.round(trade.winning.by)}`, tone: 'live' as const }
    : { label: 'TOO EARLY', tone: 'muted' as const };
  const shares = trade.sides.map((s) => s.market_share ?? 0);
  return (
    <View style={[styles.card, mine && styles.cardMine, focused && styles.cardFocus]}>
      <View style={styles.head}>
        <Text style={styles.kind}>TRADE</Text>
        <Text style={styles.when}>{`${itemWhen(trade.at, trade.date)}${trade.week ? ` · Wk ${trade.week}` : ''}`}</Text>
        <View style={ui.flex} />
        <Pill label={verdict.label} tone={verdict.tone} />
      </View>
      {trade.sides.map((side, i) => <TradeSideView key={side.owner} side={side} me={me} first={i === 0} played={!!trade.weeks_since} />)}
      {shares.some((x) => x > 0) ? (
        <View style={styles.valueRow}>
          <Text style={styles.valueLabel}>VALUE NOW</Text>
          <View style={styles.valueBar}>
            {trade.sides.map((s, i) => (
              <View key={s.owner} style={[styles.valuePart, { flex: Math.max(s.market_share ?? 0, 0.02), backgroundColor: i === 0 ? colors.gold : '#5B7A6C' }]} />
            ))}
          </View>
          <Text style={styles.valueLabel}>{trade.sides.map((s) => `${Math.round((s.market_share ?? 0) * 100)}%`).join(' / ')}</Text>
        </View>
      ) : null}
    </View>
  );
}

function TradeSideView({ side, me, first, played }: { side: TradeSide; me: string | null; first: boolean; played: boolean }) {
  const who = side.owner === me ? 'You' : side.owner;
  return (
    <View style={[styles.side, !first && styles.sideNext]}>
      <View style={styles.sideHead}>
        <Pressable disabled={!side.team_id} onPress={openTeam(side.team_id)}>
          <Text style={styles.owner}>{`${who} get${who === 'You' ? '' : 's'}`}</Text>
        </Pressable>
        <Text style={styles.meta}>
          {[played && side.points_since != null ? `${Math.round(side.points_since)} pts since` : null, side.cap_change ? `${signedMoney(side.cap_change)} cap` : null]
            .filter(Boolean).join(' · ')}
        </Text>
      </View>
      {side.players.map((p) => (
        <PlayerLine key={p.player_id} p={p} right={played && p.points_since != null ? String(Math.round(p.points_since)) : undefined} />
      ))}
      {side.picks.map((pk) => (
        <View key={pk.label + (pk.original_owner ?? '')} style={styles.row}>
          <View style={styles.dot}><Text style={styles.dotText}>PICK</Text></View>
          <Text style={ui.name}>{`${pk.label}${pk.original_owner && pk.original_owner !== side.owner ? ` (${shortOwner(pk.original_owner)}'s)` : ''}`}</Text>
        </View>
      ))}
      {side.faab > 0 ? <Text style={[ui.sub, styles.faab]}>{`+${money(side.faab)} cap space`}</Text> : null}
    </View>
  );
}

// ---- pickups and drops ------------------------------------------------------

const MOVE_LABEL: Record<Move['type'], string> = { waiver: 'WAIVER CLAIM', free_agent: 'FREE AGENT', commissioner: 'COMMISSIONER', drop: 'DROP' };
const KIND_COLOR: Record<Move['type'], string> = { waiver: '#7FB6FF', free_agent: colors.live, commissioner: colors.muted, drop: '#E07C7C' };

function MoveCard({ move, me, focused }: { move: Move; me: string | null; focused?: boolean }) {
  const mine = !!me && move.owner === me;
  const [bids, setBids] = useState<{ player: string; bids: WaiverBid[] } | null>(null);
  const label = move.adds.length && move.drops.length && move.type !== 'commissioner' ? (move.type === 'waiver' ? 'WAIVER CLAIM + DROP' : 'ADD + DROP') : MOVE_LABEL[move.type];
  return (
    <View style={[styles.card, mine && styles.cardMine, focused && styles.cardFocus]}>
      <View style={styles.head}>
        <Text style={[styles.kind, { color: KIND_COLOR[move.type] }]}>{label}</Text>
        <View style={ui.flex} />
        <Text style={styles.when}>{`${itemWhen(move.at, move.date)}${move.week ? ` · Wk ${move.week}` : ''}`}</Text>
      </View>
      <Pressable disabled={!move.team_id} onPress={openTeam(move.team_id)}>
        <Text style={styles.owner}>{mine ? 'You' : move.owner}</Text>
      </Pressable>
      {move.adds.map((p) => (
        <PlayerLine
          key={`a${p.player_id}`} p={p} tag={move.drops.length ? 'ADD' : undefined}
          right={p.faab != null ? `$${p.faab}` : undefined} rightLabel={p.faab != null ? 'FAAB' : undefined}
          bids={p.bids?.length ? () => setBids({ player: p.name, bids: p.bids ?? [] }) : undefined} bidCount={p.bids?.length}
        />
      ))}
      {move.drops.map((p) => <PlayerLine key={`d${p.player_id}`} p={p} tag={move.adds.length ? 'DROP' : undefined} extra={deadCapText(p, move.season)} />)}
      <Modal visible={!!bids} transparent animationType="fade" onRequestClose={() => setBids(null)}>
        <Pressable style={styles.shade} onPress={() => setBids(null)}>
          <Pressable style={styles.modal}>
            <Text style={styles.kind}>{`WAIVER BIDS${move.week ? ` · WK ${move.week}` : ''}`}</Text>
            <Text style={styles.modalName}>{bids?.player}</Text>
            {bids?.bids.map((b, i) => (
              <View key={`${b.owner}-${i}`} style={styles.bidRow}>
                <Text style={[ui.name, ui.flex, b.won && styles.mineText]}>{b.owner === me ? 'You' : b.owner}</Text>
                <Text style={[styles.bidTag, b.won && { color: colors.live }]}>{b.won ? 'WON' : 'outbid'}</Text>
                <Text style={styles.bidAmount}>{`$${b.amount}`}</Text>
              </View>
            ))}
            <Note>Ties go to waiver order.</Note>
            <Pressable onPress={() => setBids(null)} style={styles.close}><Text style={ui.link}>Close</Text></Pressable>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );
}

// ---- the draft --------------------------------------------------------------

function DraftCard({ draft, me }: { draft: Draft; me: string | null }) {
  const [open, setOpen] = useState(false);
  const picks = open ? draft.picks : draft.picks.slice(0, 5);
  return (
    <View style={styles.card}>
      <View style={styles.head}>
        <Text style={[styles.kind, { color: '#D9A6FF' }]}>{`${draft.season} DRAFT`}</Text>
        <Text style={styles.when}>{`${itemDate(draft.date)} · ${draft.picks.length} picks`}</Text>
      </View>
      {picks.map((p) => (
        <Pressable key={`${p.round}.${p.slot}.${p.player_id}`} onPress={() => router.push(`/player/${p.player_id}`)} style={styles.row}>
          <Text style={styles.slot}>{p.round && p.slot ? `${p.round}.${String(p.slot).padStart(2, '0')}` : ''}</Text>
          <PlayerFace name={p.name} team={p.nfl_team} pos={p.pos} size={32} />
          <View style={ui.flex}>
            <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
            <Text style={[ui.sub, p.owner === me && styles.mineText]} numberOfLines={1}>{p.owner === me ? 'You' : p.owner}</Text>
          </View>
        </Pressable>
      ))}
      {draft.picks.length > 5 ? (
        <Pressable onPress={() => setOpen((o) => !o)} style={styles.more}>
          <Text style={ui.link}>{open ? 'Show less' : `All ${draft.picks.length} picks ›`}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

function PlayerLine({ p, tag, right, rightLabel, extra, bids, bidCount }: {
  p: MovePlayer; tag?: 'ADD' | 'DROP'; right?: string; rightLabel?: string; extra?: string | null; bids?: () => void; bidCount?: number;
}) {
  return (
    <Pressable onPress={() => router.push(`/player/${p.player_id}`)} style={styles.row}>
      <PlayerFace name={p.name} team={p.nfl_team} pos={p.pos} size={36} />
      <View style={ui.flex}>
        <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
        <Text style={ui.sub} numberOfLines={1}>
          {[tag, p.pos, p.salary != null ? money(p.salary) : null, p.years ? `${p.years} yr${p.years === 1 ? '' : 's'}` : null, extra].filter(Boolean).join(' · ')}
        </Text>
      </View>
      {right ? (
        <View style={styles.right}>
          <Text style={styles.rightText}>{right}</Text>
          {rightLabel ? <Text style={styles.rightLabel}>{rightLabel}</Text> : null}
          {bids ? (
            <Pressable onPress={bids} hitSlop={8} style={styles.bidChip}>
              <Text style={styles.bidChipText}>{`${bidCount} bid${bidCount === 1 ? '' : 's'} ›`}</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginBottom: 12 },
  chip: { borderWidth: 1, borderColor: colors.line, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 6 },
  chipOn: { borderColor: colors.gold },
  chipText: { color: colors.muted, fontSize: 12, fontWeight: '700' },
  chipTextOn: { color: colors.gold },
  card: { borderRadius: 18, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.card, padding: 14, marginBottom: 12 },
  cardMine: { borderColor: colors.gold },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 },
  kind: { color: colors.gold, fontSize: 10, fontWeight: '900', letterSpacing: 1 },
  when: { color: colors.muted, fontSize: 12, fontWeight: '700' },
  side: { paddingTop: 8 },
  sideNext: { borderTopWidth: 1, borderTopColor: colors.row, marginTop: 8 },
  sideHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 },
  owner: { color: colors.gold, fontSize: 14, fontWeight: '800' },
  meta: { color: colors.muted, fontSize: 11 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 5 },
  right: { alignItems: 'flex-end' },
  rightText: { color: colors.text, fontSize: 15, fontWeight: '800' },
  rightLabel: { color: colors.muted, fontSize: 9, fontWeight: '800', letterSpacing: 0.5 },
  dot: { width: 36, height: 36, borderRadius: 18, borderWidth: 1, borderColor: colors.gold, alignItems: 'center', justifyContent: 'center' },
  dotText: { color: colors.gold, fontSize: 9, fontWeight: '800' },
  slot: { color: colors.muted, fontSize: 12, fontWeight: '800', width: 34 },
  mineText: { color: colors.gold },
  faab: { marginTop: 2 },
  valueRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 12 },
  valueLabel: { color: colors.muted, fontSize: 10, fontWeight: '800', letterSpacing: 0.5 },
  valueBar: { flex: 1, flexDirection: 'row', height: 6, borderRadius: 3, overflow: 'hidden', gap: 2 },
  valuePart: { height: 6 },
  more: { alignItems: 'center', paddingVertical: 8, marginBottom: 4 },
  cardFocus: { borderColor: colors.gold, borderWidth: 2 },
  line: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 9 },
  lineNext: { borderTopWidth: 1, borderTopColor: colors.row },
  lineText: { color: colors.text, fontSize: 13, fontWeight: '700' },
  icon: { width: 30, height: 30, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  iconText: { fontSize: 10, fontWeight: '900' },
  bidChip: { marginTop: 4, borderWidth: 1, borderColor: '#2C4A6E', borderRadius: 9, paddingHorizontal: 8, paddingVertical: 2 },
  bidChipText: { color: '#7FB6FF', fontSize: 11, fontWeight: '800' },
  shade: { flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'center', padding: 24 },
  modal: { backgroundColor: colors.card, borderRadius: 20, borderWidth: 1, borderColor: colors.gold, padding: 18 },
  modalName: { color: colors.text, fontSize: 18, fontWeight: '800', marginTop: 6, marginBottom: 8 },
  bidRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 10, borderTopWidth: 1, borderTopColor: colors.row },
  bidTag: { color: colors.muted, fontSize: 11, fontWeight: '700' },
  bidAmount: { color: colors.text, fontSize: 16, fontWeight: '800', minWidth: 44, textAlign: 'right' },
  close: { alignItems: 'center', borderWidth: 1, borderColor: colors.gold, borderRadius: 14, paddingVertical: 10, marginTop: 12 },
});
