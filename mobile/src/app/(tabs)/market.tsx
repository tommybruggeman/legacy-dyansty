import { LinearGradient } from 'expo-linear-gradient';
import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';
import { Alert, Linking, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { PlayerFace } from '../../components/PlayerFace';
import { ErrorState, Loading } from '../../components/ScreenState';
import { Card, Note, Pill, Screen, Segmented, SwipeTabs, Title, money, ui } from '../../components/ui';
import { apiSend, type Expiring, type LeagueOwner, type LeaguePlayer, type LeaguePlayers, type RookieDeal, type Target, useApi } from '../../lib/api';
import { teamColors } from '../../lib/nflColors';
import { nameMatches } from '../../lib/players';
import { colors } from '../../lib/theme';
import { useTargets } from '../../lib/targets';

const VIEWS = ['Players', 'Expiring'] as const;
const POSITIONS = ['All', 'QB', 'RB', 'WR', 'TE', 'K'] as const;
const PAGE = 40;

// Players: every player in the league (free agents first). Browse only: bidding and the rookie draft happen on the website, trades in Sleeper (League > Activity shows them all).
// Players show as tiles ranked by season points, with ☆ to save a transfer target.
export default function Market() {
  const [view, setView] = useState<(typeof VIEWS)[number]>('Players');
  // "See all in Players" from the pull-down search lands here with the search typed in.
  const { q } = useLocalSearchParams<{ q?: string }>();
  useEffect(() => {
    if (q != null) setView('Players');
  }, [q]);
  return (
    <Screen>
      <Title>Market</Title>
      <Segmented options={VIEWS} value={view} onChange={setView} />
      <SwipeTabs options={VIEWS} value={view} onChange={setView} render={(v) => (v === 'Players' ? <PlayersList query={q} /> : <ExpiringList />)} />
    </Screen>
  );
}

function Chips<T extends string>({ options, value, onChange }: { options: readonly T[]; value: T; onChange: (v: T) => void }) {
  return (
    <View style={styles.chips}>
      {options.map((o) => (
        <Pressable key={o} onPress={() => onChange(o)} style={[styles.chip, o === value && styles.chipOn]}>
          <Text style={[styles.chipText, o === value && styles.chipTextOn]}>{o}</Text>
        </Pressable>
      ))}
    </View>
  );
}

const INJURY_SHORT: Record<string, string> = { questionable: 'Q', doubtful: 'D', out: 'OUT', ir: 'IR', pup: 'PUP', sus: 'SUS' };
const injuryLabel = (s: string) => INJURY_SHORT[s.toLowerCase()] ?? s.toUpperCase();
const shortOwner = (name: string) => {
  const [first, last] = name.split(' ');
  return last ? `${first} ${last[0]}.` : first;
};
const ppgText = (n: number | null | undefined) => (n != null ? n.toFixed(1) : '—');
const ptsText = (n: number | null | undefined) => (n != null ? `${Math.round(n)} pts` : '');

// One player as a card tinted in their NFL team's colors: circle, name, a short line, PPG and season points.
function PlayerTile({ id, name, pos, team, ppg, points, line, tag, mine }: {
  id: string; name: string; pos: string; team: string; ppg: number | null; points: number | null;
  line: string; tag?: { label: string; tone: 'out' | 'gold' } | null; mine?: boolean;
}) {
  const { isTarget, toggle } = useTargets();
  const saved = isTarget(id);
  const [fill] = teamColors(team);
  return (
    <Pressable onPress={() => router.push(`/player/${id}`)} style={[styles.tile, (saved || mine) && styles.tileGold]}>
      <LinearGradient colors={[`${fill}CC`, colors.card]} locations={[0, 0.6]} start={{ x: 0, y: 0 }} end={{ x: 0.6, y: 1 }} style={StyleSheet.absoluteFill} />
      <View style={styles.tileTop}>
        <PlayerFace name={name} team={team} pos={pos} size={48} />
        <View style={styles.tileTopRight}>
          {tag ? <Pill label={tag.label} tone={tag.tone} /> : null}
          {mine ? null : (
            <Pressable onPress={() => toggle(id)} hitSlop={10} style={[styles.star, saved && styles.starOn]}>
              <Text style={[styles.starText, saved && styles.starTextOn]}>{saved ? '★' : '☆'}</Text>
            </Pressable>
          )}
        </View>
      </View>
      <Text style={styles.tileName} numberOfLines={1}>{name}</Text>
      <Text style={styles.tileLine} numberOfLines={1}>{line}</Text>
      <View style={styles.tileBottom}>
        <Text style={styles.tilePpg}>{ppgText(ppg)}<Text style={styles.unit}> PPG</Text></Text>
        <Text style={styles.tilePts}>{ptsText(points)}</Text>
      </View>
    </Pressable>
  );
}

const STATUSES = ['Free agents', 'Rostered', 'All'] as const;
const SALARY = ['Any', 'Under $5', 'Under $10', 'Under $20', '$20+'] as const;
const CONTRACT = ['Any', 'Expiring', '2+ years', 'Rookie deal', 'Taxi', 'IR'] as const;
const SORTS = ['Points', 'PPG', 'Value per $', 'Youngest'] as const;
type Filters = {
  pos: (typeof POSITIONS)[number]; owner: string; salary: (typeof SALARY)[number];
  contract: (typeof CONTRACT)[number]; sort: (typeof SORTS)[number];
};
const NO_FILTERS: Filters = { pos: 'All', owner: 'all', salary: 'Any', contract: 'Any', sort: 'Points' };

const salaryOk = (p: LeaguePlayer, s: Filters['salary']) => {
  if (s === 'Any') return true;
  if (p.salary == null) return false;
  return s === '$20+' ? p.salary >= 20 : p.salary < Number(s.replace(/\D/g, ''));
};
const contractOk = (p: LeaguePlayer, c: Filters['contract']) =>
  c === 'Any' ? true
    : c === 'Expiring' ? p.years_left != null && p.years_left <= 1
    : c === '2+ years' ? (p.years_left ?? 0) >= 2
    : c === 'Rookie deal' ? p.rookie_deal
    : p.designation === (c === 'Taxi' ? 'taxi' : 'ir');
const perDollar = (p: LeaguePlayer) => (p.rank_ppg != null && p.salary ? p.rank_ppg / p.salary : -1);
const SORTERS: Record<Filters['sort'], (a: LeaguePlayer, b: LeaguePlayer) => number> = {
  Points: () => 0, // the server already ranks by points
  PPG: (a, b) => (b.rank_ppg ?? -1) - (a.rank_ppg ?? -1),
  'Value per $': (a, b) => perDollar(b) - perDollar(a),
  Youngest: (a, b) => (a.age ?? 99) - (b.age ?? 99),
};

// Every player in the league: free agents by default, rostered players a tap away, and a name search
// that always looks through everyone. Everything past the status row lives in the Filters sheet.
function PlayersList({ query }: { query?: string }) {
  const { data, error, loading, reload } = useApi<LeaguePlayers>('/market/players');
  const [search, setSearch] = useState(query ?? '');
  const [status, setStatus] = useState<(typeof STATUSES)[number]>('Free agents');
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [sheet, setSheet] = useState(false);
  const [shown, setShown] = useState(PAGE);
  useEffect(() => {
    if (query != null) setSearch(query);
  }, [query]);

  const needle = search.trim();
  const rows = useMemo(() => {
    const out = (data?.players ?? []).filter((p) =>
      (needle ? nameMatches(p.name, needle) : status === 'All' || (status === 'Rostered') === !!p.team_id)
      && (filters.pos === 'All' || p.pos === filters.pos)
      && (filters.owner === 'all' || p.team_id === filters.owner)
      && salaryOk(p, filters.salary)
      && contractOk(p, filters.contract));
    return filters.sort === 'Points' ? out : [...out].sort(SORTERS[filters.sort]);
  }, [data, needle, status, filters]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const rankSeason = data.season_started ? data.season : data.last_season;
  const active = (Object.keys(NO_FILTERS) as (keyof Filters)[]).filter((k) => filters[k] !== NO_FILTERS[k]);
  const owner = data.owners.find((o) => o.team_id === filters.owner);
  const summary = [
    filters.pos !== 'All' ? filters.pos : null,
    owner ? (owner.is_mine ? 'YOURS' : owner.owner_name.toUpperCase()) : null,
    filters.salary !== 'Any' ? filters.salary.toUpperCase() : null,
    filters.contract !== 'Any' ? filters.contract.toUpperCase() : null,
    `${rows.length} PLAYERS`,
  ].filter(Boolean).join(' · ');
  const reset = () => { setFilters(NO_FILTERS); setShown(PAGE); };

  return (
    <>
      <TextInput
        value={search}
        onChangeText={(t) => { setSearch(t); setShown(PAGE); }}
        placeholder="Search every player"
        placeholderTextColor={colors.muted}
        style={styles.search}
        autoCorrect={false}
        clearButtonMode="while-editing"
      />
      <View style={[styles.chips, needle ? styles.dim : null]}>
        {STATUSES.map((o) => (
          <Pressable key={o} onPress={() => { setStatus(o); setShown(PAGE); }} style={[styles.chip, o === status && styles.chipOn]}>
            <Text style={[styles.chipText, o === status && styles.chipTextOn]}>{o}</Text>
          </Pressable>
        ))}
        <Pressable onPress={() => setSheet(true)} style={[styles.chip, styles.filterChip, active.length > 0 && styles.chipOn]}>
          <Text style={[styles.chipText, active.length > 0 && styles.chipTextOn]}>{active.length ? `Filters · ${active.length}` : 'Filters'}</Text>
        </Pressable>
      </View>
      <View style={styles.sectionHead}>
        <Text style={styles.sectionRight} numberOfLines={1}>{needle ? `WHOLE LEAGUE · ${summary}` : summary}</Text>
        <Text style={styles.sectionRight}>{filters.sort === 'Points' ? `MOST ${rankSeason} POINTS` : filters.sort.toUpperCase()}</Text>
      </View>
      {rows.length === 0 ? <Note>No players match.</Note> : null}
      <View style={styles.grid}>
        {rows.slice(0, shown).map((p) => (
          <PlayerTile
            key={p.player_id}
            id={p.player_id}
            name={p.name}
            pos={p.pos}
            team={p.nfl_team}
            ppg={p.rank_ppg}
            points={p.points}
            mine={p.is_mine}
            line={p.team_id
              ? `${p.is_mine ? 'You' : shortOwner(p.owner_name ?? '')}${p.salary != null ? ` · ${money(p.salary)}` : ''}${p.years_left ? ` · ${p.years_left}y` : ''}`
              : `${p.nfl_team} · Free agent`}
            tag={p.injury ? { label: injuryLabel(p.injury), tone: 'out' }
              : p.designation ? { label: p.designation.toUpperCase(), tone: 'gold' }
              : p.rookie ? { label: 'ROOKIE', tone: 'gold' } : null}
          />
        ))}
      </View>
      {rows.length > shown ? (
        <Pressable onPress={() => setShown((n) => n + PAGE)} style={styles.more}>
          <Text style={ui.link}>Show {Math.min(PAGE, rows.length - shown)} more ›</Text>
        </Pressable>
      ) : null}
      <Note>Bids happen in the free agent auction on the website.</Note>
      <FilterSheet
        open={sheet}
        onClose={() => setSheet(false)}
        filters={filters}
        owners={data.owners}
        count={rows.length}
        onChange={(f) => { setFilters(f); setShown(PAGE); }}
        onReset={reset}
      />
    </>
  );
}

function SheetChips<T extends string>({ label, options, value, onChange, names }: {
  label: string; options: readonly T[]; value: T; onChange: (v: T) => void; names?: Record<string, string>;
}) {
  return (
    <>
      <Text style={styles.sheetLabel}>{label}</Text>
      <View style={styles.chips}>
        {options.map((o) => (
          <Pressable key={o} onPress={() => onChange(o)} style={[styles.chip, o === value && styles.chipOn]}>
            <Text style={[styles.chipText, o === value && styles.chipTextOn]}>{names?.[o] ?? o}</Text>
          </Pressable>
        ))}
      </View>
    </>
  );
}

function FilterSheet({ open, onClose, filters, owners, count, onChange, onReset }: {
  open: boolean; onClose: () => void; filters: Filters; owners: LeagueOwner[]; count: number;
  onChange: (f: Filters) => void; onReset: () => void;
}) {
  const insets = useSafeAreaInsets();
  const set = <K extends keyof Filters>(k: K) => (v: Filters[K]) => onChange({ ...filters, [k]: v });
  const ownerIds = ['all', ...owners.map((o) => o.team_id)];
  const ownerNames = Object.fromEntries([['all', 'Everyone'], ...owners.map((o) => [o.team_id, o.is_mine ? 'You' : shortOwner(o.owner_name)])]);
  return (
    <Modal visible={open} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.scrim} onPress={onClose} />
      <View style={[styles.sheet, { paddingBottom: insets.bottom + 12 }]}>
        <View style={styles.grab} />
        <View style={styles.sheetHead}>
          <Text style={styles.sectionTitle}>Filters</Text>
          <Pressable onPress={onReset} hitSlop={10}><Text style={ui.link}>Reset</Text></Pressable>
        </View>
        <ScrollView style={styles.sheetBody}>
          <SheetChips label="POSITION" options={POSITIONS} value={filters.pos} onChange={set('pos')} />
          <SheetChips label="OWNER" options={ownerIds} value={filters.owner} onChange={set('owner')} names={ownerNames} />
          <SheetChips label="SALARY" options={SALARY} value={filters.salary} onChange={set('salary')} />
          <SheetChips label="CONTRACT" options={CONTRACT} value={filters.contract} onChange={set('contract')} />
          <SheetChips label="SORT" options={SORTS} value={filters.sort} onChange={set('sort')} />
        </ScrollView>
        <Pressable onPress={onClose} style={styles.plan}>
          <Text style={styles.planText}>Show {count} players</Text>
        </Pressable>
      </View>
    </Modal>
  );
}

function ExpiringList() {
  const [year, setYear] = useState<string | null>(null);
  const path = year ? `/market/expiring?year=${year}` : '/market/expiring';
  const { data, error, loading, reload, refresh } = useApi<Expiring>(path);
  useTargets(refresh); // saving a star anywhere on this page refreshes the targets card
  const [pos, setPos] = useState<string>('All');
  const options = ['All', 'QB', 'RB', 'WR', 'TE', 'Mine'] as const;

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const rows = data.players.filter((p) => pos === 'All' || (pos === 'Mine' ? p.is_mine : p.pos === pos));
  // Between the final game and the rollover, my calls come first until every one is made.
  const callsOpen = !!data.calls && data.calls.players.some((p) => !p.choice);

  return (
    <>
      {callsOpen ? <Calls calls={data.calls!} sleeperId={data.sleeper_league_id} onChange={refresh} /> : <Targets data={data} />}

      <View style={styles.sectionHead}>
        <Text style={styles.sectionTitle}>Contracts ending</Text>
        <Text style={styles.sectionRight}>TAP ☆ TO SAVE</Text>
      </View>
      <Segmented options={data.years.map(String)} value={String(data.season)} onChange={setYear} />
      <Chips options={options} value={pos as (typeof options)[number]} onChange={setPos} />
      {rows.length === 0 ? <Note>Nothing expiring here.</Note> : null}
      <View style={styles.grid}>
        {rows.map((p) => (
          <PlayerTile
            key={p.player_id}
            id={p.player_id}
            name={p.name}
            pos={p.pos}
            team={p.nfl_team}
            ppg={p.rank_ppg}
            points={p.points}
            mine={p.is_mine}
            line={`${p.is_mine ? 'You' : shortOwner(p.owner_name)} · ${money(p.salary)}`}
          />
        ))}
      </View>
      <Note>These hit free agency at the {data.season} rollover unless their owner extends them.</Note>
    </>
  );
}

const CHOICE_LABEL: Record<string, string> = { keep: '✓ KEEPING', walk: '✓ LETTING WALK', review: 'IN REVIEW' };

// My keep-or-walk calls on rookie deals, due at the June 30 rollover. Trades happen in Sleeper.
function Calls({ calls, sleeperId, onChange }: { calls: NonNullable<Expiring['calls']>; sleeperId: string; onChange: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const decided = calls.players.filter((p) => p.choice).length;
  const due = new Date(`${calls.deadline.slice(0, 10)}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

  const choose = (p: RookieDeal, choice: 'keep' | 'walk') => {
    if (!p.decision_id) return;
    const go = async () => {
      setBusy(p.player_id);
      try {
        await apiSend('POST', `/home/decisions/${p.decision_id}`, { choice });
        onChange();
      } catch (e: any) {
        Alert.alert('Not saved', e?.message ?? 'Try again.');
      } finally {
        setBusy(null);
      }
    };
    Alert.alert(
      choice === 'keep' ? `Keep ${p.name}?` : `Let ${p.name} walk?`,
      choice === 'keep' ? 'He stays on your team on a new deal.' : 'He goes to free agency at the rollover.',
      [{ text: 'Cancel', style: 'cancel' }, { text: choice === 'keep' ? 'Keep' : 'Let walk', onPress: go }],
    );
  };

  const ask = `Help me decide keep or walk on ${calls.players.filter((p) => !p.choice).map((p) => p.name).join(', ')} before the rollover.`;

  return (
    <Card gold title="Your calls" right={`DUE ${due.toUpperCase()}`}>
      <View style={styles.progress}>
        {calls.players.map((p) => <View key={p.player_id} style={[styles.progressBit, p.choice && styles.progressOn]} />)}
      </View>
      <Text style={ui.sub}>{decided} of {calls.players.length} decided</Text>
      {!calls.window_open ? <Text style={[ui.sub, styles.gap]}>Keep and Let walk open when the commissioner starts the rollover. Anything you skip walks.</Text> : null}
      {calls.players.map((p) => (
        <View key={p.player_id} style={[styles.call, p.choice && styles.callDone]}>
          <View style={styles.callRow}>
            <PlayerFace name={p.name} team={p.nfl_team} pos={p.pos} size={44} />
            <View style={ui.flex}>
              <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
              <Text style={ui.sub}>{money(p.salary)}{p.ppg != null ? ` · ${p.ppg.toFixed(1)} PPG` : ''}</Text>
            </View>
            {p.choice ? <Pill label={CHOICE_LABEL[p.choice] ?? p.choice.toUpperCase()} tone="live" /> : null}
          </View>
          {p.choice ? null : (
            <View style={styles.choices}>
              {(['keep', 'walk'] as const).map((c) => (
                <Pressable key={c} disabled={!p.open || busy === p.player_id} onPress={() => choose(p, c)} style={[styles.choice, !p.open && styles.choiceOff]}>
                  <Text style={styles.choiceText}>{c === 'keep' ? 'Keep' : 'Let walk'}</Text>
                </Pressable>
              ))}
              <Pressable onPress={() => Linking.openURL(`https://sleeper.com/leagues/${sleeperId}`)} style={[styles.choice, styles.choiceGold]}>
                <Text style={[styles.choiceText, styles.choiceGoldText]}>Trade ↗</Text>
              </Pressable>
            </View>
          )}
        </View>
      ))}
      <Pressable onPress={() => router.navigate({ pathname: '/front-office', params: { q: ask, at: String(Date.now()) } })} style={styles.askLink}>
        <Text style={ui.link}>Ask GM to help me decide ›</Text>
      </Pressable>
    </Card>
  );
}

// Players I saved with ☆, and the jump into the Front Office to plan around them.
function Targets({ data }: { data: Expiring }) {
  const { toggle } = useTargets();
  const list = data.targets;
  return (
    <Card gold title="Transfer targets" right={list.length ? `${list.length} SAVED` : undefined}>
      {list.length === 0 ? (
        <Text style={[ui.sub, styles.gap]}>Tap ☆ on any player, here, on Free Agents or on a player page, to save them as a target.</Text>
      ) : null}
      {list.map((t: Target) => (
        <Pressable key={t.player_id} onPress={() => router.push(`/player/${t.player_id}`)} style={styles.targetRow}>
          <PlayerFace name={t.name} team={t.nfl_team} pos={t.pos} size={46} />
          <View style={ui.flex}>
            <Text style={ui.name} numberOfLines={1}>{t.name}</Text>
            <Text style={ui.sub} numberOfLines={1}>
              {t.owner_name ? `${t.is_mine ? 'You' : t.owner_name} · ${money(t.salary)}${t.years_left === 1 ? ' · expiring' : ''}` : `${t.nfl_team} · free agent`}
            </Text>
          </View>
          <View style={styles.targetRight}>
            <Text style={styles.targetPpg}>{ppgText(t.rank_ppg)}<Text style={styles.unit}> PPG</Text></Text>
            <Pressable onPress={() => toggle(t.player_id)} hitSlop={10}>
              <Text style={styles.targetStar}>★</Text>
            </Pressable>
          </View>
        </Pressable>
      ))}
      {list.length ? (
        <Pressable
          onPress={() => router.navigate({ pathname: '/front-office', params: { q: data.plan_question, at: String(Date.now()) } })}
          style={styles.plan}
        >
          <Text style={styles.planText}>Plan my offseason with GM ›</Text>
        </Pressable>
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  search: {
    backgroundColor: colors.card, borderWidth: 1, borderColor: colors.line, borderRadius: 22,
    paddingHorizontal: 16, paddingVertical: 11, color: colors.text, fontSize: 15, marginBottom: 10,
  },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginBottom: 12 },
  chip: { borderWidth: 1, borderColor: colors.line, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 6 },
  chipOn: { borderColor: colors.gold },
  chipText: { color: colors.muted, fontSize: 12, fontWeight: '700' },
  chipTextOn: { color: colors.gold },
  filterChip: { marginLeft: 'auto', backgroundColor: colors.card },
  dim: { opacity: 0.45 },
  scrim: { flex: 1, backgroundColor: 'rgba(0,0,0,0.55)' },
  sheet: { backgroundColor: '#0C2419', borderTopLeftRadius: 22, borderTopRightRadius: 22, paddingHorizontal: 18, paddingTop: 10, maxHeight: '80%' },
  grab: { alignSelf: 'center', width: 40, height: 5, borderRadius: 3, backgroundColor: colors.line, marginBottom: 10 },
  sheetHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  sheetBody: { flexGrow: 0 },
  sheetLabel: { color: colors.muted, fontSize: 11, fontWeight: '800', letterSpacing: 1, marginTop: 10, marginBottom: 8 },
  sectionHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 4, marginBottom: 10, paddingHorizontal: 2 },
  sectionTitle: { color: colors.text, fontSize: 18, fontWeight: '800' },
  sectionRight: { color: colors.muted, fontSize: 10, fontWeight: '800', letterSpacing: 1 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', rowGap: 10, marginBottom: 12 },
  tile: { width: '48.5%', borderRadius: 18, borderWidth: 1, borderColor: colors.line, padding: 12, overflow: 'hidden', backgroundColor: colors.card },
  tileGold: { borderColor: colors.gold },
  tileTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' },
  tileTopRight: { alignItems: 'flex-end', gap: 6 },
  star: { width: 28, height: 28, borderRadius: 14, backgroundColor: 'rgba(0,0,0,0.35)', alignItems: 'center', justifyContent: 'center' },
  starOn: { backgroundColor: colors.gold },
  starText: { color: colors.text, fontSize: 15 },
  starTextOn: { color: colors.bg },
  tileName: { color: colors.text, fontSize: 15, fontWeight: '800', marginTop: 12 },
  tileLine: { color: colors.muted, fontSize: 12, marginTop: 2 },
  tileBottom: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-end', marginTop: 8 },
  tilePpg: { color: colors.gold, fontSize: 24, fontWeight: '800' },
  unit: { color: colors.muted, fontSize: 10, fontWeight: '600' },
  tilePts: { color: colors.muted, fontSize: 11, marginBottom: 4 },
  more: { alignItems: 'center', paddingVertical: 8, marginBottom: 4 },
  gap: { marginTop: 6 },
  progress: { flexDirection: 'row', gap: 4, marginTop: 4, marginBottom: 6 },
  progressBit: { flex: 1, height: 6, borderRadius: 3, backgroundColor: '#24392E' },
  progressOn: { backgroundColor: colors.gold },
  call: { borderTopWidth: 1, borderTopColor: colors.row, paddingTop: 12, marginTop: 12 },
  callDone: { opacity: 0.6 },
  callRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  choices: { flexDirection: 'row', gap: 8, marginTop: 12 },
  choice: { flex: 1, borderWidth: 1, borderColor: colors.line, borderRadius: 11, paddingVertical: 9, alignItems: 'center' },
  choiceOff: { opacity: 0.4 },
  choiceText: { color: colors.text, fontWeight: '800', fontSize: 13 },
  choiceGold: { backgroundColor: colors.gold, borderColor: colors.gold },
  choiceGoldText: { color: colors.bg },
  askLink: { alignItems: 'center', marginTop: 14 },
  targetRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 9 },
  targetRight: { alignItems: 'flex-end', gap: 2 },
  targetPpg: { color: colors.text, fontSize: 19, fontWeight: '800' },
  targetStar: { color: colors.gold, fontSize: 16 },
  plan: { backgroundColor: colors.gold, borderRadius: 12, paddingVertical: 12, alignItems: 'center', marginTop: 10 },
  planText: { color: colors.bg, fontWeight: '800', fontSize: 15 },
});
