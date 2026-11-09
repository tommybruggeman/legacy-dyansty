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
const ppgText = (n: number | null | undefined) => (n != null ? n.toFixed(1) : '—');

// ESPN-style row: star, face, name with team and any tag, two short lines, then three numbers: this week (projection
// before kickoff, live points in green during his game, final points after), the last-3 average (green or red when
// it's more than a point off his season) and PPG with games played under it. Line 2 is the owner and contract for
// rostered players, age and position rank for free agents; line 3 is his game: kickoff with a tough/soft defense
// note, the live clock, or the final score. Your own and saved players get a gold name.
function PlayerRow({ p, line }: { p: LeaguePlayer; line: string | null }) {
  const { isTarget, toggle } = useTargets();
  const saved = isTarget(p.player_id);
  const tag = p.injury ? { label: injuryLabel(p.injury), tone: 'out' as const }
    : p.designation ? { label: p.designation.toUpperCase(), tone: 'gold' as const }
    : p.rookie ? { label: 'ROOKIE', tone: 'gold' as const } : null;
  const ppg = p.avg ?? p.rank_ppg;
  const facts = [p.age != null ? `Age ${p.age}` : null, p.pos_rank ?? null].filter(Boolean).join(' · ');
  const off = p.last3 != null && p.trend != null && Math.abs(p.trend) > 1 ? (p.trend > 0 ? GREEN : RED) : colors.text;
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
        {line ?? facts ? <Text style={styles.rowLine} numberOfLines={1}>{line ?? facts}</Text> : null}
        <NextLine next={p.next} />
      </View>
      <WeekCell p={p} />
      <View style={styles.col}><Text style={[styles.last3, { color: off }]}>{p.last3 != null ? p.last3.toFixed(1) : '—'}</Text></View>
      <View style={styles.col}>
        <Text style={styles.ppg}>{ppgText(ppg)}</Text>
        {p.games ? <Text style={styles.games}>{p.games} gms</Text> : null}
      </View>
    </Pressable>
  );
}

// This week's number: projection before kickoff, live points (green, with a dot) during, final points after.
function WeekCell({ p }: { p: LeaguePlayer }) {
  const state = p.next?.state;
  const out = !!p.injury && OUT.has(p.injury.toLowerCase());
  let text = p.proj != null ? p.proj.toFixed(1) : '—';
  let style = styles.proj;
  if (p.next?.bye) { text = 'BYE'; style = styles.weekDim; }
  else if ((state === 'in' || state === 'post') && p.week_pts != null) { text = p.week_pts.toFixed(1); style = state === 'in' ? styles.weekLive : styles.proj; }
  else if (out) { text = injuryLabel(p.injury ?? ''); style = styles.weekDim; }
  return (
    <View style={[styles.col, styles.colProj]}>
      <View style={styles.weekRow}>
        {state === 'in' && !p.next?.bye ? <View style={styles.liveDot} /> : null}
        <Text style={style}>{text}</Text>
      </View>
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

function NextLine({ next }: { next?: NextGame | null }) {
  if (!next) return null;
  if (next.bye) return <Text style={styles.rowLine}>Bye week</Text>;
  const where = `${next.home ? 'vs' : '@'} ${next.opp}`;
  if (next.state === 'post' || (next.final && next.state == null)) {
    const won = next.score != null && next.opp_score != null ? (next.score > next.opp_score ? 'W' : next.score < next.opp_score ? 'L' : 'T') : null;
    return <Text style={styles.rowLine} numberOfLines={1}>Final{won ? ` · ${won} ${next.score}-${next.opp_score}` : ''} {where}</Text>;
  }
  if (next.state === 'in') {
    return (
      <Text style={styles.rowLine} numberOfLines={1}>
        <Text style={{ color: GREEN }}>● {next.detail || 'Live'}</Text>{` · ${where}`}{next.score != null ? ` · ${next.score}-${next.opp_score}` : ''}
      </Text>
    );
  }
  const when = next.kickoff
    ? (() => { const d = new Date(next.kickoff); return `${d.toLocaleDateString(undefined, { weekday: 'short' })} ${d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`; })()
    : '';
  const teams = next.teams ?? 32;
  // rank 1 = fewest points allowed to his position (toughest): the top third is a tough defense, the bottom third a soft one
  const matchup = next.opp_rank == null ? null : next.opp_rank <= teams / 3 ? { text: 'tough D', color: RED } : next.opp_rank > (teams * 2) / 3 ? { text: 'soft D', color: GREEN } : null;
  return (
    <Text style={styles.rowLine} numberOfLines={1}>
      {when ? `${when} · ` : ''}{where}{matchup ? <Text style={{ color: matchup.color }}>{` · ${matchup.text}`}</Text> : null}
    </Text>
  );
}

const STATUSES = ['Free agents', 'Rostered', '★ Saved', 'All'] as const;
const SALARY = ['Under $5', 'Under $10', 'Under $20', '$20+'] as const;
const TYPES = ['Rookie deal', 'Taxi', 'IR'] as const;
const SORTS = ['Points', 'PPG', 'Value per $', 'Salary', 'Youngest'] as const;
type Sort = (typeof SORTS)[number];
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
const SORTERS: Record<Sort, (a: LeaguePlayer, b: LeaguePlayer) => number> = {
  Points: () => 0, // the server already ranks by points
  PPG: (a, b) => (b.rank_ppg ?? -1) - (a.rank_ppg ?? -1),
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
    return filters.sort === 'Points' ? out : [...out].sort(SORTERS[filters.sort]);
  }, [data, needle, status, filters, saved]);
  const weekNo = data?.players.find((p) => p.next?.week)?.next?.week;

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const active = (Object.keys(NO_FILTERS) as (keyof Filters)[])
    .filter((k) => (k === 'sort' ? filters.sort !== 'Points' : filters[k].length > 0)).length;
  const reset = () => { setFilters(NO_FILTERS); setShown(PAGE); };
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
        <View style={styles.head}>
          <Text style={[styles.headText, styles.headPlayer]}>PLAYER</Text>
          <View style={[styles.col, styles.colProj, styles.headProj]}><Text style={[styles.headText, styles.headGold, styles.headCenter]}>{weekNo ? `WEEK\n${weekNo}` : 'WEEK'}</Text></View>
          <View style={styles.col}><Text style={[styles.headText, styles.headCenter]}>LAST{'\n'}3</Text></View>
          <View style={styles.col}><Text style={styles.headText}>PPG</Text></View>
        </View>
      ) : null}
      <View style={styles.list}>
        {rows.slice(0, shown).map((p) => (
          <PlayerRow
            key={p.player_id}
            p={p}
            line={p.team_id
              ? `${p.is_mine ? 'You' : shortOwner(p.owner_name ?? '')}${p.salary != null ? ` · ${money(p.salary)}` : ''}${p.years_left ? ` · ${p.years_left}y` : ''}`
              : null}
          />
        ))}
      </View>
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
          <SheetChips label="SORT" options={SORTS} picked={(o) => filters.sort === o} onTap={(o) => onChange({ ...filters, sort: o as Sort })} />
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
  head: { flexDirection: 'row', alignItems: 'flex-end', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.line, paddingBottom: 6 },
  headText: { color: colors.muted, fontSize: 10, fontWeight: '800', letterSpacing: 1 },
  headPlayer: { flex: 1 },
  headProj: { borderTopLeftRadius: 6, borderTopRightRadius: 6, paddingTop: 4 },
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
  rowLine: { color: colors.muted, fontSize: 11, marginTop: 1 },
  col: { width: 44, alignItems: 'center', justifyContent: 'center', alignSelf: 'stretch' },
  colProj: { backgroundColor: colors.row },
  proj: { color: colors.text, fontSize: 15, fontWeight: '800' },
  weekLive: { color: GREEN, fontSize: 15, fontWeight: '800' },
  weekDim: { color: colors.muted, fontSize: 11, fontWeight: '800', opacity: 0.8 },
  weekRow: { flexDirection: 'row', alignItems: 'center', gap: 2 },
  liveDot: { width: 5, height: 5, borderRadius: 3, backgroundColor: GREEN },
  last3: { fontSize: 14, fontWeight: '800' },
  ppg: { color: colors.gold, fontSize: 15, fontWeight: '800' },
  games: { color: colors.muted, fontSize: 9, fontWeight: '600', marginTop: 1 },
  headCenter: { textAlign: 'center' },
  more: { alignItems: 'center', paddingVertical: 8, marginBottom: 4 },
  plan: { backgroundColor: colors.gold, borderRadius: 12, paddingVertical: 12, alignItems: 'center', marginTop: 10 },
  planText: { color: colors.bg, fontWeight: '800', fontSize: 15 },
});
