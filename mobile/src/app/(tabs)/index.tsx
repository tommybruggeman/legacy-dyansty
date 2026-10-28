import { router, useFocusEffect } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import { Alert, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { ErrorState, Loading } from '../../components/ScreenState';
import { Bar, Card, Note, Pill, Pos, Row, Screen, Tile, Tiles, firstName, money, ordinal, record, ui } from '../../components/ui';
import { type HomeData, type HomeGame, type RookieDeal, apiSend, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

// Home changes with the calendar: your game (Thu-Mon) or last week's recap
// (Tue-Wed) in season, the bracket in the playoffs, rookie-deal decisions
// until the June 30 rollover, then draft prep. Every version has a Front
// Office card you can type into.
const ROUND = ['', '1st', '2nd', '3rd', '4th', '5th'];
const DAY = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: 'long' });
const SHORT = (iso: string) => new Date(iso.length > 10 ? iso : `${iso}T12:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

export default function Home() {
  const { data, error, loading, reload } = useApi<HomeData>('/home', 0);

  const seen = useRef(false);
  useFocusEffect(useCallback(() => {
    if (seen.current) reload();
    seen.current = true;
  }, [reload]));

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const eyebrow = data.phase === 'decisions' || data.phase === 'draft_prep'
    ? `${data.season + (data.phase === 'draft_prep' ? 0 : 1)} offseason`
    : data.phase === 'playoffs' ? `Playoffs · Week ${data.week}` : `Week ${data.week} · ${DAY(data.today)}`;
  const initials = data.owner_name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();

  return (
    <Screen loading={loading} onRefresh={reload}>
      <View style={styles.top}>
        <View style={ui.flex}>
          <Text style={styles.eyebrow}>{eyebrow}</Text>
          <Text style={styles.hey}>Hey, {firstName(data.owner_name) || 'there'}</Text>
        </View>
        <Pressable onPress={() => router.push('/settings')} style={styles.avatar} hitSlop={8}>
          <Text style={styles.avatarText}>{initials || '•'}</Text>
        </Pressable>
      </View>

      {data.phase === 'game_day' ? <GameDay data={data} /> : null}
      {data.phase === 'recap' ? <Recap data={data} /> : null}
      {data.phase === 'playoffs' ? <Playoffs data={data} /> : null}
      {data.phase === 'decisions' ? <Decisions data={data} onChange={reload} /> : null}
      {data.phase === 'draft_prep' ? <DraftPrep data={data} /> : null}
    </Screen>
  );
}

// ---- pieces ----------------------------------------------------------------

function ScoreCard({ title, week, game, live, foot }: { title: string; week: number; game: HomeGame | null | undefined; live?: boolean; foot?: string }) {
  if (!game?.me) {
    return <Card gold><Text style={styles.cardEyebrow}>{title}</Text><Text style={ui.sub}>No game this week.</Text></Card>;
  }
  const { me, them } = game;
  const ahead = me.score >= (them?.score ?? 0);
  const total = me.score + (them?.score ?? 0);
  const open = game.matchup_id != null ? () => router.push(`/game/${week}/${game.matchup_id}`) : undefined;
  return (
    <Pressable onPress={open}>
      <Card gold>
        <View style={styles.between}>
          <Text style={styles.cardEyebrow}>{title}</Text>
          {live ? <Pill label="LIVE" tone="live" /> : !ahead && total ? <Pill label="LOSS" tone="out" /> : total ? <Pill label="WIN" tone="live" /> : null}
        </View>
        <View style={styles.scoreRow}>
          <Text style={[styles.side, ahead && ui.gold]}>You</Text>
          <Text style={[styles.score, ahead && ui.gold]}>{me.score.toFixed(2)}</Text>
        </View>
        <View style={styles.scoreRow}>
          <Text style={[styles.side, !ahead && ui.gold]}>{them ? firstName(them.name) : 'Bye'}</Text>
          <Text style={[styles.score, !ahead && ui.gold]}>{(them?.score ?? 0).toFixed(2)}</Text>
        </View>
        {total ? <View style={styles.bar}><Bar fraction={me.score / total} /></View> : null}
        {foot ? <Text style={[ui.sub, styles.foot]}>{foot}</Text> : null}
      </Card>
    </Pressable>
  );
}

function StandingTiles({ data }: { data: HomeData }) {
  const m = data.me;
  return (
    <Tiles>
      <Tile label="STANDING" value={ordinal(m.rank)} sub={`${m.standing_points} SP`} />
      <Tile label="RECORD" value={record(m.wins, m.losses, m.ties)} sub={`of ${data.teams_count} teams`} />
      <Tile label="CAP" value={money(m.cap_space)} tone={m.cap_space < 0 ? 'red' : undefined} sub="space" />
    </Tiles>
  );
}

export function FrontOfficeCard({ line, prompts }: { line: string; prompts: string[] }) {
  const [draft, setDraft] = useState('');
  const ask = (q: string) => {
    if (!q.trim()) return;
    setDraft('');
    router.navigate({ pathname: '/front-office', params: { q: q.trim(), at: String(Date.now()) } });
  };
  return (
    <Card gold>
      <View style={styles.between}>
        <Text style={styles.cardEyebrow}>FRONT OFFICE</Text>
        <Pressable onPress={() => router.navigate('/front-office')} hitSlop={8}><Text style={ui.link}>Open ›</Text></Pressable>
      </View>
      {line ? <Text style={styles.foLine}>{line}</Text> : null}
      <View style={styles.chips}>
        {prompts.slice(0, 2).map((p) => (
          <Pressable key={p} onPress={() => ask(p)} style={styles.chip}><Text style={styles.chipText}>{p}</Text></Pressable>
        ))}
      </View>
      <View style={styles.askRow}>
        <TextInput
          value={draft}
          onChangeText={setDraft}
          placeholder="Ask your assistant GM…"
          placeholderTextColor={colors.muted}
          style={styles.ask}
          returnKeyType="send"
          onSubmitEditing={() => ask(draft)}
        />
        <Pressable onPress={() => ask(draft)} hitSlop={8}><Text style={styles.askGo}>↑</Text></Pressable>
      </View>
    </Card>
  );
}

// ---- phases ----------------------------------------------------------------

function GameDay({ data }: { data: HomeData }) {
  const g = data.game;
  return (
    <>
      <ScoreCard title={`YOUR MATCHUP · WEEK ${data.week}`} week={data.week} game={g} live={!!g && !g.is_final && !!g.me?.score} />
      <StandingTiles data={data} />
      <FrontOfficeCard {...data.front_office} />
    </>
  );
}

function Recap({ data }: { data: HomeData }) {
  const r = data.recap;
  const game = r?.game;
  const top5 = game?.me?.top5;
  const next = data.game?.them;
  return (
    <>
      <ScoreCard
        title={`WEEK ${r?.week ?? data.week - 1} RECAP`}
        week={r?.week ?? data.week - 1}
        game={game ?? null}
        foot={game?.me ? `${ordinal(game.me.rank)} highest score of ${data.teams_count}. ${top5 ? 'Made the Top 5' : `Top 5 cut was ${r?.summary?.cut.score.toFixed(2) ?? '—'}`}.` : undefined}
      />
      {next ? (
        <Card>
          <View style={styles.between}>
            <Text style={styles.cardEyebrow}>UP NEXT · WEEK {data.week}</Text>
          </View>
          <Text style={ui.name}>{next.name}</Text>
        </Card>
      ) : null}
      <FrontOfficeCard {...data.front_office} />
      {data.picture?.length ? (
        <Card
          title="Playoff picture"
          right={<Pressable onPress={() => router.navigate('/league')} hitSlop={8}><Text style={ui.link}>Standings ›</Text></Pressable>}
        >
          <Text style={ui.sub}>Top {data.playoff_teams} make it{data.weeks_left ? ` · ${data.weeks_left} weeks left` : ''}</Text>
          {data.picture.map((p, i) => (
            <View key={p.rank}>
              {i > 0 && data.picture![i - 1].in && !p.in ? <View style={styles.cut} /> : null}
              <Row first={i === 0} onPress={p.team_id ? () => router.push(`/team/${p.team_id}`) : undefined}>
                <Text style={[styles.rank, p.is_me && ui.gold]}>{p.rank}</Text>
                <Text style={[ui.value, ui.flex, p.is_me && ui.gold]}>{p.is_me ? 'You' : p.name}</Text>
                <Text style={ui.sub}>{p.standing_points} SP</Text>
              </Row>
            </View>
          ))}
        </Card>
      ) : null}
    </>
  );
}

function Playoffs({ data }: { data: HomeData }) {
  const g = data.game;
  return (
    <>
      <ScoreCard title={`PLAYOFFS · WEEK ${data.week}`} week={data.week} game={g} live={!!g && !g.is_final && !!g.me?.score} />
      {data.bracket?.length ? (
        <Card title="Playoff bracket" right={`top ${data.playoff_teams}`}>
          <View style={styles.bracket}>
            {data.bracket.map((round) => (
              <View key={round.round} style={styles.roundCol}>
                <Text style={styles.roundLabel}>{round.label.toUpperCase()} · WK {round.week}</Text>
                {round.games.map((game, i) => (
                  <View key={i} style={styles.match}>
                    {[game.a, game.b].map((side, j) => (
                      <View key={j} style={styles.matchRow}>
                        <Text style={[styles.matchName, side && game.winner === side.roster_id && ui.gold]} numberOfLines={1}>
                          {side ? firstName(side.name) : 'TBD'}
                        </Text>
                        <Text style={styles.matchScore}>{side?.score != null && side.score > 0 ? side.score.toFixed(1) : ''}</Text>
                      </View>
                    ))}
                  </View>
                ))}
              </View>
            ))}
          </View>
        </Card>
      ) : null}
      <FrontOfficeCard {...data.front_office} />
    </>
  );
}

function Decisions({ data, onChange }: { data: HomeData; onChange: () => void }) {
  const d = data.decisions!;
  const [busy, setBusy] = useState<string | null>(null);
  const daysLeft = Math.max(0, Math.round((new Date(`${d.deadline.slice(0, 10)}T12:00:00`).getTime() - new Date(`${data.today}T12:00:00`).getTime()) / 86_400_000));
  const decided = d.players.filter((p) => p.choice).length;
  const walking = d.players.filter((p) => p.choice === 'walk').reduce((s, p) => s + p.salary, 0);
  const next = data.me.cap_by_year?.[1];

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

  return (
    <>
      <Card gold>
        <View style={styles.between}>
          <Text style={styles.cardEyebrow}>ROLLOVER DECISIONS</Text>
          <Text style={styles.days}>{daysLeft} DAYS LEFT</Text>
        </View>
        <Text style={styles.big}>Keep or let walk?</Text>
        <Text style={ui.sub}>
          Decide by {SHORT(d.deadline)}. Anything you skip walks to free agency.
          {!d.window_open ? ' Choices open when the commissioner starts the rollover.' : ''}
        </Text>
      </Card>

      <Card title="Your rookie deals ending" right={d.players.length ? `${decided} of ${d.players.length} decided` : undefined}>
        {d.players.length === 0 ? <Note>No rookie deals end this year.</Note> : null}
        {d.players.map((p, i) => (
          <View key={p.player_id} style={[styles.deal, i > 0 && styles.dealLine]}>
            <View style={styles.dealTop}>
              <Pos label={p.pos} />
              <View style={ui.flex}>
                <Text style={ui.name}>{p.name}</Text>
                <Text style={ui.sub}>Rookie deal {money(p.salary)}{p.ppg != null ? ` · ${p.ppg.toFixed(1)} PPG` : ''}</Text>
              </View>
            </View>
            <View style={styles.choices}>
              {(['keep', 'walk'] as const).map((c) => (
                <Pressable
                  key={c}
                  disabled={!p.open || busy === p.player_id}
                  onPress={() => choose(p, c)}
                  style={[styles.choice, p.choice === c && styles.choiceOn, !p.open && !p.choice && styles.choiceOff]}
                >
                  <Text style={[styles.choiceText, p.choice === c && styles.choiceTextOn]}>{c === 'keep' ? 'Keep' : 'Let walk'}</Text>
                </Pressable>
              ))}
            </View>
          </View>
        ))}
      </Card>

      {next ? (
        <Card
          title="Cap after your choices"
          right={<Pressable onPress={() => router.push('/league-cap')} hitSlop={8}><Text style={ui.link}>League cap ›</Text></Pressable>}
        >
          <Tiles>
            <Tile label={`${next.season} SPACE`} value={money(next.space)} sub="with your deals now" />
            <Tile label="WALKING" value={money(walking)} sub="freed up" />
          </Tiles>
        </Card>
      ) : null}

      <FrontOfficeCard line={data.front_office.line} prompts={['Who should I keep?', 'Plan my draft']} />
    </>
  );
}

function DraftPrep({ data }: { data: HomeData }) {
  const years = data.me.cap_by_year ?? [];
  return (
    <>
      <FrontOfficeCard line={data.front_office.line} prompts={['Plan my draft', 'Who should I target in free agency?']} />
      <Card
        title="Your cap"
        right={<Pressable onPress={() => router.push('/league-cap')} hitSlop={8}><Text style={ui.link}>League cap ›</Text></Pressable>}
      >
        <Tiles>
          {years.slice(0, 2).map((y) => <Tile key={y.season} label={String(y.season)} value={money(y.space)} tone={y.space < 0 ? 'red' : undefined} sub="space" />)}
          <Tile label="ROSTER" value={`${data.roster_count ?? 0}/${data.roster_max ?? 22}`} sub={`${Math.max(0, (data.roster_max ?? 22) - (data.roster_count ?? 0))} open`} />
        </Tiles>
      </Card>
      <Card title="Draft prep" right={data.picks?.year ? `${data.picks.year} picks` : undefined}>
        {data.picks?.picks.length ? data.picks.picks.map((p, i) => (
          <Row key={`${p.round}-${p.original_owner}`} first={i === 0}>
            <Pos label={ROUND[p.round] ?? `R${p.round}`} />
            <Text style={[ui.value, ui.flex]}>Round {p.round}{p.own ? '' : ` (from ${firstName(p.original_owner)})`}</Text>
          </Row>
        )) : <Note>No picks in the next draft.</Note>}
        <View style={styles.choices}>
          <Pressable style={[styles.choice, styles.choiceOn]} onPress={() => router.navigate('/market')}>
            <Text style={[styles.choiceText, styles.choiceTextOn]}>Free agents ›</Text>
          </Pressable>
        </View>
      </Card>
      {data.who_has_money?.length ? (
        <Card title="Who has money" right={`${data.season} cap space`}>
          {data.who_has_money.map((m, i) => (
            <Row key={m.name} first={i === 0} onPress={m.team_id ? () => router.push(`/team/${m.team_id}`) : undefined}>
              <Text style={styles.rank}>{i + 1}</Text>
              <Text style={[ui.value, ui.flex, m.is_me && ui.gold]}>{m.is_me ? 'You' : m.name}</Text>
              <Text style={ui.value}>{money(m.space)}</Text>
            </Row>
          ))}
        </Card>
      ) : null}
      {data.coming_off?.players.length ? (
        <Card title="Coming off your books" right={<Text style={[ui.value, ui.gold]}>{money(data.coming_off.total)}</Text>}>
          <Text style={ui.sub}>
            {data.coming_off.players.slice(0, 2).map((p) => `${p.name} ${money(p.salary)}`).join(' · ')}
            {data.coming_off.players.length > 2 ? ` · +${data.coming_off.players.length - 2} more` : ''} hit free agency
          </Text>
        </Card>
      ) : null}
    </>
  );
}

const styles = StyleSheet.create({
  top: { flexDirection: 'row', alignItems: 'center', marginBottom: 12 },
  eyebrow: { color: colors.muted, fontSize: 13 },
  hey: { color: colors.text, fontSize: 28, fontWeight: '800' },
  avatar: { width: 36, height: 36, borderRadius: 18, borderWidth: 1, borderColor: colors.line, alignItems: 'center', justifyContent: 'center' },
  avatarText: { color: colors.gold, fontWeight: '800', fontSize: 13 },
  between: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  cardEyebrow: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1.2 },
  scoreRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 8 },
  side: { color: colors.text, fontSize: 20, fontWeight: '800' },
  score: { color: colors.text, fontSize: 28, fontWeight: '800' },
  bar: { marginTop: 12 },
  foot: { marginTop: 8 },
  foLine: { color: colors.text, fontSize: 15, lineHeight: 21, marginTop: 6 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 10 },
  chip: { borderWidth: 1, borderColor: colors.line, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 6 },
  chipText: { color: colors.text, fontSize: 12, fontWeight: '600' },
  askRow: { flexDirection: 'row', alignItems: 'center', marginTop: 10, backgroundColor: colors.card, borderRadius: 20, paddingHorizontal: 14 },
  ask: { flex: 1, color: colors.text, fontSize: 14, paddingVertical: 10 },
  askGo: { color: colors.gold, fontSize: 18, fontWeight: '800' },
  rank: { color: colors.muted, width: 18, fontSize: 14 },
  cut: { borderTopWidth: 1, borderStyle: 'dashed', borderColor: colors.gold, marginVertical: 2 },
  bracket: { flexDirection: 'row', gap: 10, marginTop: 6 },
  roundCol: { flex: 1, gap: 8, justifyContent: 'center' },
  roundLabel: { color: colors.muted, fontSize: 10, fontWeight: '800', letterSpacing: 1 },
  match: { borderWidth: 1, borderColor: colors.line, borderRadius: 10, padding: 8, gap: 2 },
  matchRow: { flexDirection: 'row', justifyContent: 'space-between' },
  matchName: { color: colors.text, fontSize: 14, fontWeight: '700', flex: 1 },
  matchScore: { color: colors.text, fontSize: 14 },
  days: { color: colors.gold, fontSize: 11, fontWeight: '800' },
  big: { color: colors.text, fontSize: 22, fontWeight: '800', marginVertical: 6 },
  deal: { paddingVertical: 10 },
  dealLine: { borderTopWidth: 1, borderTopColor: colors.card },
  dealTop: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  choices: { flexDirection: 'row', gap: 8, marginTop: 10 },
  choice: { flex: 1, borderWidth: 1, borderColor: colors.line, borderRadius: 12, paddingVertical: 9, alignItems: 'center' },
  choiceOn: { borderColor: colors.gold },
  choiceOff: { opacity: 0.45 },
  choiceText: { color: colors.text, fontWeight: '700', fontSize: 13 },
  choiceTextOn: { color: colors.gold },
});
