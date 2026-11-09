import { useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Headshot } from '../../components/Pictures';
import { ErrorState, Loading } from '../../components/ScreenState';
import { Back, Screen } from '../../components/ui';
import { type GameLogRow, type PlayerPage, useApi } from '../../lib/api';
import { LIVE_REFRESH_MS } from '../../components/team/TeamView';
import { colors } from '../../lib/theme';

export { ScreenError as ErrorBoundary } from '../../components/ScreenState';

// The full game log, opened from the player page's points chart: a week-by-week table
// (Receiving / Rushing / Passing) with his league points, snap share and a per-game average row.
// It reads the same /players response, so it opens instantly from cache.
export default function PlayerGames() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [live, setLive] = useState(false);
  const [tab, setTab] = useState<TabKey | null>(null);
  // While his game is on, refresh as often as the team page does.
  const { data, error, loading, reload } = useApi<PlayerPage>(`/players/${id}`, live ? LIVE_REFRESH_MS : 0);
  useEffect(() => setLive(!!data?.live), [data]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const log = [...(data.game_log ?? [])].sort((x, y) => x.week - y.week);
  // His main tab comes first (RB: Rushing, WR/TE: Receiving, QB: Passing then Rushing); QBs skip Receiving.
  const first = DEFAULT_TAB[data.pos] ?? 'rec';
  const tabs = TABS.filter((t) => (data.pos === 'QB' ? t.key !== 'rec' : t.key !== 'pass'))
    .sort((x, y) => Number(y.key === first) - Number(x.key === first));
  const active = tabs.find((t) => t.key === tab) ?? tabs.find((t) => t.key === DEFAULT_TAB[data.pos]) ?? tabs[0];
  const snaps = data.pos !== 'QB';
  const games = log.filter((g) => g.line && g.status !== 'bye' && g.status !== 'out');
  const scored = log.filter((g) => g.points != null);
  const avgPts = scored.length ? scored.reduce((s, g) => s + (g.points ?? 0), 0) / scored.length : null;

  return (
    <Screen loading={loading}>
      <Back label={data.name} />
      <View style={styles.stack}>
        <View style={styles.hero}>
          <Headshot playerId={data.player_id} pos={data.pos} team={data.nfl_team} size={56} />
          <View style={{ flex: 1 }}>
            <Text style={styles.eyebrow}>{data.pos} · {data.nfl_team} · {data.season}</Text>
            <Text style={styles.title}>Game log</Text>
          </View>
        </View>

        <View style={styles.seg}>
          {tabs.map((t) => (
            <Pressable key={t.key} onPress={() => setTab(t.key)} style={[styles.segItem, active.key === t.key && styles.segOn]}>
              <Text style={[styles.segText, active.key === t.key && styles.segTextOn]}>{t.label}</Text>
            </Pressable>
          ))}
        </View>

        <View style={styles.table}>
          <View style={[styles.row, styles.headRow]}>
            <Text style={[styles.th, styles.cWk]}>WK</Text>
            <Text style={[styles.th, styles.cOpp]}>OPP</Text>
            <Text style={[styles.th, styles.cNum, styles.cPts, { color: colors.gold }]}>PTS</Text>
            {active.cols.map((c) => <Text key={c.label} style={[styles.th, styles.cNum]}>{c.label}</Text>)}
            {snaps ? <Text style={[styles.th, styles.cNum]}>SNP</Text> : null}
          </View>
          {log.map((g) => {
            const off = g.status === 'bye' || g.status === 'out' || !g.line;
            return (
              <View key={g.week} style={styles.row}>
                <Text style={[styles.cell, styles.cWk]}>{g.week}</Text>
                <Text style={[styles.cell, styles.cOpp, styles.dim]} numberOfLines={1}>{g.status === 'bye' ? 'BYE' : g.opp}</Text>
                <Text style={[styles.cell, styles.cNum, styles.cPts, styles.ptsCell, g.status === 'started' && styles.started, g.live && styles.live]}>
                  {g.status === 'out' ? 'OUT' : g.points != null ? g.points.toFixed(1) : '–'}
                </Text>
                {active.cols.map((c) => (
                  <Text key={c.label} style={[styles.cell, styles.cNum, !off && (c.get(g.line!) ?? 0) > 0 && (c.td ? styles.td : c.bad ? styles.bad : null)]}>
                    {off ? '–' : String(c.get(g.line!) ?? 0)}
                  </Text>
                ))}
                {snaps ? <Text style={[styles.cell, styles.cNum, styles.dim]}>{!off && g.line?.snap_pct != null ? `${g.line.snap_pct}%` : '–'}</Text> : null}
              </View>
            );
          })}
          {games.length ? (
            <View style={[styles.row, styles.headRow]}>
              <Text style={[styles.cell, styles.cWk, styles.bold]}>AVG</Text>
              <Text style={[styles.cell, styles.cOpp, styles.dim]}>{games.length} gm</Text>
              <Text style={[styles.cell, styles.cNum, styles.cPts, styles.bold, { color: colors.gold }]}>{avgPts != null ? avgPts.toFixed(1) : '–'}</Text>
              {active.cols.map((c) => {
                const v = games.reduce((s, g) => s + (c.get(g.line!) ?? 0), 0) / games.length;
                return <Text key={c.label} style={[styles.cell, styles.cNum, styles.bold]}>{v >= 100 ? v.toFixed(0) : v.toFixed(1)}</Text>;
              })}
              {snaps ? <Text style={[styles.cell, styles.cNum, styles.bold]}>{snapAvg(games)}</Text> : null}
            </View>
          ) : null}
        </View>
        <Text style={styles.note}>Gold points = started that week. Legacy points use our league's scoring; AVG counts games he played. SNP is his share of offensive snaps.</Text>
      </View>
    </Screen>
  );
}

type Line = NonNullable<GameLogRow['line']>;
type Col = { label: string; get: (l: Line) => number | undefined; td?: boolean; bad?: boolean };
type TabKey = 'rec' | 'rush' | 'pass';
const TABS: { key: TabKey; label: string; cols: Col[] }[] = [
  { key: 'rec', label: 'Receiving', cols: [
    { label: 'REC', get: (l) => l.rec }, { label: 'TAR', get: (l) => l.targets },
    { label: 'YDS', get: (l) => l.rec_yds }, { label: 'TD', get: (l) => l.rec_td, td: true },
  ] },
  { key: 'rush', label: 'Rushing', cols: [
    { label: 'CAR', get: (l) => l.carries }, { label: 'YDS', get: (l) => l.rush_yds },
    { label: 'YPC', get: (l) => (l.carries ? Math.round((l.rush_yds / l.carries) * 10) / 10 : 0) }, { label: 'TD', get: (l) => l.rush_td, td: true },
  ] },
  { key: 'pass', label: 'Passing', cols: [
    { label: 'CMP', get: (l) => l.comp }, { label: 'ATT', get: (l) => l.att },
    { label: 'YDS', get: (l) => l.pass_yds }, { label: 'TD', get: (l) => l.pass_td, td: true }, { label: 'INT', get: (l) => l.ints, bad: true },
  ] },
];
const DEFAULT_TAB: Record<string, TabKey> = { QB: 'pass', RB: 'rush', WR: 'rec', TE: 'rec' };

function snapAvg(games: GameLogRow[]): string {
  const s = games.map((g) => g.line?.snap_pct).filter((v): v is number => v != null);
  return s.length ? `${Math.round(s.reduce((a, b) => a + b, 0) / s.length)}%` : '–';
}

const styles = StyleSheet.create({
  stack: { gap: 14 },
  hero: { flexDirection: 'row', gap: 12, alignItems: 'center' },
  eyebrow: { color: colors.gold, fontSize: 11, fontWeight: '700', letterSpacing: 1 },
  title: { color: colors.text, fontSize: 24, fontWeight: '800' },
  seg: { flexDirection: 'row', backgroundColor: colors.card, borderRadius: 12, padding: 3, gap: 3 },
  segItem: { flex: 1, alignItems: 'center', paddingVertical: 8, borderRadius: 9 },
  segOn: { backgroundColor: '#23392F' },
  segText: { color: '#8E8676', fontSize: 13, fontWeight: '700' },
  segTextOn: { color: colors.text },
  table: { backgroundColor: colors.card, borderRadius: 16, overflow: 'hidden' },
  row: { flexDirection: 'row', alignItems: 'center', paddingVertical: 11, paddingHorizontal: 12, borderTopWidth: 1, borderColor: '#1F3F31' },
  headRow: { backgroundColor: '#0D2219', borderTopWidth: 0, paddingVertical: 9 },
  th: { color: colors.muted, fontSize: 10, fontWeight: '800', letterSpacing: 0.6 },
  cell: { color: colors.text, fontSize: 13, fontVariant: ['tabular-nums'] },
  cWk: { width: 30 },
  cOpp: { width: 50 },
  cNum: { flex: 1, textAlign: 'right' },
  cPts: { flex: 1.2 },
  ptsCell: { fontWeight: '800', color: colors.muted },
  started: { color: colors.gold },
  live: { color: colors.live },
  td: { color: colors.live, fontWeight: '800' },
  bad: { color: '#E8826F', fontWeight: '800' },
  dim: { color: colors.muted },
  bold: { fontWeight: '800' },
  note: { color: colors.muted, fontSize: 12, lineHeight: 18 },
});
