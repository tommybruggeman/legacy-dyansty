import { LinearGradient } from 'expo-linear-gradient';
import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { PlayerFace } from '../../components/PlayerFace';
import { ErrorState, Loading } from '../../components/ScreenState';
import { Note, Pill, Screen, Title, money, ui } from '../../components/ui';
import { type LeagueOwner, type LeaguePlayer, type LeaguePlayers, useApi } from '../../lib/api';
import { teamColors } from '../../lib/nflColors';
import { nameMatches } from '../../lib/players';
import { colors } from '../../lib/theme';
import { useTargets } from '../../lib/targets';

const POSITIONS = ['All', 'QB', 'RB', 'WR', 'TE', 'K'] as const;
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

const STATUSES = ['Free agents', 'Rostered', '★ Saved', 'All'] as const;
const SALARY = ['Any', 'Under $5', 'Under $10', 'Under $20', '$20+'] as const;
const TYPES = ['Any', 'Rookie deal', 'Taxi', 'IR'] as const;
const SORTS = ['Points', 'PPG', 'Value per $', 'Salary', 'Youngest'] as const;
type Filters = {
  pos: (typeof POSITIONS)[number]; owner: string; salary: (typeof SALARY)[number];
  ends: string; type: (typeof TYPES)[number]; sort: (typeof SORTS)[number];
};
const NO_FILTERS: Filters = { pos: 'All', owner: 'all', salary: 'Any', ends: 'Any', type: 'Any', sort: 'Points' };

const salaryOk = (p: LeaguePlayer, s: Filters['salary']) => {
  if (s === 'Any') return true;
  if (p.salary == null) return false;
  return s === '$20+' ? p.salary >= 20 : p.salary < Number(s.replace(/\D/g, ''));
};
// Contract ends after `ends`: after this season means 1 year left or fewer, later seasons an exact count.
const endsOk = (p: LeaguePlayer, ends: string, season: number) => {
  if (ends === 'Any') return true;
  if (p.years_left == null) return false;
  const left = Number(ends) - season + 1;
  return left <= 1 ? p.years_left <= 1 : p.years_left === left;
};
const typeOk = (p: LeaguePlayer, t: Filters['type']) =>
  t === 'Any' ? true : t === 'Rookie deal' ? p.rookie_deal : p.designation === (t === 'Taxi' ? 'taxi' : 'ir');
const perDollar = (p: LeaguePlayer) => (p.rank_ppg != null && p.salary ? p.rank_ppg / p.salary : -1);
const SORTERS: Record<Filters['sort'], (a: LeaguePlayer, b: LeaguePlayer) => number> = {
  Points: () => 0, // the server already ranks by points
  PPG: (a, b) => (b.rank_ppg ?? -1) - (a.rank_ppg ?? -1),
  'Value per $': (a, b) => perDollar(b) - perDollar(a),
  Salary: (a, b) => (b.salary ?? 0) - (a.salary ?? 0),
  Youngest: (a, b) => (a.age ?? 99) - (b.age ?? 99),
};

// Free agents by default, rostered and saved players a tap away, and a name search that always looks through
// everyone. Everything past the status row lives in the Filters sheet.
function PlayersList({ query }: { query?: string }) {
  const { data, error, loading, reload, refresh } = useApi<LeaguePlayers>('/market/players');
  const { ids: saved } = useTargets(refresh); // starring someone refreshes the GM plan question
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
    const season = data?.season ?? 0;
    const inStatus = (p: LeaguePlayer) =>
      status === 'All' ? true : status === '★ Saved' ? saved.includes(p.player_id) : (status === 'Rostered') === !!p.team_id;
    const out = (data?.players ?? []).filter((p) =>
      (needle ? nameMatches(p.name, needle) : inStatus(p))
      && (filters.pos === 'All' || p.pos === filters.pos)
      && (filters.owner === 'all' || p.team_id === filters.owner)
      && salaryOk(p, filters.salary)
      && endsOk(p, filters.ends, season)
      && typeOk(p, filters.type));
    return filters.sort === 'Points' ? out : [...out].sort(SORTERS[filters.sort]);
  }, [data, needle, status, filters, saved]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const rankSeason = data.season_started ? data.season : data.last_season;
  const active = (Object.keys(NO_FILTERS) as (keyof Filters)[]).filter((k) => filters[k] !== NO_FILTERS[k]);
  const owner = data.owners.find((o) => o.team_id === filters.owner);
  const label = needle ? 'WHOLE LEAGUE' : status === '★ Saved' ? 'SAVED' : status.toUpperCase();
  const summary = [
    label,
    filters.pos !== 'All' ? filters.pos : null,
    owner ? (owner.is_mine ? 'YOURS' : owner.owner_name.toUpperCase()) : null,
    filters.salary !== 'Any' ? filters.salary.toUpperCase() : null,
    filters.ends !== 'Any' ? `ENDS ${filters.ends}` : null,
    filters.type !== 'Any' ? filters.type.toUpperCase() : null,
    String(rows.length),
  ].filter(Boolean).join(' · ');
  const reset = () => { setFilters(NO_FILTERS); setShown(PAGE); };
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
          <Text style={ui.link}>{active.length ? `⚙ Filters · ${active.length}` : '⚙ Filters'}</Text>
        </Pressable>
      </View>
      <View style={[styles.chips, needle ? styles.dim : null]}>
        {STATUSES.map((o) => (
          <Pressable key={o} onPress={() => { setStatus(o); setShown(PAGE); }} style={[styles.chip, o === status && styles.chipOn]}>
            <Text style={[styles.chipText, o === status && styles.chipTextOn]}>{o}</Text>
          </Pressable>
        ))}
      </View>
      <View style={styles.sectionHead}>
        <Text style={[styles.sectionRight, ui.flex]} numberOfLines={1}>{summary}</Text>
        <Text style={styles.sectionRight}>{filters.sort === 'Points' ? `MOST ${rankSeason} POINTS` : filters.sort.toUpperCase()}</Text>
      </View>
      {rows.length === 0 ? (
        <Note>{savedView && !saved.length ? 'Tap ☆ on any player to save them here.' : 'No players match.'}</Note>
      ) : null}
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
      {savedView && rows.length ? (
        <Pressable
          onPress={() => router.navigate({ pathname: '/front-office', params: { q: data.plan_question ?? 'Plan my moves around my saved players.', at: String(Date.now()) } })}
          style={styles.plan}
        >
          <Text style={styles.planText}>Plan these moves with the GM ›</Text>
        </Pressable>
      ) : null}
      {filters.ends !== 'Any'
        ? <Note>These hit free agency at the {filters.ends} rollover unless their owner extends them.</Note>
        : <Note>Bids happen in the free agent auction on the website.</Note>}
      <FilterSheet
        open={sheet}
        onClose={() => setSheet(false)}
        filters={filters}
        owners={data.owners}
        season={data.season}
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

function FilterSheet({ open, onClose, filters, owners, season, count, onChange, onReset }: {
  open: boolean; onClose: () => void; filters: Filters; owners: LeagueOwner[]; season: number; count: number;
  onChange: (f: Filters) => void; onReset: () => void;
}) {
  const insets = useSafeAreaInsets();
  const set = <K extends keyof Filters>(k: K) => (v: Filters[K]) => onChange({ ...filters, [k]: v });
  const ownerIds = ['all', ...owners.map((o) => o.team_id)];
  const ownerNames = Object.fromEntries([['all', 'Everyone'], ...owners.map((o) => [o.team_id, o.is_mine ? 'You' : shortOwner(o.owner_name)])]);
  const ends = ['Any', ...[0, 1, 2].map((i) => String(season + i))];
  const endNames = { [String(season)]: `After ${season}` };
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
          <SheetChips label="CONTRACT ENDS" options={ends} value={filters.ends} onChange={set('ends')} names={endNames} />
          <SheetChips label="TYPE" options={TYPES} value={filters.type} onChange={set('type')} />
          <SheetChips label="SALARY" options={SALARY} value={filters.salary} onChange={set('salary')} />
          <SheetChips label="SORT" options={SORTS} value={filters.sort} onChange={set('sort')} />
        </ScrollView>
        <Pressable onPress={onClose} style={styles.plan}>
          <Text style={styles.planText}>Show {count} players</Text>
        </Pressable>
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
  plan: { backgroundColor: colors.gold, borderRadius: 12, paddingVertical: 12, alignItems: 'center', marginTop: 10 },
  planText: { color: colors.bg, fontWeight: '800', fontSize: 15 },
});
