import { router } from 'expo-router';
import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Animated, Dimensions, Easing, PanResponder, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors } from '../lib/theme';

// Shared pieces from the page designs: cards, tiles, the gold segmented
// switcher, position badges and status pills.

export function Screen({ children, loading, onRefresh }: { children: ReactNode; loading?: boolean; onRefresh?: () => void }) {
  const insets = useSafeAreaInsets();
  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={[styles.content, { paddingTop: insets.top + 8 }]}
      refreshControl={onRefresh ? <RefreshControl refreshing={!!loading} onRefresh={onRefresh} tintColor={colors.gold} /> : undefined}
    >
      {children}
    </ScrollView>
  );
}

export function Back({ label }: { label: string }) {
  return (
    <Pressable onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))} hitSlop={10} style={styles.back}>
      <Text style={styles.backText}>‹ {label}</Text>
    </Pressable>
  );
}

export function Title({ children, eyebrow, right, badge }: { children: ReactNode; eyebrow?: string; right?: ReactNode; badge?: ReactNode }) {
  return (
    <View style={styles.titleRow}>
      <View style={styles.flex}>
        {eyebrow ? <Text style={styles.eyebrow}>{eyebrow}</Text> : null}
        <View style={styles.titleLine}>
          <Text style={[styles.title, styles.titleShrink]} numberOfLines={1}>{children}</Text>
          {badge}
        </View>
      </View>
      {right}
    </View>
  );
}

export function Segmented<T extends string>({ options, value, onChange }: { options: readonly T[]; value: T; onChange: (v: T) => void }) {
  return (
    <View style={styles.seg}>
      {options.map((option) => (
        <Pressable key={option} onPress={() => onChange(option)} style={[styles.segItem, option === value && styles.segOn]}>
          <Text style={[styles.segText, option === value && styles.segTextOn]} numberOfLines={1}>{option}</Text>
        </Pressable>
      ))}
    </View>
  );
}

// The content under a Segmented switcher. Swipe left or right to change tabs: the page follows your
// finger with the neighbouring tab sliding in beside it, then glides the rest of the way on release.
// Only clearly sideways drags count, so scrolling the page up and down is untouched. Past the first or
// last tab it only gives a little, like iOS lists.
export function SwipeTabs<T extends string>({ options, value, onChange, render }: { options: readonly T[]; value: T; onChange: (v: T) => void; render: (v: T) => ReactNode }) {
  // Tabs sit side by side, one screen width apart (the side margins keep the next one just off screen),
  // each moved by `shift`, which rests at -index * page. Swiping moves them all, so the tab that slides in
  // is already in place when it becomes the current one: nothing has to jump. Only the tabs move, not the
  // container, so the current tab always stays inside it and keeps receiving taps.
  const page = Dimensions.get('window').width;
  const index = options.indexOf(value);
  const shift = useRef(new Animated.Value(-index * page)).current;
  const [width, setWidth] = useState(page - 36);
  const [live, setLive] = useState(false); // neighbours are only rendered while a swipe is in play
  const latest = useRef({ options, value, onChange, width });
  latest.current = { options, value, onChange, width };
  const place = useMemo(() => options.map((_, k) => Animated.add(shift, k * page)), [options, shift, page]);

  // Tapping the switcher changes the tab without a swipe: move the strip under it.
  useLayoutEffect(() => {
    shift.stopAnimation();
    shift.setValue(-index * page);
    setLive(false);
  }, [index, page, shift]);

  const responder = useMemo(() => {
    const at = () => -latest.current.options.indexOf(latest.current.value) * page;
    const settle = () =>
      Animated.spring(shift, { toValue: at(), useNativeDriver: true, damping: 24, stiffness: 170, mass: 0.9 }).start(({ finished }) => finished && setLive(false));
    return PanResponder.create({
      onMoveShouldSetPanResponderCapture: (_, g) => Math.abs(g.dx) > 14 && Math.abs(g.dx) > Math.abs(g.dy) * 2,
      onPanResponderTerminationRequest: () => false,
      onPanResponderGrant: () => {
        shift.stopAnimation();
        setLive(true);
      },
      onPanResponderMove: (_, g) => {
        const { options: opts, value: current } = latest.current;
        const i = opts.indexOf(current);
        const edge = (g.dx > 0 && i === 0) || (g.dx < 0 && i === opts.length - 1);
        shift.setValue(at() + (edge ? g.dx * 0.2 : g.dx));
      },
      onPanResponderRelease: (_, g) => {
        const { options: opts, value: current, onChange: change, width: w } = latest.current;
        const i = opts.indexOf(current);
        const dir = g.dx < -w * 0.25 || g.vx < -0.4 ? 1 : g.dx > w * 0.25 || g.vx > 0.4 ? -1 : 0;
        const next = opts[i + dir];
        if (!dir || !next) return settle();
        Animated.timing(shift, { toValue: -(i + dir) * page, duration: 320, easing: Easing.out(Easing.cubic), useNativeDriver: true }).start(({ finished }) => {
          if (finished) change(next);
        });
      },
      onPanResponderTerminate: settle,
    });
  }, [shift, page]);

  return (
    <View {...responder.panHandlers} onLayout={(e) => setWidth(e.nativeEvent.layout.width)} style={{ minHeight: 480 }}>
      {options.map((o, k) => {
        const offset = k - index;
        if (offset !== 0 && !(live && Math.abs(offset) === 1)) return null;
        // The current tab stays in the layout (it sets the height); its neighbours float beside it.
        // Keyed by tab, so the one that slides in keeps its state when it becomes current.
        return (
          <Animated.View
            key={o}
            pointerEvents={offset ? 'none' : 'auto'}
            style={[offset ? { position: 'absolute', top: 0, left: 0, width } : null, { transform: [{ translateX: place[k] }] }]}
          >
            {render(o)}
          </Animated.View>
        );
      })}
    </View>
  );
}

export function Tiles({ children }: { children: ReactNode }) {
  return <View style={styles.tiles}>{children}</View>;
}

export function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'red' }) {
  return (
    <View style={styles.tile}>
      <Text style={styles.tileLabel}>{label}</Text>
      <Text style={[styles.tileValue, tone === 'red' && { color: colors.danger }]} numberOfLines={1}>{value}</Text>
      {sub ? <Text style={styles.tileSub} numberOfLines={1}>{sub}</Text> : null}
    </View>
  );
}

export function Card({ children, title, right, gold, style }: { children: ReactNode; title?: string; right?: ReactNode; gold?: boolean; style?: StyleProp<ViewStyle> }) {
  return (
    <View style={[styles.card, gold && styles.cardGold, style]}>
      {title || right ? (
        <View style={styles.cardTitle}>
          {title ? <Text style={styles.cardTitleText}>{title}</Text> : <View />}
          {typeof right === 'string' ? <Text style={styles.small}>{right}</Text> : right}
        </View>
      ) : null}
      {children}
    </View>
  );
}

export function Row({ children, onPress, first }: { children: ReactNode; onPress?: () => void; first?: boolean }) {
  const body = <View style={[styles.row, first && styles.rowFirst]}>{children}</View>;
  return onPress ? <Pressable onPress={onPress}>{body}</Pressable> : body;
}

export function Pos({ label }: { label: string }) {
  return (
    <View style={styles.pos}>
      <Text style={styles.posText}>{label || '—'}</Text>
    </View>
  );
}

const PILL = {
  live: { color: colors.live, backgroundColor: '#163626' },
  out: { color: '#E39A9A', backgroundColor: '#3A1E1E' },
  warn: { color: '#E3C47A', backgroundColor: '#3A321E' },
  gold: { color: colors.gold, backgroundColor: '#2A2A1A' },
  muted: { color: colors.muted, backgroundColor: colors.card },
};

export function Pill({ label, tone = 'muted' }: { label: string; tone?: keyof typeof PILL }) {
  return <Text style={[styles.pill, PILL[tone]]}>{label}</Text>;
}

export function Bar({ fraction }: { fraction: number }) {
  return (
    <View style={styles.bar}>
      <View style={[styles.barFill, { width: `${Math.max(0, Math.min(1, fraction)) * 100}%` }]} />
    </View>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return <Text style={styles.note}>{children}</Text>;
}

export const money = (n: number | null | undefined) => `${(n ?? 0) < 0 ? '-' : ''}$${Math.abs(n ?? 0).toFixed(1)}`;
export const firstName = (name: string) => name.split(' ')[0];
export const record = (w: number, l: number, t = 0) => `${w}-${l}${t ? `-${t}` : ''}`;
export const ordinal = (n: number | null | undefined) => {
  if (!n) return '—';
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] ?? s[v] ?? s[0]}`;
};

export const ui = StyleSheet.create({
  name: { color: colors.text, fontSize: 15, fontWeight: '700' },
  sub: { color: colors.muted, fontSize: 12, marginTop: 1 },
  right: { marginLeft: 'auto', alignItems: 'flex-end' },
  value: { color: colors.text, fontSize: 14 },
  gold: { color: colors.gold },
  link: { color: colors.gold, fontSize: 13, fontWeight: '700' },
  flex: { flex: 1 },
});

const styles = StyleSheet.create({
  titleLine: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  titleShrink: { flexShrink: 1 },
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 16, paddingBottom: 40 },
  flex: { flex: 1 },
  back: { alignSelf: 'flex-start', marginBottom: 6 },
  backText: { color: colors.muted, fontSize: 15 },
  titleRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 12, gap: 10 },
  eyebrow: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1.2, marginBottom: 2 },
  title: { color: colors.text, fontSize: 26, fontWeight: '800' },
  seg: { flexDirection: 'row', backgroundColor: colors.card, borderRadius: 12, padding: 3, marginBottom: 12 },
  segItem: { flex: 1, paddingVertical: 8, borderRadius: 9, alignItems: 'center' },
  segOn: { backgroundColor: colors.gold },
  segText: { color: colors.muted, fontSize: 13, fontWeight: '700' },
  segTextOn: { color: colors.bg },
  tiles: { flexDirection: 'row', gap: 8, marginBottom: 12 },
  tile: { flex: 1, backgroundColor: colors.row, borderWidth: 1, borderColor: colors.line, borderRadius: 14, paddingVertical: 10, paddingHorizontal: 6, alignItems: 'center' },
  tileLabel: { color: colors.muted, fontSize: 10, fontWeight: '700', letterSpacing: 1 },
  tileValue: { color: colors.gold, fontSize: 19, fontWeight: '800', marginTop: 3 },
  tileSub: { color: colors.muted, fontSize: 11, marginTop: 2 },
  card: { backgroundColor: colors.row, borderWidth: 1, borderColor: colors.line, borderRadius: 18, padding: 14, marginBottom: 12 },
  cardGold: { borderColor: colors.gold },
  cardTitle: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  cardTitleText: { color: colors.text, fontSize: 16, fontWeight: '700' },
  small: { color: colors.muted, fontSize: 12 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 11, borderTopWidth: 1, borderTopColor: colors.card },
  rowFirst: { borderTopWidth: 0 },
  pos: { width: 34, borderWidth: 1, borderColor: colors.line, borderRadius: 8, paddingVertical: 3, alignItems: 'center' },
  posText: { color: colors.muted, fontSize: 10, fontWeight: '800' },
  pill: { fontSize: 10, fontWeight: '800', letterSpacing: 1, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 9, overflow: 'hidden' },
  bar: { height: 6, borderRadius: 3, backgroundColor: colors.card, overflow: 'hidden' },
  barFill: { height: '100%', backgroundColor: colors.gold },
  note: { color: colors.muted, fontSize: 12, textAlign: 'center', marginTop: 4 },
});
