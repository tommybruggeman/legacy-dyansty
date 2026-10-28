import { router } from 'expo-router';
import { useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { ErrorState, Loading } from '../../components/ScreenState';
import { Card, Note, Pill, Pos, Row, Screen, Segmented, SwipeTabs, Title, money, ui } from '../../components/ui';
import { type Expiring, type FreeAgents, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

const VIEWS = ['Free Agents', 'Expiring'] as const;
const POSITIONS = ['All', 'QB', 'RB', 'WR', 'TE', 'K'] as const;

// Browse only: bidding and the rookie draft happen on the website, trades in Sleeper.
export default function Market() {
  const [view, setView] = useState<(typeof VIEWS)[number]>('Free Agents');
  return (
    <Screen>
      <Title>Market</Title>
      <Segmented options={VIEWS} value={view} onChange={setView} />
      <SwipeTabs options={VIEWS} value={view} onChange={setView} render={(v) => (v === 'Free Agents' ? <FreeAgentList /> : <ExpiringList />)} />
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

function FreeAgentList() {
  const { data, error, loading, reload } = useApi<FreeAgents>('/market/free-agents');
  const [search, setSearch] = useState('');
  const [pos, setPos] = useState<(typeof POSITIONS)[number]>('All');

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (data?.players ?? []).filter(
      (p) => (pos === 'All' || p.pos === pos) && (!needle || p.name.toLowerCase().includes(needle)),
    );
  }, [data, search, pos]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const ppgLabel = data.season_started ? `${data.season} PPG` : `${data.last_season} PPG`;

  return (
    <>
      <TextInput
        value={search}
        onChangeText={setSearch}
        placeholder="Search players"
        placeholderTextColor={colors.muted}
        style={styles.search}
        autoCorrect={false}
      />
      <Chips options={POSITIONS} value={pos} onChange={setPos} />
      <Card title="Open market" right={ppgLabel}>
        {rows.length === 0 ? <Note>No free agents match.</Note> : null}
        {rows.slice(0, 60).map((p, i) => (
          <Row key={p.player_id} first={i === 0} onPress={() => router.push(`/player/${p.player_id}`)}>
            <Pos label={p.pos} />
            <View style={ui.flex}>
              <View style={styles.nameLine}>
                <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
                {p.injury ? <Pill label={p.injury.toUpperCase()} tone="out" /> : p.rookie ? <Pill label="ROOKIE" tone="gold" /> : null}
              </View>
              <Text style={ui.sub}>
                {p.nfl_team}{p.last_ppg != null && data.season_started ? ` · ${p.last_ppg.toFixed(1)} last year` : ''}
              </Text>
            </View>
            <Text style={[ui.value, ui.gold]}>{p.rank_ppg != null ? p.rank_ppg.toFixed(1) : '—'}</Text>
          </Row>
        ))}
      </Card>
      <Note>Bids happen in the free agent auction on the website.</Note>
    </>
  );
}

function ExpiringList() {
  const [year, setYear] = useState<string | null>(null);
  const { data, error, loading, reload } = useApi<Expiring>(year ? `/market/expiring?year=${year}` : '/market/expiring');
  const [pos, setPos] = useState<string>('All');
  const options = ['All', 'QB', 'RB', 'WR', 'TE', 'Mine'] as const;

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const rows = data.players.filter((p) => pos === 'All' || (pos === 'Mine' ? p.is_mine : p.pos === pos));

  const years = data.years.map(String);

  return (
    <>
      <Segmented options={years} value={String(data.season)} onChange={setYear} />
      <Chips options={options} value={pos as (typeof options)[number]} onChange={setPos} />
      <Card title={`Free agents after ${data.season}`} right="owner · salary">
        {rows.length === 0 ? <Note>Nothing expiring here.</Note> : null}
        {rows.map((p, i) => (
          <Row key={p.player_id} first={i === 0} onPress={() => router.push(`/player/${p.player_id}`)}>
            <Pos label={p.pos} />
            <View style={ui.flex}>
              <View style={styles.nameLine}>
                <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
                {p.is_mine ? <Pill label="YOURS" tone="gold" /> : null}
              </View>
              <Text style={ui.sub}>{p.owner_name}</Text>
            </View>
            <Text style={ui.value}>{money(p.salary)}</Text>
          </Row>
        ))}
      </Card>
      <Note>These hit free agency at the {data.season} rollover unless their owner extends them.</Note>
    </>
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
  nameLine: { flexDirection: 'row', alignItems: 'center', gap: 6 },
});
