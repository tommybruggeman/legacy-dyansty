import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { type HomeBottom, type Riser, type ShotgunRow } from '../../lib/api';
import { colors } from '../../lib/theme';
import { Avatar } from '../Pictures';
import { PlayerFace } from '../PlayerFace';
import { Card, Pill, firstName, ui } from '../ui';

// The bottom of game-day Home, picked by the server from the clock:
// GM's Risers Thu-Sat, the week's scoreboard on Sunday, then Shotgun watch
// (last place shotguns a beer) from midway through the late games to Monday.

const pts = (n: number) => n.toFixed(1);
const POSITIONS = ['All', 'QB', 'RB', 'WR', 'TE'] as const;
const RED = '#E08A8A';

export function GameDayBottom({ bottom, week }: { bottom: HomeBottom; week: number }) {
  if (bottom.mode === 'risers') return bottom.risers.length ? <Risers rows={bottom.risers} week={week} /> : null;
  if (bottom.mode === 'scoreboard') return <Scoreboard games={bottom.scoreboard} race={bottom.race} week={week} />;
  return bottom.shotgun ? <Shotgun data={bottom.shotgun} /> : null;
}

function Eyebrow({ title, right, tone = colors.gold }: { title: string; right?: React.ReactNode; tone?: string }) {
  return (
    <View style={styles.between}>
      <Text style={[styles.eyebrow, { color: tone }]}>{title}</Text>
      {right ?? null}
    </View>
  );
}

// ---- GM's Risers -------------------------------------------------------------

function Risers({ rows, week }: { rows: Riser[]; week: number }) {
  const [pos, setPos] = useState<(typeof POSITIONS)[number]>('All');
  const shown = rows.filter((r) => pos === 'All' || r.pos === pos).slice(0, 5);
  return (
    <Card>
      <Eyebrow title={`GM'S RISERS · WEEK ${week}`} />
      <View style={styles.chips}>
        {POSITIONS.map((p) => (
          <Pressable key={p} onPress={() => setPos(p)} style={[styles.chip, p === pos && styles.chipOn]} hitSlop={4}>
            <Text style={[styles.chipText, p === pos && styles.chipTextOn]}>{p}</Text>
          </Pressable>
        ))}
      </View>
      {shown.length === 0 ? <Text style={[ui.sub, styles.gap]}>No {pos} risers this week.</Text> : null}
      {shown.map((r, i) => (
        <Pressable key={r.player_id} style={[styles.riser, i > 0 && styles.line]} onPress={() => router.push(`/player/${r.player_id}`)}>
          <Text style={styles.rank}>{i + 1}</Text>
          <PlayerFace name={r.name} team={r.team} pos={r.pos} size={40} hurt={!!r.injury} />
          <View style={ui.flex}>
            <Text style={[styles.name, r.is_mine && ui.gold]} numberOfLines={1}>{r.name}</Text>
            <Text style={styles.sub} numberOfLines={1}>
              {r.pos} · {r.team || 'FA'} · <Text style={r.is_mine ? ui.gold : r.owner ? undefined : styles.free}>{r.is_mine ? 'You' : r.owner ? firstName(r.owner) : 'Free agent'}</Text>
            </Text>
            <Text style={styles.stats}>
              AVG <Text style={styles.statVal}>{pts(r.avg)}</Text>   PROJ <Text style={styles.statVal}>{pts(r.proj)}</Text>
              {r.last != null ? <>   LAST <Text style={styles.statVal}>{pts(r.last)}</Text></> : null}
            </Text>
          </View>
          <Text style={styles.rise}>+{pts(r.rise)}</Text>
        </Pressable>
      ))}
    </Card>
  );
}

// ---- Sunday scoreboard -------------------------------------------------------

function Scoreboard({ games, race, week }: { games: Extract<HomeBottom, { mode: 'scoreboard' }>['scoreboard']; race: Extract<HomeBottom, { mode: 'scoreboard' }>['race']; week: number }) {
  return (
    <Card>
      <Eyebrow
        title={`WEEK ${week} SCOREBOARD`}
        right={<Pressable onPress={() => router.navigate('/league')} hitSlop={8}><Text style={ui.link}>All ›</Text></Pressable>}
      />
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.strip} contentContainerStyle={styles.stripIn}>
        {games.map((g) => {
          const left = g.teams.reduce((n, t) => n + t.left, 0);
          return (
            <Pressable key={g.matchup_id} style={[styles.chipGame, g.mine && styles.chipGameMine]} onPress={() => router.push(`/game/${week}/${g.matchup_id}`)}>
              {g.teams.map((t, i) => {
                const lead = t.score >= Math.max(...g.teams.map((x) => x.score));
                return (
                  <View key={t.roster_id} style={[styles.gameRow, i > 0 && styles.gap4]}>
                    <Avatar uri={t.avatar} name={t.name} size={18} me={t.is_me} />
                    <Text style={[styles.gameName, !lead && styles.dim, t.is_me && ui.gold]} numberOfLines={1}>{t.is_me ? 'You' : firstName(t.name)}</Text>
                    <Text style={[styles.gameScore, !lead && styles.dim]}>{pts(t.score)}</Text>
                  </View>
                );
              })}
              <Text style={[styles.gameFoot, !left && styles.dim]}>{left ? `● ${g.teams.map((t) => t.left).join(' · ')} LEFT` : 'FINAL'}</Text>
            </Pressable>
          );
        })}
      </ScrollView>
      {race.length ? (
        <View style={styles.top5}>
          <Text style={styles.top5Label}>TOP 5</Text>
          {race.map((r, i) => (
            <View key={r.roster_id} style={[styles.top5Face, i >= 5 && styles.faded, i === 5 && styles.top5Cut]}>
              <Avatar uri={r.avatar} name={r.name} size={22} me={r.is_me} />
            </View>
          ))}
        </View>
      ) : null}
    </Card>
  );
}

// ---- Shotgun watch -----------------------------------------------------------

type Shot = NonNullable<Extract<HomeBottom, { mode: 'shotgun' }>['shotgun']>;

const who = (r: ShotgunRow) => (r.is_me ? 'You' : firstName(r.name));

function Shotgun({ data }: { data: Shot }) {
  const { last, locked } = data;
  if (locked) {
    return (
      <Card style={styles.shotCard}>
        <Eyebrow title="SHOTGUN WATCH · LOCKED" tone={RED} right={<Pill label="FINAL" tone="out" />} />
        <View style={styles.lock}>
          <Avatar uri={last.avatar} name={last.name} size={64} me={last.is_me} />
          <Text style={styles.lockName}>{last.is_me ? "You're shotgunning" : `${firstName(last.name)} is shotgunning`}</Text>
          <Text style={styles.sub}>{pts(last.score)} pts{data.danger[0] ? ` · ${pts(data.danger[0].score - last.score)} behind ${who(data.danger[0])}` : ''}</Text>
          <Text style={styles.stamp}>LOCKED IN</Text>
        </View>
        {data.danger.length ? <Zone title="CLOSEST ESCAPES" rows={data.danger} muted /> : null}
      </Card>
    );
  }
  const line = data.needs
    ? `${last.is_me ? 'You need' : 'Needs'} ${pts(data.needs.points)} to pass ${firstName(data.needs.to_pass)}`
    : 'Lowest projected score in Legacy';
  return (
    <Card style={styles.shotCard}>
      <Eyebrow title="SHOTGUN WATCH" tone={RED} right={<Pill label={`WITHIN ${data.window} PTS`} tone="out" />} />
      <View style={styles.victim}>
        <Avatar uri={last.avatar} name={last.name} size={46} me={last.is_me} />
        <View style={ui.flex}>
          <Text style={styles.victimName}>{who(last)}</Text>
          <Text style={styles.sub}>Projected {pts(last.projected)} · now {pts(last.score)} · {last.left ? `${last.left} left` : 'done'}</Text>
          <Text style={styles.victimLine}>{line}</Text>
        </View>
      </View>
      <Zone title={`IN DANGER · WITHIN ${data.window} PROJECTED PTS`} rows={data.danger} safe={data.safe} />
    </Card>
  );
}

function Zone({ title, rows, safe = [], muted }: { title: string; rows: ShotgunRow[]; safe?: ShotgunRow[]; muted?: boolean }) {
  const Row = ({ r, out }: { r: ShotgunRow; out?: boolean }) => (
    <View style={[styles.zRow, out && styles.faded]}>
      <Avatar uri={r.avatar} name={r.name} size={22} me={r.is_me} />
      <Text style={[styles.zName, r.is_me && ui.gold]} numberOfLines={1}>{who(r)}</Text>
      <Text style={[styles.zNum, r.is_me && ui.gold]}>{pts(r.projected)}</Text>
      <Text style={[styles.zNum, styles.dim]}>{pts(r.score)}</Text>
      <View style={styles.zLeft}>
        {r.frozen ? <Text style={styles.frozen}>FROZEN</Text> : <Text style={styles.leftText}>{r.playing ? '● ' : ''}{r.left} left</Text>}
      </View>
    </View>
  );
  return (
    <>
      <Text style={[styles.zone, muted && styles.dim]}>{title}</Text>
      <View style={styles.zHead}>
        <Text style={[styles.zName, styles.zHeadText, styles.left]}>OWNER</Text>
        <Text style={[styles.zNum, styles.zHeadText]}>PROJ</Text>
        <Text style={[styles.zNum, styles.zHeadText]}>NOW</Text>
        <Text style={[styles.zLeft, styles.zHeadText]}>LEFT</Text>
      </View>
      {rows.map((r) => <Row key={r.roster_id} r={r} />)}
      {rows.length === 0 && !muted ? <Text style={[ui.sub, styles.gap]}>Nobody else is close.</Text> : null}
      {safe.length ? <View style={styles.cut} /> : null}
      {safe.map((r) => <Row key={r.roster_id} r={r} out />)}
    </>
  );
}

const styles = StyleSheet.create({
  between: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  eyebrow: { fontSize: 11, fontWeight: '800', letterSpacing: 1.2, flexShrink: 1 },
  gap: { marginTop: 8 },
  gap4: { marginTop: 4 },
  line: { borderTopWidth: 1, borderTopColor: colors.row },
  faded: { opacity: 0.4 },
  dim: { color: colors.muted },
  left: { textAlign: 'left' },
  sub: { color: colors.muted, fontSize: 11, fontWeight: '600', marginTop: 2 },
  name: { color: colors.text, fontSize: 14, fontWeight: '800' },
  // risers
  chips: { flexDirection: 'row', gap: 6, marginTop: 10, marginBottom: 4 },
  chip: { borderWidth: 1, borderColor: colors.line, borderRadius: 14, paddingVertical: 5, paddingHorizontal: 12 },
  chipOn: { backgroundColor: colors.gold, borderColor: colors.gold },
  chipText: { color: colors.muted, fontSize: 12, fontWeight: '700' },
  chipTextOn: { color: colors.bg },
  riser: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 9 },
  rank: { color: colors.muted, width: 14, fontSize: 13, fontWeight: '700' },
  free: { color: colors.live },
  stats: { color: colors.muted, fontSize: 10, fontWeight: '700', marginTop: 3 },
  statVal: { color: colors.text },
  rise: { color: colors.live, fontSize: 19, fontWeight: '800' },
  // scoreboard
  strip: { marginTop: 10, marginRight: -14 },
  stripIn: { gap: 8, paddingRight: 14 },
  chipGame: { width: 128, backgroundColor: colors.row, borderRadius: 12, paddingVertical: 8, paddingHorizontal: 10, borderWidth: 1, borderColor: '#23362C' },
  chipGameMine: { borderColor: colors.gold },
  gameRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  gameName: { color: colors.text, fontSize: 12, fontWeight: '700', flex: 1 },
  gameScore: { color: colors.text, fontSize: 13, fontWeight: '800' },
  gameFoot: { color: colors.live, fontSize: 9, fontWeight: '800', letterSpacing: 0.5, marginTop: 6 },
  top5: { flexDirection: 'row', alignItems: 'center', gap: 5, marginTop: 12, flexWrap: 'wrap' },
  top5Label: { color: colors.muted, fontSize: 10, fontWeight: '800', letterSpacing: 0.6, marginRight: 2 },
  top5Face: {},
  top5Cut: { borderLeftWidth: 1, borderLeftColor: '#E0A95A', borderStyle: 'dashed', paddingLeft: 6, marginLeft: 2 },
  // shotgun
  shotCard: { borderWidth: 1, borderColor: RED },
  victim: {
    flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 12, padding: 12, borderRadius: 14,
    backgroundColor: 'rgba(169,74,74,0.16)', borderWidth: 1, borderColor: 'rgba(224,138,138,0.4)',
  },
  victimName: { color: colors.text, fontSize: 20, fontWeight: '800' },
  victimLine: { color: RED, fontSize: 11, fontWeight: '700', marginTop: 2 },
  zone: { color: '#E0A95A', fontSize: 11, fontWeight: '800', letterSpacing: 0.6, marginTop: 14, marginBottom: 2 },
  zHead: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingLeft: 30, marginTop: 4 },
  zHeadText: { color: colors.muted, fontSize: 9, fontWeight: '800', letterSpacing: 0.8, textAlign: 'right' },
  zRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 7, borderTopWidth: 1, borderTopColor: colors.row },
  zName: { flex: 1, color: colors.text, fontSize: 13, fontWeight: '700' },
  zNum: { width: 46, textAlign: 'right', color: colors.text, fontSize: 13, fontWeight: '700' },
  zLeft: { width: 58, alignItems: 'flex-end' },
  leftText: { color: colors.live, fontSize: 11, fontWeight: '700', textAlign: 'right' },
  frozen: { color: '#7FB2E0', backgroundColor: 'rgba(127,178,224,0.15)', fontSize: 9, fontWeight: '800', borderRadius: 6, overflow: 'hidden', paddingHorizontal: 5, paddingVertical: 2 },
  cut: { borderTopWidth: 1, borderTopColor: RED, borderStyle: 'dashed', marginVertical: 4 },
  lock: { alignItems: 'center', paddingTop: 14, paddingBottom: 4, gap: 6 },
  lockName: { color: colors.text, fontSize: 24, fontWeight: '900', marginTop: 4 },
  stamp: {
    color: RED, borderWidth: 2, borderColor: RED, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 4, fontWeight: '900',
    letterSpacing: 2, fontSize: 13, marginTop: 6, transform: [{ rotate: '-4deg' }],
  },
});
