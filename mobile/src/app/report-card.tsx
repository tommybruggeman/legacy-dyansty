import { StyleSheet, Text, View } from 'react-native';

import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Card, Note, Pill, Row, Screen, Tile, Tiles, Title, ui } from '../components/ui';
import { type FrontOffice, useApi } from '../lib/api';
import { colors } from '../lib/theme';

const LABEL: Record<string, string> = { start_sit: 'START/SIT', trade: 'TRADE CALLS', pickup: 'PICKUPS' };

const pct = (record: string) => {
  const [w, l] = record.split('-').map(Number);
  return w + l ? `${Math.round((100 * w) / (w + l))}% right` : '';
};

// How the Assistant GM's calls graded against real results, and what it learned.
export default function ReportCard() {
  const { data, error, loading, reload } = useApi<FrontOffice>('/front-office');

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const card = data.report_card;
  const kinds = Object.entries(card.record).slice(0, 2);

  return (
    <Screen loading={loading} onRefresh={reload}>
      <Back label="Front Office" />
      <Title>GM report card</Title>
      <Tiles>
        {kinds.map(([kind, rec]) => <Tile key={kind} label={LABEL[kind] ?? kind.toUpperCase()} value={rec} sub={pct(rec)} />)}
        <Tile label="LESSONS" value={String(card.lessons.length)} sub="learned" />
      </Tiles>
      <Card title="Recent calls" right={card.open_calls ? `${card.open_calls} waiting on games` : undefined}>
        {card.recent.length === 0 ? (
          <Note>No graded calls yet. Ask who to start or whether to take a trade, and the call gets graded after the games.</Note>
        ) : null}
        {card.recent.map((r, i) => (
          <Row key={`${r.when}-${i}`} first={i === 0}>
            <Pill label={r.right ? 'RIGHT' : 'MISS'} tone={r.right ? 'live' : 'out'} />
            <View style={ui.flex}>
              <Text style={ui.value}>{r.call.charAt(0).toUpperCase() + r.call.slice(1)} ({r.points})</Text>
              <Text style={ui.sub}>{r.when}{r.why ? ` · ${r.why}` : ''}</Text>
            </View>
          </Row>
        ))}
      </Card>
      {card.lessons.length ? (
        <Card>
          <Text style={styles.eyebrow}>WHAT IT LEARNED</Text>
          {card.lessons.map((l) => (
            <Text key={l.lesson} style={styles.lesson}>• {l.lesson}{l.seen > 1 ? <Text style={ui.sub}>  (seen {l.seen}x)</Text> : null}</Text>
          ))}
        </Card>
      ) : null}
      <Note>Graded every morning against real results.</Note>
    </Screen>
  );
}

const styles = StyleSheet.create({
  eyebrow: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1.2, marginBottom: 6 },
  lesson: { color: colors.text, fontSize: 14, lineHeight: 21, marginBottom: 4 },
});
