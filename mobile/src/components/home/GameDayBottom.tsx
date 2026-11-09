import { router } from 'expo-router';
import { useState } from 'react';
import { LinearGradient } from 'expo-linear-gradient';
import { Image, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { type HomeBottom, type Riser, type ShotgunRow } from '../../lib/api';
import { colors } from '../../lib/theme';
import { Avatar } from '../Pictures';
import { PlayerFace } from '../PlayerFace';
import { Card, firstName, ui } from '../ui';

// The bottom of game-day Home, picked by the server from the clock:
// Highest projected Thu-Sat, the week's scoreboard on Sunday, then Shotgun watch
// (last place shotguns a beer) from midway through the late games to Monday.

const pts = (n: number) => n.toFixed(1);
const POSITIONS = ['Total', 'QB', 'RB', 'WR', 'TE'] as const;
const RED = '#E08A8A';
const SIREN = '#E05555';

export function GameDayBottom({ bottom, week }: { bottom: HomeBottom; week: number }) {
  if (bottom.mode === 'risers') return bottom.risers.length ? <Risers rows={bottom.risers} week={week} /> : null;
  if (bottom.mode === 'scoreboard') return <Scoreboard games={bottom.scoreboard} week={week} />;
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

// ---- Highest projected -------------------------------------------------------
// The week's top projected players, Total or by position (5 each). Big number = this week's projection.

function Risers({ rows, week }: { rows: Riser[]; week: number }) {
  const [pos, setPos] = useState<(typeof POSITIONS)[number]>('Total');
  const shown = rows.filter((r) => pos === 'Total' || r.pos === pos).slice(0, 5);
  return (
    <Card>
      <Eyebrow title={`HIGHEST PROJECTED · WEEK ${week}`} />
      <View style={styles.chips}>
        {POSITIONS.map((p) => (
          <Pressable key={p} onPress={() => setPos(p)} style={[styles.chip, p === pos && styles.chipOn]} hitSlop={4}>
            <Text style={[styles.chipText, p === pos && styles.chipTextOn]}>{p}</Text>
          </Pressable>
        ))}
      </View>
      {shown.length === 0 ? <Text style={[ui.sub, styles.gap]}>No {pos} projections yet.</Text> : null}
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
              {r.avg != null ? <>AVG <Text style={styles.statVal}>{pts(r.avg)}</Text>   </> : 'New starter   '}
              {r.last != null ? <>LAST <Text style={styles.statVal}>{pts(r.last)}</Text></> : null}
            </Text>
          </View>
          <View style={styles.projBox}><Text style={styles.projBig}>{pts(r.proj)}</Text><Text style={styles.projUnit}>PROJ</Text></View>
        </Pressable>
      ))}
    </Card>
  );
}

// ---- Sunday scoreboard -------------------------------------------------------

export function Scoreboard({ games, week }: { games: Extract<HomeBottom, { mode: 'scoreboard' }>['scoreboard']; week: number }) {
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
              {left ? (
                <Text style={styles.gameProj} numberOfLines={1}>
                  <Text style={styles.liveDot}>● </Text>PROJ {g.teams.map((t) => pts(t.projected ?? t.score)).join(' – ')}
                </Text>
              ) : (
                <Text style={[styles.gameFoot, styles.dim]}>FINAL</Text>
              )}
            </Pressable>
          );
        })}
      </ScrollView>
    </Card>
  );
}

// ---- Shotgun watch -----------------------------------------------------------

type Shot = NonNullable<Extract<HomeBottom, { mode: 'shotgun' }>['shotgun']>;

const who = (r: ShotgunRow) => (r.is_me ? 'You' : firstName(r.name));

// A frothy beer mug drawn for this card (assets/beer-mug.png).
const BEER_MUG = require('../../../assets/beer-mug.png');

function Shotgun({ data }: { data: Shot }) {
  const { last, locked } = data;
  if (locked) {
    return (
      <Card style={styles.shotCard}>
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
  return (
    <Card style={[styles.shotCard, styles.siren]}>
      <LinearGradient colors={['rgba(150,30,34,0.55)', 'rgba(150,30,34,0)']} style={StyleSheet.absoluteFill} end={{ x: 0.5, y: 0.6 }} pointerEvents="none" />
      <Text style={styles.sirenTitle}>SHOTGUN WATCH</Text>
      <View style={styles.sirenHero}>
        <View style={styles.sirenRing}><Avatar uri={last.avatar} name={last.name} size={66} me={last.is_me} /></View>
        <View style={ui.flex}>
          <Text style={styles.sirenKicker}>PROJECTED LAST</Text>
          <Text style={styles.sirenName} numberOfLines={1}>{who(last)}</Text>
          <View style={styles.sirenStats}>
            <Text style={styles.sirenScore}>{pts(last.score)}</Text>
            {last.frozen ? (
              <Text style={styles.sirenFinal}>FINAL</Text>
            ) : (
              <>
                <Text style={styles.sirenSub}>proj {pts(last.projected)}</Text>
                <Text style={styles.sirenLeft}>{last.left} left</Text>
              </>
            )}
          </View>
        </View>
        <Image source={BEER_MUG} style={styles.sirenMug} />
      </View>
      <Zone rows={data.danger} safe={data.safe} />
    </Card>
  );
}

function Zone({ title, rows, safe = [], muted }: { title?: string; rows: ShotgunRow[]; safe?: ShotgunRow[]; muted?: boolean }) {
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
      {title ? <Text style={[styles.zone, muted && styles.dim]}>{title}</Text> : null}
      <View style={[styles.zHead, !title && styles.zHeadTop]}>
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
  projBox: { alignItems: 'flex-end' },
  projBig: { color: colors.gold, fontSize: 19, fontWeight: '800' },
  projUnit: { color: colors.muted, fontSize: 9, fontWeight: '800', letterSpacing: 1 },
  // scoreboard
  strip: { marginTop: 10, marginRight: -14 },
  stripIn: { gap: 8, paddingRight: 14 },
  chipGame: { width: 128, backgroundColor: colors.row, borderRadius: 12, paddingVertical: 8, paddingHorizontal: 10, borderWidth: 1, borderColor: '#23362C' },
  chipGameMine: { borderColor: colors.gold },
  gameRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  gameName: { color: colors.text, fontSize: 12, fontWeight: '700', flex: 1 },
  gameScore: { color: colors.text, fontSize: 13, fontWeight: '800' },
  gameFoot: { color: colors.live, fontSize: 9, fontWeight: '800', letterSpacing: 0.5, marginTop: 6 },
  // live tiles: projected finals, smaller and fainter than FINAL so the scores stay the focus
  gameProj: { color: colors.muted, opacity: 0.75, fontSize: 7, fontWeight: '700', letterSpacing: 0.4, marginTop: 7 },
  liveDot: { color: colors.live, opacity: 1 },
  // shotgun
  shotCard: { borderWidth: 1, borderColor: RED },
  siren: { borderColor: SIREN, overflow: 'hidden' },
  sirenTitle: { color: '#FF8A80', fontSize: 11, fontWeight: '900', letterSpacing: 2.5 },
  sirenHero: { flexDirection: 'row', alignItems: 'center', gap: 14, marginTop: 12 },
  sirenRing: { borderWidth: 3, borderColor: SIREN, borderRadius: 40, padding: 1 },
  sirenKicker: { color: '#FF8A80', fontSize: 10, fontWeight: '900', letterSpacing: 2.5 },
  sirenMug: { width: 52, height: 57, transform: [{ rotate: '-8deg' }] },
  sirenName: { color: colors.text, fontSize: 28, fontWeight: '900', marginTop: 2 },
  sirenStats: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 4 },
  sirenScore: { color: colors.text, fontSize: 15, fontWeight: '800' },
  sirenSub: { color: colors.muted, fontSize: 12, fontWeight: '600' },
  sirenLeft: { color: colors.live, fontSize: 12, fontWeight: '700' },
  sirenFinal: {
    color: colors.text, backgroundColor: 'rgba(245,235,215,0.12)', fontSize: 9, fontWeight: '900', letterSpacing: 1,
    borderRadius: 7, overflow: 'hidden', paddingHorizontal: 7, paddingVertical: 3,
  },
  zone: { color: '#E0A95A', fontSize: 11, fontWeight: '800', letterSpacing: 0.6, marginTop: 14, marginBottom: 2 },
  zHead: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingLeft: 30, marginTop: 4 },
  zHeadTop: { marginTop: 14 },
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
