import { useRef, useState } from 'react';
import { Linking, StyleSheet, Text, View } from 'react-native';
import { Pressable } from '../components/Pressable';

import { Move, ProspectPhoto, editionDay } from '../components/home/ProspectCard';
import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Screen } from '../components/ui';
import { type Prospect, type ProspectPage, apiSend, useApi } from '../lib/api';
import { colors } from '../lib/theme';

const TABS = ['All players', 'QB', 'RB', 'WR', 'TE'] as const;
type Tab = (typeof TABS)[number];

// Prospect Power Rankings: the GM's weekly article on the next rookie class (written Tuesdays). A big board up
// top with position tabs, then a writeup per player with his photo, role, stats from our college feed, where the
// mocks and boards have him, and a star to save him (stars are per owner and shared with the website).
export default function Prospects() {
  const { data, error, loading, reload } = useApi<ProspectPage>('/prospects/rankings');
  const [tab, setTab] = useState<Tab>('All players');
  const [starred, setStarred] = useState<Set<string> | null>(null);
  const [jump, setJump] = useState<{ y: number; n: number } | null>(null);
  const tops = useRef<Record<string, number>>({});
  const listTop = useRef(0);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  const ed = data?.edition;
  if (!ed) {
    return (
      <Screen>
        <Back label="Home" />
        <Text style={styles.title}>Prospect Power Rankings</Text>
        <Text style={styles.dek}>The first board comes out on Tuesday.</Text>
      </Screen>
    );
  }
  const stars = starred ?? new Set(data!.starred);
  const toggle = (p: Prospect) => {
    const on = !stars.has(p.athlete_id);
    const next = new Set(stars);
    if (on) next.add(p.athlete_id); else next.delete(p.athlete_id);
    setStarred(next);
    apiSend<{ starred: string[] }>('POST', '/prospects/star', { athlete_id: p.athlete_id, on })
      .then((r) => setStarred(new Set(r.starred)))
      .catch(() => setStarred(stars));
  };
  const posRank: Record<string, number> = {};
  const rows = ed.players
    .map((p) => { posRank[p.pos] = (posRank[p.pos] || 0) + 1; return { p, pr: posRank[p.pos] }; })
    .filter(({ p }) => tab === 'All players' || p.pos === tab);
  const shownRank = (p: Prospect, pr: number) => (tab === 'All players' ? p.rank : pr);

  return (
    <Screen jumpTo={jump}>
      <Back label="Home" />
      <View style={styles.hero}>
        <View style={styles.heroArt}>
          {ed.players.slice(0, 3).map((p, i) => (
            <View key={p.athlete_id} style={i === 1 ? styles.heroMid : null}><ProspectPhoto p={p} size={i === 1 ? 92 : 80} square /></View>
          ))}
        </View>
        <Text style={styles.kicker}>PROSPECT POWER RANKINGS · NO. {ed.number}</Text>
      </View>
      <Text style={styles.title}>{ed.headline}</Text>
      {ed.dek ? <Text style={styles.dek}>{ed.dek}</Text> : null}
      <Text style={styles.by}>By Legacy GM · {editionDay(ed.date)} · Superflex rankings</Text>
      {ed.lede ? <Text style={styles.lede}>{ed.lede}</Text> : null}

      <View style={styles.tabs}>
        {TABS.map((t) => (
          <Pressable key={t} onPress={() => setTab(t)} style={[styles.tab, t === tab && styles.tabOn]} accessibilityRole="tab" accessibilityState={{ selected: t === tab }}>
            <Text style={[styles.tabText, t === tab && styles.tabTextOn]}>{t}</Text>
          </Pressable>
        ))}
      </View>

      <View style={styles.board}>
        <View style={styles.boardHead}>
          <Text style={styles.boardLabel}>BIG BOARD{tab === 'All players' ? '' : ` · ${tab}`}</Text>
          <Text style={styles.boardLabel}>{stars.size ? `★ ${stars.size} STARRED` : 'TAP ☆ TO SAVE'}</Text>
        </View>
        {rows.map(({ p, pr }) => (
          <Pressable key={p.athlete_id} style={styles.boardRow}
            onPress={() => setJump({ y: listTop.current + (tops.current[p.athlete_id] ?? 0), n: (jump?.n ?? 0) + 1 })}>
            <Text style={styles.boardRank}>{shownRank(p, pr)}</Text>
            <ProspectPhoto p={p} size={30} />
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={styles.boardName} numberOfLines={1}>{p.name}</Text>
              <Text style={styles.boardSub} numberOfLines={1}>{p.pos}{pr} · {p.school}</Text>
            </View>
            <Move p={p} />
            {stars.has(p.athlete_id) ? <Text style={styles.boardStar}>★</Text> : null}
          </Pressable>
        ))}
      </View>

      <View onLayout={(e) => { listTop.current = e.nativeEvent.layout.y; }}>
        {rows.map(({ p, pr }) => (
          <View key={p.athlete_id} style={styles.article} onLayout={(e) => { tops.current[p.athlete_id] = e.nativeEvent.layout.y; }}>
            <View style={styles.photoBand}>
              <ProspectPhoto p={p} size={92} square />
              <Text style={styles.bigRank}>{shownRank(p, pr)}</Text>
            </View>
            <View style={styles.nameRow}>
              <Pressable onPress={() => toggle(p)} hitSlop={10} accessibilityRole="button" accessibilityLabel={`Star ${p.name}`} accessibilityState={{ selected: stars.has(p.athlete_id) }}>
                <Text style={[styles.star, stars.has(p.athlete_id) && styles.starOn]}>{stars.has(p.athlete_id) ? '★' : '☆'}</Text>
              </Pressable>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={styles.name}>{p.name}</Text>
                <Text style={styles.sub}>{p.pos}{pr} · {p.school}{p.size ? ` · ${p.size}` : ''}</Text>
              </View>
            </View>
            <View style={styles.tags}>
              {p.role ? <Text style={styles.role}>{p.role.toUpperCase()}</Text> : null}
              {p.tags.map((t) => (
                <Text key={t} style={[styles.tag, (t === 'Rising' || t === 'Consensus No. 1') && styles.tagUp, (t === 'Falling' || t === 'Injury') && styles.tagDown]}>
                  {t === 'Rising' ? '▲ ' : t === 'Falling' ? '▼ ' : ''}{t}
                </Text>
              ))}
            </View>
            <Text style={styles.stats}>{p.line}</Text>
            <View style={styles.meters}>
              <View style={styles.meter}><Text style={styles.meterLabel}>NFL MOCK</Text><Text style={styles.meterValue}>{p.mock || '—'}</Text></View>
              <View style={styles.meter}><Text style={styles.meterLabel}>FANTASY BOARDS</Text><Text style={styles.meterValue}>{p.boards || '—'}</Text></View>
            </View>
            <Text style={styles.text}>{p.text}</Text>
          </View>
        ))}
      </View>

      {ed.sources.length ? (
        <View style={styles.sources}>
          <Text style={styles.sourcesHead}>Sources the GM read this week</Text>
          {ed.sources.map((s) => (
            <Text key={s.url} style={styles.source} onPress={() => Linking.openURL(s.url)}>{s.title || s.url}</Text>
          ))}
          <Text style={styles.note}>Stats are this season's from our college feed; touchdown counts can run a little low.</Text>
        </View>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  hero: { marginTop: 4, borderRadius: 16, overflow: 'hidden', backgroundColor: '#1D2415', paddingTop: 18, paddingBottom: 12, paddingHorizontal: 14 },
  heroArt: { flexDirection: 'row', gap: 10, justifyContent: 'center', alignItems: 'flex-end' },
  heroMid: { marginBottom: 6 },
  kicker: { color: colors.gold, fontSize: 11, fontWeight: '900', letterSpacing: 1.6, marginTop: 12, textAlign: 'center' },
  title: { color: colors.text, fontSize: 26, lineHeight: 29, fontWeight: '900', marginTop: 10 },
  dek: { color: colors.muted, fontSize: 15, lineHeight: 21, marginTop: 6 },
  by: { color: colors.muted, opacity: 0.75, fontSize: 11, fontWeight: '700', marginTop: 8 },
  lede: { color: colors.text, fontSize: 15, lineHeight: 23, marginTop: 14, marginBottom: 6 },
  tabs: { flexDirection: 'row', gap: 6, marginTop: 16, marginBottom: 10, flexWrap: 'wrap' },
  tab: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.line, borderRadius: 14, paddingVertical: 6, paddingHorizontal: 12 },
  tabOn: { backgroundColor: colors.gold, borderColor: colors.gold },
  tabText: { color: colors.muted, fontSize: 12, fontWeight: '700' },
  tabTextOn: { color: colors.bg },
  board: { backgroundColor: colors.row, borderWidth: 1, borderColor: colors.line, borderRadius: 14, paddingVertical: 8, paddingHorizontal: 10 },
  boardHead: { flexDirection: 'row', justifyContent: 'space-between', paddingTop: 2, paddingBottom: 6 },
  boardLabel: { color: colors.muted, fontSize: 10, fontWeight: '800', letterSpacing: 1 },
  boardRow: { flexDirection: 'row', alignItems: 'center', gap: 9, paddingVertical: 6, borderTopWidth: 1, borderTopColor: colors.card },
  boardRank: { color: colors.gold, fontWeight: '900', width: 20, textAlign: 'center', fontSize: 13 },
  boardName: { color: colors.text, fontSize: 13, fontWeight: '800' },
  boardSub: { color: colors.muted, fontSize: 10.5 },
  boardStar: { color: '#E2BE72', fontSize: 13 },
  article: { borderTopWidth: 1, borderTopColor: colors.line, paddingTop: 14, paddingBottom: 6, marginTop: 14 },
  photoBand: { alignItems: 'center', backgroundColor: 'rgba(200,155,74,0.10)', borderRadius: 14, paddingTop: 10, paddingBottom: 6, marginBottom: 8 },
  bigRank: { position: 'absolute', left: 12, top: 6, color: colors.gold, fontWeight: '900', fontSize: 28 },
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  star: { color: colors.muted, fontSize: 26, lineHeight: 28 },
  starOn: { color: '#E2BE72' },
  name: { color: colors.text, fontSize: 17, fontWeight: '900' },
  sub: { color: colors.muted, fontSize: 11.5 },
  tags: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 8 },
  role: { fontSize: 10, fontWeight: '800', letterSpacing: 0.5, color: colors.live, backgroundColor: '#163626', paddingVertical: 3, paddingHorizontal: 7, borderRadius: 7, overflow: 'hidden' },
  tag: { fontSize: 10, fontWeight: '800', paddingVertical: 3, paddingHorizontal: 7, borderRadius: 7, overflow: 'hidden', color: colors.muted, backgroundColor: colors.card },
  tagUp: { color: colors.live, backgroundColor: '#163626' },
  tagDown: { color: '#E39A9A', backgroundColor: '#3A1E1E' },
  stats: { color: colors.muted, fontSize: 12, marginTop: 8 },
  meters: { flexDirection: 'row', gap: 8, marginTop: 8 },
  meter: { flex: 1, backgroundColor: colors.card, borderRadius: 10, paddingVertical: 7, paddingHorizontal: 9 },
  meterLabel: { color: colors.muted, fontSize: 8.5, fontWeight: '800', letterSpacing: 0.8 },
  meterValue: { color: colors.text, fontSize: 13, fontWeight: '800', marginTop: 2 },
  text: { color: colors.text, fontSize: 14.5, lineHeight: 22, marginTop: 10, marginBottom: 8 },
  sources: { marginTop: 16, gap: 6, borderTopWidth: 1, borderTopColor: colors.line, paddingTop: 12 },
  sourcesHead: { color: colors.text, fontSize: 13, fontWeight: '800' },
  source: { color: colors.gold, fontSize: 12.5 },
  note: { color: colors.muted, fontSize: 11, marginTop: 4 },
});
