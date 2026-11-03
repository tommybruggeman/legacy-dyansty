import { StyleSheet, Text, View } from 'react-native';

import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Bar, Card, Note, Pill, Pos, Row, Screen, Tile, Tiles, Title, ui } from '../components/ui';
import { type Accuracy, type FrontOffice, type ProjectionCard, useApi } from '../lib/api';
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
      {card.accuracy ? <AccuracyCards a={card.accuracy} /> : null}
      {card.projections ? <ProjectionCards c={card.projections} /> : null}
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

const p = (x: number) => `${Math.round(100 * x)}%`;

// Every start/sit call in every lineup this season: the engine's pick vs what the owners started.
function AccuracyCards({ a }: { a: Accuracy }) {
  const weeks = a.by_week.slice(-6).reverse();
  return (
    <>
      <Card title={`Start/sit engine, ${a.season}`} right={`${a.overall.calls} calls`}>
        <Text style={ui.sub}>Every start/sit choice in every lineup in the league, graded after the games.</Text>
        <View style={styles.vs}>
          <View style={ui.flex}>
            <Text style={styles.big}>{p(a.overall.engine)}</Text>
            <Text style={ui.sub}>engine right</Text>
          </View>
          <View style={ui.flex}>
            <Text style={styles.big}>{p(a.overall.owners)}</Text>
            <Text style={ui.sub}>owners right</Text>
          </View>
          {a.you ? (
            <View style={ui.flex}>
              <Text style={styles.big}>{p(a.you.you)}</Text>
              <Text style={ui.sub}>you ({a.you.calls})</Text>
            </View>
          ) : null}
        </View>
        {a.you ? (
          <Note>
            {a.you.points_per_call > 0
              ? `Following the engine would have added ${a.you.points_per_call.toFixed(1)} pts per call this season.`
              : `Your own calls beat the engine by ${Math.abs(a.you.points_per_call).toFixed(1)} pts per call this season.`}
          </Note>
        ) : null}
      </Card>
      <Card title="By position">
        {a.by_position.map((r, i) => (
          <Row key={r.pos} first={i === 0}>
            <Pos label={r.pos} />
            <View style={ui.flex}>
              <Bar fraction={r.engine} />
              <Text style={ui.sub}>engine {p(r.engine)} · owners {p(r.owners)} · {r.calls} calls</Text>
            </View>
          </Row>
        ))}
      </Card>
      {weeks.length ? (
        <Card title="By week">
          {weeks.map((r, i) => (
            <Row key={r.week} first={i === 0}>
              <Text style={[ui.value, styles.week]}>Wk {r.week}</Text>
              <View style={ui.flex}>
                <Bar fraction={r.engine} />
                <Text style={ui.sub}>engine {p(r.engine)} · owners {p(r.owners)} · {r.calls} calls</Text>
              </View>
            </Row>
          ))}
        </Card>
      ) : null}
    </>
  );
}

const pts = (x: number) => x.toFixed(1);
const SHOW = ['QB', 'RB', 'WR', 'TE', 'K'];

// Weekly projections vs what players really scored, and the position factors learned from it.
function ProjectionCards({ c }: { c: ProjectionCard }) {
  const player = c.overall.as_shown ?? c.overall.calibrated;
  const team = c.overall.team_calibrated;
  const before = c.overall.shown;
  const weeks = c.by_week.slice(-6).reverse();
  const scaled = SHOW.filter((p) => c.factors[p] != null && Math.abs(c.factors[p] - 1) >= 0.02);
  if (!player) return null;
  return (
    <>
      <Card title={`Weekly projections, ${c.season}`} right={`${player.n} players graded`}>
        <Text style={ui.sub}>Every projection compared with the real score once the games are final.</Text>
        <View style={styles.vs}>
          <View style={ui.flex}>
            <Text style={styles.big}>{pts(player.mae)}</Text>
            <Text style={ui.sub}>avg miss per player</Text>
          </View>
          {team ? (
            <View style={ui.flex}>
              <Text style={styles.big}>{pts(team.mae)}</Text>
              <Text style={ui.sub}>avg miss per team</Text>
            </View>
          ) : null}
        </View>
        {before ? <Note>{`Before learning from results the miss was ${pts(before.mae)} per player, and projections ran ${pts(Math.abs(before.bias))} pts ${before.bias < 0 ? 'high' : 'low'}.`}</Note> : null}
        {scaled.length ? (
          <Note>{`Learned so far: ${scaled.map((p) => `${p} ${Math.round(100 * c.factors[p])}%`).join(' · ')} of the raw projection. Retuned every morning.`}</Note>
        ) : null}
      </Card>
      {weeks.length ? (
        <Card title="Projection miss by week">
          {weeks.map((r, i) => {
            const m = r.as_shown ?? r.calibrated;
            return m ? (
              <Row key={r.week} first={i === 0}>
                <Text style={[ui.value, styles.week]}>Wk {r.week}</Text>
                <View style={ui.flex}>
                  <Text style={ui.value}>{pts(m.mae)} pts per player{r.team_calibrated ? ` · ${pts(r.team_calibrated.mae)} per team` : ''}</Text>
                  <Text style={ui.sub}>{m.bias < 0 ? `ran ${pts(-m.bias)} high` : `ran ${pts(m.bias)} low`}{r.shown ? ` · ${pts(r.shown.mae)} before learning` : ''}</Text>
                </View>
              </Row>
            ) : null;
          })}
        </Card>
      ) : null}
    </>
  );
}

const styles = StyleSheet.create({
  eyebrow: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1.2, marginBottom: 6 },
  lesson: { color: colors.text, fontSize: 14, lineHeight: 21, marginBottom: 4 },
  vs: { flexDirection: 'row', gap: 12, marginTop: 10, marginBottom: 4 },
  big: { color: colors.text, fontSize: 26, fontWeight: '800' },
  week: { width: 48 },
});
