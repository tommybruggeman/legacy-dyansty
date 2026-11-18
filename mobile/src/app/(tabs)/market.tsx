import { router, useLocalSearchParams, useNavigation } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { PlayerFace } from '../../components/PlayerFace';
import { ErrorState, Loading } from '../../components/ScreenState';
import { Note, Pill, Screen, Title, money, ui } from '../../components/ui';
import { type LeagueOwner, type LeaguePlayer, type LeaguePlayers, type NextGame, useApi } from '../../lib/api';
import { nameMatches } from '../../lib/players';
import { colors } from '../../lib/theme';
import { useTargets } from '../../lib/targets';

const POSITIONS = ['QB', 'RB', 'WR', 'TE', 'K'] as const;
const PAGE = 40;

// One page for every player in the league: search, a status row (free agents, rostered, saved ★, all) and a
// Filters sheet. Expiring contracts are a filter here; keep-or-walk calls live on My Team.
// Browse only: bidding and the rookie draft happen on the website, trades in Sleeper.
export default function Market() {
  // "See all in Players" from the pull-down search lands here with the search typed in.
  const { q } = useLocalSearchParams<{ q?: string }>();
  return (
    <Screen>
      <Title>Market</Title>
      <PlayersList query={q} />
    </Screen>
  );
}

const INJURY_SHORT: Record<string, string> = { questionable: 'Q', doubtful: 'D', out: 'OUT', ir: 'IR', pup: 'PUP', sus: 'SUS' };
const injuryLabel = (s: string) => INJURY_SHORT[s.toLowerCase()] ?? s.toUpperCase();
const shortOwner = (name: string) => {
  const [first, last] = name.split(' ');
  return last ? `${first} ${last[0]}.` : first;
};

// One row: star, face, name with team and any tag, line 1 = contract (owner · salary · years for rostered players,
// age and position rank for free agents), line 2 = his game (kickoff, opponent, bye), his last 6 weeks as bars, then one big number on the right that is always the stat the list
// is sorted by. Every number comes from the same league-scoring lines as the sort, so the order always matches.
function PlayerRow({ p, line, sort }: { p: LeaguePlayer; line: string | null; sort: Sort }) {
  const { isTarget, toggle } = useTargets();
  const saved = isTarget(p.player_id);
  const tag = p.injury ? { label: injuryLabel(p.injury), tone: 'out' as const }
    : p.designation ? { label: p.designation.toUpperCase(), tone: 'gold' as const }
    : p.rookie ? { label: 'ROOKIE', tone: 'gold' as const } : null;
  const facts = line ?? [p.age != null ? `Age ${p.age}` : null, p.pos_rank ?? null].filter(Boolean).join(' · ');
  return (
    <Pressable onPress={() => router.push(`/player/${p.player_id}`)} style={styles.row}>
      <Pressable onPress={() => toggle(p.player_id)} hitSlop={10} style={styles.star}>
        <Text style={[styles.starText, saved && styles.starTextOn]}>{saved ? '★' : '☆'}</Text>
      </Pressable>
      <PlayerFace name={p.name} team={p.nfl_team} pos={p.pos} size={40} />
      <View style={styles.rowMid}>
        <View style={styles.rowNameLine}>
          <Text style={[styles.rowName, (saved || p.is_mine) && styles.rowNameGold]} numberOfLines={1}>{shortName(p.name)}</Text>
          <Text style={styles.rowTeam}>{p.nfl_team === 'FA' ? '' : p.nfl_team}</Text>
          {tag ? <Pill label={tag.label} tone={tag.tone} /> : null}
        </View>
        {facts ? <Text style={styles.rowLine} numberOfLines={1}>{facts}</Text> : null}
        {p.next ? <Text style={styles.rowLine} numberOfLines={1}><GameText next={p.next} /></Text> : null}
        <Bars bars={p.bars} />
      </View>
      <BigNumber p={p} sort={sort} />
    </Pressable>
  );
}

// The sorted stat, big, with a small line under it that says what it covers.
function BigNumber({ p, sort }: { p: LeaguePlayer; sort: Sort }) {
  const one = (n: number | null | undefined) => (n != null ? n.toFixed(1) : '—');
  const games = (n: number | null | undefined) => (n ? `${n} game${n === 1 ? '' : 's'}` : '');
  let text = '—';
  let sub = '';
  let color: string = colors.gold;
  if (sort === 'Points') { text = p.points != null ? p.points.toFixed(1) : '—'; sub = games(p.games); }
  else if (sort === 'PPG') { text = one(p.avg ?? p.rank_ppg); sub = games(p.games); }
  else if (sort === 'Last 3 games') {
    text = one(p.last3);
    if (p.trend != null && Math.abs(p.trend) >= 1) { sub = `${p.trend > 0 ? '▲' : '▼'}${Math.abs(p.trend).toFixed(1)} vs avg`; color = p.trend > 0 ? GREEN : RED; }
    else sub = p.last3 != null ? 'even w/ avg' : '';
  } else if (sort === 'This week') {
    const w = weekNumber(p);
    text = w.text; sub = w.sub; color = w.live ? GREEN : w.dim ? colors.muted : colors.gold;
  } else if (sort === 'Value per $') { const v = perDollar(p); text = v >= 0 ? v.toFixed(1) : '—'; sub = p.salary ? `PPG per ${money(p.salary)}` : 'no contract'; }
  else if (sort === 'Salary') { text = p.salary != null ? money(p.salary) : '—'; sub = p.years_left ? `${p.years_left}y left` : ''; }
  else if (sort === 'Youngest') { text = p.age != null ? String(p.age) : '—'; sub = 'years old'; }
  return (
    <View style={styles.big}>
      <Text style={[styles.bigText, { color }]} numberOfLines={1} adjustsFontSizeToFit>{text}</Text>
      {sub ? <Text style={styles.bigSub} numberOfLines={1}>{sub}</Text> : null}
    </View>
  );
}

// This week's number: projection before kickoff, live points (green) during, final points after.
function weekNumber(p: LeaguePlayer): { text: string; sub: string; live?: boolean; dim?: boolean } {
  const state = p.next?.state;
  if (p.next?.bye) return { text: 'BYE', sub: '', dim: true };
  if ((state === 'in' || state === 'post') && p.week_pts != null) {
    return { text: p.week_pts.toFixed(1), sub: state === 'in' ? 'live' : 'final', live: state === 'in' };
  }
  if (p.injury && OUT.has(p.injury.toLowerCase())) return { text: injuryLabel(p.injury), sub: '', dim: true };
  return { text: p.proj != null ? p.proj.toFixed(1) : '—', sub: p.proj != null ? 'projected' : '' };
}

// His last 6 weeks, one bar each on a shared 30-point scale so rows compare. A bye is a dashed line, a week he
// didn't play a short grey stub, this week's game gold (green while it's live), and a game still to come an outline.
const BAR_MAX = 30;
function Bars({ bars }: { bars?: LeaguePlayer['bars'] }) {
  if (!bars?.length) return null;
  const last = bars[bars.length - 1].week;
  return (
    <View style={styles.bars}>
      {bars.map((b) => {
        if (b.bye) return <View key={b.week} style={[styles.bar, styles.barBye]} />;
        if (b.pts == null) return <View key={b.week} style={[styles.bar, b.week === last ? styles.barNext : styles.barMissed]} />;
        const h = Math.max(2, Math.min(16, (b.pts / BAR_MAX) * 16));
        return <View key={b.week} style={[styles.bar, { height: h }, b.live ? styles.barLive : b.week === last ? styles.barNow : null]} />;
      })}
    </View>
  );
}

const OUT = new Set(['out', 'ir', 'doubtful', 'pup', 'sus', 'na']);
const GREEN = colors.live;
const RED = '#E66A6A';
// "Jacoby Brissett" -> "J. Brissett" (keeps suffixes: "M. Harrison Jr.")
const shortName = (name: string) => {
  const [first, ...rest] = name.split(' ');
  return rest.length ? `${first[0]}. ${rest.join(' ')}` : first;
};

// His game as pieces of the row line: kickoff with a tough/soft defense note, the live clock, or the final score.
function GameText({ next }: { next?: NextGame | null }) {
  if (!next) return null;
  if (next.bye) return <Text>Bye week</Text>;
  const where = `${next.home ? 'vs' : '@'} ${next.opp}`;
  if (next.state === 'post' || (next.final && next.state == null)) {
    const won = next.score != null && next.opp_score != null ? (next.score > next.opp_score ? 'W' : next.score < next.opp_score ? 'L' : 'T') : null;
    return <Text>{`Final${won ? ` · ${won} ${next.score}-${next.opp_score}` : ''} ${where}`}</Text>;
  }
  if (next.state === 'in') {
    return <Text><Text style={{ color: GREEN }}>● {next.detail || 'Live'}</Text>{` · ${where}`}</Text>;
  }
  const when = next.kickoff
    ? (() => { const d = new Date(next.kickoff); return `${d.toLocaleDateString(undefined, { weekday: 'short' })} ${d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`; })()
    : '';
  const teams = next.teams ?? 32;
  // rank 1 = fewest points allowed to his position (toughest): the top third is a tough defense, the bottom third a soft one
  const matchup = next.opp_rank == null ? null : next.opp_rank <= teams / 3 ? { text: 'tough D', color: RED } : next.opp_rank > (teams * 2) / 3 ? { text: 'soft D', color: GREEN } : null;
  return <Text>{when ? `${when} · ` : ''}{where}{matchup ? <Text style={{ color: matchup.color }}>{` · ${matchup.text}`}</Text> : null}</Text>;
}

const STATUSES = ['Free agents', 'Rostered', '★ Saved', 'All'] as const;
const SALARY = ['Under $5', 'Under $10', 'Under $20', '$20+'] as const;
const TYPES = ['Rookie deal', 'Taxi', 'IR'] as const;
const SORTS = ['Points', 'PPG', 'Last 3 games', 'This week', 'Value per $', 'Salary', 'Youngest'] as const;
type Sort = (typeof SORTS)[number];
const SORT_HEAD: Record<Sort, string> = {
  Points: 'POINTS', PPG: 'PPG', 'Last 3 games': 'LAST 3', 'This week': 'THIS WK', 'Value per $': 'PPG/$', Salary: 'SALARY', Youngest: 'AGE',
};
// Every group but Sort can hold several picks (QB and RB, Chase and Chasen); none picked means everyone.
type Filters = { pos: string[]; owner: string[]; salary: string[]; ends: string[]; type: string[]; sort: Sort };
type Group = Exclude<keyof Filters, 'sort'>;
const NO_FILTERS: Filters = { pos: [], owner: [], salary: [], ends: [], type: [], sort: 'Points' };
// Only rostered players have an owner, salary or contract, so these show rostered results even from Free agents.
const ROSTERED_ONLY: Group[] = ['owner', 'salary', 'ends', 'type'];

const salaryOk = (p: LeaguePlayer, s: string) => {
  if (p.salary == null) return false;
  return s === '$20+' ? p.salary >= 20 : p.salary < Number(s.replace(/\D/g, ''));
};
// Contract ends after `ends`: after this season means 1 year left or fewer, later seasons an exact count.
const endsOk = (p: LeaguePlayer, ends: string, season: number) => {
  if (p.years_left == null) return false;
  const left = Number(ends) - season + 1;
  return left <= 1 ? p.years_left <= 1 : p.years_left === left;
};
const typeOk = (p: LeaguePlayer, t: string) => (t === 'Rookie deal' ? p.rookie_deal : p.designation === (t === 'Taxi' ? 'taxi' : 'ir'));
const anyOf = (picked: string[], ok: (v: string) => boolean) => !picked.length || picked.some(ok);
const perDollar = (p: LeaguePlayer) => (p.rank_ppg != null && p.salary ? p.rank_ppg / p.salary : -1);
// The same sorts as the server's market.sort_rows, on the numbers the row shows.
const weekSortValue = (p: LeaguePlayer) => {
  if (p.next?.bye) return -1;
  const state = p.next?.state;
  return (state === 'in' || state === 'post') ? (p.week_pts ?? -1) : (p.proj ?? -1);
};
const SORTERS: Record<Sort, (a: LeaguePlayer, b: LeaguePlayer) => number> = {
  Points: (a, b) => (b.points ?? -1) - (a.points ?? -1),
  PPG: (a, b) => (b.rank_ppg ?? -1) - (a.rank_ppg ?? -1),
  'Last 3 games': (a, b) => (b.last3 ?? -1) - (a.last3 ?? -1),
  'This week': (a, b) => weekSortValue(b) - weekSortValue(a),
  'Value per $': (a, b) => perDollar(b) - perDollar(a),
  Salary: (a, b) => (b.salary ?? 0) - (a.salary ?? 0),
  Youngest: (a, b) => (a.age ?? 99) - (b.age ?? 99),
};

// Free agents by default (every time the tab opens), rostered and saved players a tap away, and a name search
// that always looks through everyone. Everything past the status row lives in the Filters sheet.
function PlayersList({ query }: { query?: string }) {
  const { data, error, loading, reload, refresh } = useApi<LeaguePlayers>('/market/players');
  const { ids: saved } = useTargets(refresh); // starring someone refreshes the GM plan question
  const [search, setSearch] = useState(query ?? '');
  const [status, setStatus] = useState<(typeof STATUSES)[number]>('Free agents');
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [sheet, setSheet] = useState(false);
  const [menu, setMenu] = useState(false);
  const [shown, setShown] = useState(PAGE);
  const navigation = useNavigation();
  useEffect(() => navigation.addListener('tabPress' as never, () => {
    setStatus('Free agents');
    setFilters(NO_FILTERS);
    setSearch('');
    setShown(PAGE);
  }), [navigation]);
  useEffect(() => {
    if (query != null) setSearch(query);
  }, [query]);

  const needle = search.trim();
  const rows = useMemo(() => {
    const season = data?.season ?? 0;
    const inStatus = (p: LeaguePlayer) =>
      status === 'All' ? true : status === '★ Saved' ? saved.includes(p.player_id) : (status === 'Rostered') === !!p.team_id;
    const out = (data?.players ?? []).filter((p) =>
      (needle ? nameMatches(p.name, needle) : inStatus(p))
      && anyOf(filters.pos, (v) => p.pos === v)
      && anyOf(filters.owner, (v) => p.team_id === v)
      && anyOf(filters.salary, (v) => salaryOk(p, v))
      && anyOf(filters.ends, (v) => endsOk(p, v, season))
      && anyOf(filters.type, (v) => typeOk(p, v)));
    return [...out].sort(SORTERS[filters.sort]);
  }, [data, needle, status, filters, saved]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  // the sort lives on the page now, so it doesn't count as a filter
  const active = (Object.keys(NO_FILTERS) as (keyof Filters)[]).filter((k) => k !== 'sort' && filters[k].length > 0).length;
  const sortBy = (sort: Sort) => { setFilters({ ...filters, sort }); setShown(PAGE); setMenu(false); };
  const reset = () => { setFilters({ ...NO_FILTERS, sort: filters.sort }); setShown(PAGE); };
  const change = (f: Filters) => {
    setFilters(f);
    setShown(PAGE);
    if (status === 'Free agents' && ROSTERED_ONLY.some((k) => f[k].length)) setStatus('Rostered');
  };
  const savedView = !needle && status === '★ Saved';

  return (
    <>
      <View style={styles.searchBox}>
        <TextInput
          value={search}
          onChangeText={(t) => { setSearch(t); setShown(PAGE); }}
          placeholder="Search every player"
          placeholderTextColor={colors.muted}
          style={styles.searchInput}
          autoCorrect={false}
          clearButtonMode="while-editing"
        />
        <Pressable onPress={() => setSheet(true)} hitSlop={8} style={styles.filterButton}>
          <Text style={ui.link}>{active ? `Filters · ${active}` : 'Filters'}</Text>
        </Pressable>
      </View>
      <View style={[styles.chips, needle ? styles.dim : null]}>
        {STATUSES.map((o) => (
          <Pressable key={o} onPress={() => { setStatus(o); setShown(PAGE); }} style={[styles.chip, o === status && styles.chipOn]}>
            <Text style={[styles.chipText, o === status && styles.chipTextOn]}>{o}</Text>
          </Pressable>
        ))}
      </View>
      {rows.length === 0 ? (
        <Note>{savedView && !saved.length ? 'Tap ☆ on any player to save them here.' : 'No players match.'}</Note>
      ) : null}
      {rows.length ? (
        <View style={styles.listWrap}>
          <View style={styles.sortBar}>
            <Pressable onPress={() => setMenu((m) => !m)} hitSlop={8}>
              <Text style={styles.sortText}>Sorted by <Text style={styles.sortOn}>{filters.sort} ▾</Text></Text>
            </Pressable>
            <Text style={styles.sortHint}>bars = last 6 weeks</Text>
          </View>
          {menu ? (
            <View style={styles.menu}>
              {SORTS.map((o) => (
                <Pressable key={o} onPress={() => sortBy(o)} style={styles.menuItem}>
                  <Text style={[styles.menuText, o === filters.sort && styles.menuTextOn]}>{o}{o === filters.sort ? '  ✓' : ''}</Text>
                </Pressable>
              ))}
            </View>
          ) : null}
          <View style={styles.head}>
            <Text style={[styles.headText, styles.headPlayer]}>PLAYER</Text>
            <View style={styles.headBig}><Text style={[styles.headText, styles.headGold]} numberOfLines={1}>{SORT_HEAD[filters.sort]}</Text></View>
          </View>
          <View style={styles.list}>
            {rows.slice(0, shown).map((p) => (
              <PlayerRow
                key={p.player_id}
                p={p}
                sort={filters.sort}
                line={p.team_id
                  ? `${p.is_mine ? 'You' : shortOwner(p.owner_name ?? '')}${p.salary != null ? ` · ${money(p.salary)}` : ''}${p.years_left ? ` · ${p.years_left}y` : ''}`
                  : null}
              />
            ))}
          </View>
        </View>
      ) : null}
      {rows.length > shown ? (
        <Pressable onPress={() => setShown((n) => n + PAGE)} style={styles.more}>
          <Text style={ui.link}>Show {Math.min(PAGE, rows.length - shown)} more ›</Text>
        </Pressable>
      ) : null}
      {savedView && rows.length ? (
        <Pressable
          onPress={() => router.navigate({ pathname: '/front-office', params: { q: data.plan_question ?? 'Plan my moves around my saved players.', at: String(Date.now()) } })}
          style={styles.plan}
        >
          <Text style={styles.planText}>Plan these moves with the GM ›</Text>
        </Pressable>
      ) : null}
      <FilterSheet
        open={sheet}
        onClose={() => setSheet(false)}
        filters={filters}
        owners={data.owners}
        positions={data.positions ?? POSITIONS}
        season={data.season}
        count={rows.length}
        onChange={change}
        onReset={reset}
      />
    </>
  );
}

// One group of bubbles. multi: tap to add, tap again to remove. Sort is single: tap picks it.
function SheetChips({ label, options, picked, onTap, names }: {
  label: string; options: readonly string[]; picked: (o: string) => boolean; onTap: (o: string) => void; names?: Record<string, string>;
}) {
  return (
    <>
      <Text style={styles.sheetLabel}>{label}</Text>
      <View style={styles.chips}>
        {options.map((o) => (
          <Pressable key={o} onPress={() => onTap(o)} style={[styles.chip, picked(o) && styles.chipOn]}>
            <Text style={[styles.chipText, picked(o) && styles.chipTextOn]}>{names?.[o] ?? o}</Text>
          </Pressable>
        ))}
      </View>
    </>
  );
}

function FilterSheet({ open, onClose, filters, owners, positions, season, count, onChange, onReset }: {
  open: boolean; onClose: () => void; filters: Filters; owners: LeagueOwner[]; positions: readonly string[]; season: number; count: number;
  onChange: (f: Filters) => void; onReset: () => void;
}) {
  const insets = useSafeAreaInsets();
  const multi = (k: Group) => ({
    picked: (o: string) => filters[k].includes(o),
    onTap: (o: string) => onChange({ ...filters, [k]: filters[k].includes(o) ? filters[k].filter((v) => v !== o) : [...filters[k], o] }),
  });
  const ownerNames = Object.fromEntries(owners.map((o) => [o.team_id, o.is_mine ? 'You' : shortOwner(o.owner_name)]));
  const ends = [0, 1, 2].map((i) => String(season + i));
  return (
    // iOS page sheet: swipe down (or tap Done) to close. Picks apply as you tap, so there's nothing to save.
    <Modal visible={open} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <View style={[styles.sheet, { paddingBottom: insets.bottom + 12 }]}>
        <View style={styles.grab} />
        <View style={styles.sheetHead}>
          <Pressable onPress={onReset} hitSlop={10}><Text style={ui.link}>Reset</Text></Pressable>
          <Text style={styles.sectionTitle}>Filters · {count}</Text>
          <Pressable onPress={onClose} hitSlop={10}><Text style={ui.link}>Done</Text></Pressable>
        </View>
        <ScrollView style={styles.sheetBody}>
          <SheetChips label="POSITION" options={positions} {...multi('pos')} />
          <SheetChips label="OWNER" options={owners.map((o) => o.team_id)} names={ownerNames} {...multi('owner')} />
          <SheetChips label="CONTRACT ENDS" options={ends} {...multi('ends')} />
          <SheetChips label="TYPE" options={TYPES} {...multi('type')} />
          <SheetChips label="SALARY" options={SALARY} {...multi('salary')} />
        </ScrollView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  searchBox: {
    flexDirection: 'row', alignItems: 'center', backgroundColor: colors.card, borderWidth: 1, borderColor: colors.line,
    borderRadius: 22, paddingLeft: 16, paddingRight: 14, marginBottom: 10,
  },
  searchInput: { flex: 1, paddingVertical: 11, color: colors.text, fontSize: 15 },
  filterButton: { paddingLeft: 10, paddingVertical: 8 },
  dim: { opacity: 0.45 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginBottom: 12 },
  chip: { borderWidth: 1, borderColor: colors.line, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 6 },
  chipOn: { borderColor: colors.gold },
  chipText: { color: colors.muted, fontSize: 12, fontWeight: '700' },
  chipTextOn: { color: colors.gold },
  sheet: { flex: 1, backgroundColor: '#0C2419', paddingHorizontal: 18, paddingTop: 10 },
  grab: { alignSelf: 'center', width: 40, height: 5, borderRadius: 3, backgroundColor: colors.line, marginBottom: 10 },
  sheetHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  sheetBody: { flex: 1 },
  sheetLabel: { color: colors.muted, fontSize: 11, fontWeight: '800', letterSpacing: 1, marginTop: 10, marginBottom: 8 },
  sectionTitle: { color: colors.text, fontSize: 18, fontWeight: '800' },
  list: { marginBottom: 12 },
  listWrap: { position: 'relative' },
  sortBar: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  sortText: { color: colors.muted, fontSize: 12, fontWeight: '600' },
  sortOn: { color: colors.gold, fontWeight: '800' },
  sortHint: { color: colors.muted, fontSize: 10, opacity: 0.8 },
  menu: {
    position: 'absolute', top: 24, left: 0, zIndex: 10, backgroundColor: '#163526', borderWidth: 1, borderColor: colors.gold,
    borderRadius: 12, paddingVertical: 4, minWidth: 180, shadowColor: '#000', shadowOpacity: 0.5, shadowRadius: 12, shadowOffset: { width: 0, height: 6 },
  },
  menuItem: { paddingHorizontal: 14, paddingVertical: 9 },
  menuText: { color: colors.text, fontSize: 14, fontWeight: '600' },
  menuTextOn: { color: colors.gold, fontWeight: '800' },
  head: { flexDirection: 'row', alignItems: 'flex-end', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.line, paddingBottom: 6 },
  headText: { color: colors.muted, fontSize: 10, fontWeight: '800', letterSpacing: 1 },
  headPlayer: { flex: 1 },
  headBig: { width: 68, alignItems: 'center' },
  headGold: { color: colors.gold },
  row: { flexDirection: 'row', alignItems: 'center', gap: 9, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.line },
  star: { width: 18, alignItems: 'center', paddingVertical: 10 },
  starText: { color: colors.muted, fontSize: 17 },
  starTextOn: { color: colors.gold },
  rowMid: { flex: 1, minWidth: 0, paddingVertical: 9 },
  rowNameLine: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  rowName: { color: colors.text, fontSize: 14, fontWeight: '800', flexShrink: 1 },
  rowNameGold: { color: colors.gold },
  rowTeam: { color: colors.muted, fontSize: 11, fontWeight: '700' },
  rowLine: { color: colors.muted, fontSize: 11, marginTop: 2 },
  bars: { flexDirection: 'row', alignItems: 'flex-end', gap: 3, height: 16, marginTop: 5 },
  bar: { width: 7, height: 3, borderRadius: 1, backgroundColor: '#3D5A4B' },
  barNow: { backgroundColor: colors.gold },
  barLive: { backgroundColor: GREEN },
  barMissed: { height: 2, backgroundColor: colors.line },
  barBye: { height: 0, borderTopWidth: 1, borderStyle: 'dashed', borderColor: colors.muted, backgroundColor: 'transparent', opacity: 0.6 },
  barNext: { height: 6, backgroundColor: 'transparent', borderWidth: 1, borderColor: colors.line },
  big: { width: 68, alignSelf: 'stretch', alignItems: 'center', justifyContent: 'center', backgroundColor: colors.row, paddingHorizontal: 4 },
  bigText: { fontSize: 19, fontWeight: '800' },
  bigSub: { color: colors.muted, fontSize: 9, fontWeight: '600', marginTop: 2 },
  headCenter: { textAlign: 'center' },
  more: { alignItems: 'center', paddingVertical: 8, marginBottom: 4 },
  plan: { backgroundColor: colors.gold, borderRadius: 12, paddingVertical: 12, alignItems: 'center', marginTop: 10 },
  planText: { color: colors.bg, fontWeight: '800', fontSize: 15 },
});
