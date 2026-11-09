import * as Haptics from 'expo-haptics';
import { router } from 'expo-router';
import { useCallback, useMemo, useRef, useState } from 'react';
import { Animated, Easing, StyleSheet, Text, View, type NativeScrollEvent, type NativeSyntheticEvent } from 'react-native';

import { refreshPage } from '../lib/api';
import { colors } from '../lib/theme';

// Pulling down on a page, in two stages:
// - A gentle pull refreshes everything on the page. A gold football tips upright as you pull and fills in
//   (with a light tap) once it's far enough; let go and it spins until the page has reloaded.
// - A much longer pull opens search. Past the refresh point the football gives way to a search icon,
//   "Keep pulling to search", then "Let go to search" with a firmer tap. Letting go there opens search only.
// Search only counts when the page was already resting at the top when the finger landed, so a flick that
// bounces off the top never opens it.
const REFRESH_AT = 70;
const HINT_AT = 150;
const SEARCH_AT = 220;
const HOLD = 56; // room kept open above the page while it refreshes
const STILL_MS = 150;
const MIN_SPIN_MS = 700; // a fast answer still reads as "refreshed" instead of a flicker
const BALL_H = 19;

type Hint = null | 'pull' | 'ready';

export function usePullDown({ search = true, top = 0 }: { search?: boolean; top?: number } = {}) {
  const y = useRef(new Animated.Value(0)).current;
  const spin = useRef(new Animated.Value(0)).current;
  const hold = useRef(new Animated.Value(0)).current;
  const s = useRef({ armed: false, refreshReady: false, searchReady: false, lastMove: 0, busy: false });
  const [hint, setHint] = useState<Hint>(null);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    if (s.current.busy) return;
    s.current.busy = true;
    setRefreshing(true);
    Animated.timing(hold, { toValue: HOLD, duration: 180, useNativeDriver: false }).start();
    spin.setValue(0);
    const loop = Animated.loop(Animated.timing(spin, { toValue: 1, duration: 650, easing: Easing.linear, useNativeDriver: true }));
    loop.start();
    const started = Date.now();
    try {
      await refreshPage();
    } finally {
      const left = MIN_SPIN_MS - (Date.now() - started);
      if (left > 0) await new Promise((done) => setTimeout(done, left));
      loop.stop();
      Animated.timing(hold, { toValue: 0, duration: 220, useNativeDriver: false }).start();
      setRefreshing(false);
      s.current.busy = false;
    }
  }, [hold, spin]);

  const scrollProps = useMemo(() => ({
    scrollEventThrottle: 16,
    onScrollBeginDrag: (e: NativeSyntheticEvent<NativeScrollEvent>) => {
      const st = s.current;
      st.armed = search && e.nativeEvent.contentOffset.y <= 1 && Date.now() - st.lastMove > STILL_MS;
      st.refreshReady = false;
      st.searchReady = false;
    },
    onScroll: Animated.event([{ nativeEvent: { contentOffset: { y } } }], {
      useNativeDriver: false,
      listener: (e: NativeSyntheticEvent<NativeScrollEvent>) => {
        const st = s.current;
        st.lastMove = Date.now();
        const pulled = -e.nativeEvent.contentOffset.y;
        if (st.busy) return;
        const refreshReady = pulled >= REFRESH_AT;
        if (refreshReady && !st.refreshReady && !st.searchReady) Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
        st.refreshReady = refreshReady;
        if (!st.armed) return;
        const searchReady = pulled >= SEARCH_AT;
        if (searchReady && !st.searchReady) Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => undefined);
        st.searchReady = searchReady;
        const next: Hint = searchReady ? 'ready' : pulled >= HINT_AT ? 'pull' : null;
        setHint((h) => (h === next ? h : next));
      },
    }),
    onScrollEndDrag: (e: NativeSyntheticEvent<NativeScrollEvent>) => {
      const st = s.current;
      const pulled = -e.nativeEvent.contentOffset.y;
      if (st.armed && pulled >= SEARCH_AT) router.push('/search');
      else if (pulled >= REFRESH_AT) refresh();
      st.armed = false;
      st.refreshReady = false;
      st.searchReady = false;
      setHint(null);
    },
  }), [y, search, refresh]);

  // The football rides in the middle of the gap the pull opens above the page.
  const rise = y.interpolate({ inputRange: [-HOLD * 2, 0], outputRange: [HOLD - BALL_H / 2, -BALL_H / 2], extrapolate: 'clamp' });
  const tip = y.interpolate({ inputRange: [-REFRESH_AT, 0], outputRange: ['0deg', '-90deg'], extrapolate: 'clamp' });
  const show = y.interpolate({ inputRange: [-REFRESH_AT, -8, 0], outputRange: [1, 0.3, 0], extrapolate: 'clamp' });
  const full = y.interpolate({ inputRange: [-REFRESH_AT, -REFRESH_AT + 3, 0], outputRange: [1, 0, 0], extrapolate: 'clamp' });
  const turn = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });

  const overlay = (
    <View pointerEvents="none" style={[styles.overlay, { top }]}>
      {refreshing ? (
        <Animated.View style={[styles.at, { transform: [{ translateY: HOLD / 2 - BALL_H / 2 }, { rotate: turn }] }]}>
          <Ball filled />
        </Animated.View>
      ) : hint ? (
        <Animated.View style={[styles.at, styles.search, { transform: [{ translateY: Animated.subtract(rise, 10) }] }]}>
          <Magnifier on={hint === 'ready'} />
          <Text style={[styles.hint, hint === 'ready' && styles.hintOn]}>{hint === 'ready' ? 'Let go to search' : 'Keep pulling to search'}</Text>
        </Animated.View>
      ) : (
        <Animated.View style={[styles.at, { opacity: show, transform: [{ translateY: rise }, { rotate: tip }] }]}>
          <Ball />
          <Animated.View style={[StyleSheet.absoluteFill, { opacity: full }]}><Ball filled /></Animated.View>
        </Animated.View>
      )}
    </View>
  );

  // Put at the very top of the scroll content: holds the gap open while the football spins.
  const spacer = <Animated.View style={{ height: hold }} />;

  return { scrollProps, overlay, spacer, refreshing };
}

function Ball({ filled }: { filled?: boolean }) {
  const lace = filled ? colors.bg : colors.gold;
  return (
    <View style={[styles.ball, filled && styles.ballFilled]}>
      <View style={[styles.lace, { backgroundColor: lace }]} />
      {[-4, -1.5, 1, 3.5].map((x) => <View key={x} style={[styles.stitch, { backgroundColor: lace, left: 15 + x * 1.6 - 0.7 }]} />)}
    </View>
  );
}

function Magnifier({ on }: { on: boolean }) {
  const c = on ? colors.gold : colors.muted;
  return (
    <View style={styles.mag}>
      <View style={[styles.magGlass, { borderColor: c }]} />
      <View style={[styles.magHandle, { backgroundColor: c }]} />
    </View>
  );
}

const styles = StyleSheet.create({
  overlay: { position: 'absolute', left: 0, right: 0, height: 1, alignItems: 'center', zIndex: 10, overflow: 'visible' },
  at: { position: 'absolute', top: 0 },
  ball: { width: 30, height: BALL_H, borderRadius: BALL_H / 2, borderWidth: 2, borderColor: colors.gold, justifyContent: 'center', backgroundColor: colors.bg },
  ballFilled: { backgroundColor: colors.gold },
  lace: { position: 'absolute', left: 8, right: 8, height: 1.6, top: BALL_H / 2 - 2.8 },
  stitch: { position: 'absolute', width: 1.4, height: 5, top: BALL_H / 2 - 4.5 },
  search: { alignItems: 'center', width: 220 },
  hint: { marginTop: 5, color: colors.muted, fontSize: 12, fontWeight: '800', letterSpacing: 0.5 },
  hintOn: { color: colors.gold },
  mag: { width: 22, height: 22 },
  magGlass: { position: 'absolute', left: 1, top: 1, width: 14, height: 14, borderRadius: 7, borderWidth: 2.4 },
  magHandle: { position: 'absolute', left: 13, top: 15, width: 8, height: 2.6, borderRadius: 1.3, transform: [{ rotate: '45deg' }] },
});
