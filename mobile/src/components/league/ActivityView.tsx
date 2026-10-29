import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { type ActivityFeed, type ActivityItem, type Draft, type Move, type MovePlayer, type Trade, type TradeSide, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';
import { PlayerFace } from '../PlayerFace';
import { ErrorState, Loading } from '../ScreenState';
import { Note, Pill, money, ui } from '../ui';

const FILTERS = ['All', 'Trades', 'Adds & drops', 'Draft', 'Mine'] as const;
const PAGE = 30;

export const shortOwner = (name: string | null | undefined) => {
  const [first, last] = String(name ?? '').split(' ');
  return last ? `${first} ${last[0]}.` : first;
};
const itemDate = (d: string) => (d ? new Date(`${d}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '');
const signedMoney = (n: number) => `${n > 0 ? '+' : ''}${money(n)}`;
const openTeam = (id: string | null | undefined) => (id ? () => router.push(`/team/${id}`) : undefined);

// One place for everything that happens in the league: trades, pickups, drops and the draft, newest first.
export function ActivityView() {
  const { data, error, loading, reload } = useApi<ActivityFeed>('/league/activity');
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('All');
  const [shown, setShown] = useState(PAGE);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const me = data.me;
  const rows = data.items.filter((i) =>
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
      {rows.slice(0, shown).map((i) => <ActivityCard key={`${i.kind}-${i.id}`} item={i} me={me} />)}
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

export function ActivityCard({ item, me }: { item: ActivityItem; me: string | null }) {
  if (item.kind === 'trade') return <TradeCard trade={item} me={me} />;
  if (item.kind === 'draft') return <DraftCard draft={item} me={me} />;
  return <MoveCard move={item} me={me} />;
}

// ---- trades ----------------------------------------------------------------

function TradeCard({ trade, me }: { trade: Trade; me: string | null }) {
  const mine = !!me && trade.owners.includes(me);
  const verdict = trade.winning
    ? { label: `${trade.winning.owner === me ? 'YOU' : shortOwner(trade.winning.owner).toUpperCase()} +${Math.round(trade.winning.by)}`, tone: 'live' as const }
    : { label: 'TOO EARLY', tone: 'muted' as const };
  const shares = trade.sides.map((s) => s.market_share ?? 0);
  return (
    <View style={[styles.card, mine && styles.cardMine]}>
      <View style={styles.head}>
        <Text style={styles.kind}>TRADE</Text>
        <Text style={styles.when}>{`${itemDate(trade.date)}${trade.week ? ` · Wk ${trade.week}` : ''}`}</Text>
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
      {side.faab > 0 ? <Text style={[ui.sub, styles.faab]}>{`+${money(side.faab)} FAAB`}</Text> : null}
    </View>
  );
}

// ---- pickups and drops ------------------------------------------------------

const MOVE_LABEL: Record<Move['type'], string> = { waiver: 'WAIVER CLAIM', free_agent: 'FREE AGENT', commissioner: 'COMMISSIONER', drop: 'DROP' };

function MoveCard({ move, me }: { move: Move; me: string | null }) {
  const mine = !!me && move.owner === me;
  return (
    <View style={[styles.card, mine && styles.cardMine]}>
      <View style={styles.head}>
        <Text style={styles.kind}>{MOVE_LABEL[move.type]}</Text>
        <Text style={styles.when}>{`${itemDate(move.date)}${move.week ? ` · Wk ${move.week}` : ''}`}</Text>
      </View>
      <Pressable disabled={!move.team_id} onPress={openTeam(move.team_id)}>
        <Text style={styles.owner}>{mine ? 'You' : move.owner}</Text>
      </Pressable>
      {move.adds.map((p) => (
        <PlayerLine key={`a${p.player_id}`} p={p} tag="ADD" right={p.faab != null ? `$${p.faab}` : undefined} rightLabel={p.faab != null ? 'FAAB' : undefined} />
      ))}
      {move.drops.map((p) => <PlayerLine key={`d${p.player_id}`} p={p} tag="DROP" />)}
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
        <Text style={styles.kind}>{`${draft.season} DRAFT`}</Text>
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

function PlayerLine({ p, tag, right, rightLabel }: { p: MovePlayer; tag?: 'ADD' | 'DROP'; right?: string; rightLabel?: string }) {
  return (
    <Pressable onPress={() => router.push(`/player/${p.player_id}`)} style={styles.row}>
      <PlayerFace name={p.name} team={p.nfl_team} pos={p.pos} size={36} />
      <View style={ui.flex}>
        <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
        <Text style={ui.sub} numberOfLines={1}>
          {[tag, p.pos, p.salary != null ? money(p.salary) : null, p.years ? `${p.years} yr${p.years === 1 ? '' : 's'}` : null].filter(Boolean).join(' · ')}
        </Text>
      </View>
      {right ? (
        <View style={styles.right}>
          <Text style={styles.rightText}>{right}</Text>
          {rightLabel ? <Text style={styles.rightLabel}>{rightLabel}</Text> : null}
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
});
