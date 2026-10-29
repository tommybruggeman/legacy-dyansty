import { useEffect, useRef } from 'react';
import { Animated, Easing, Image, Pressable, StyleSheet, useWindowDimensions } from 'react-native';

import { colors } from '../lib/theme';

// Launch animation (Tommy picked option B "Slam & Shockwave"): the two halves of the
// crest's football spin in and slam together, a gold shockwave and rays burst out,
// the frame and crown pop in and the name fades up. 4s of animation, 2s hold, then
// the crest shrinks up and the overlay fades into the app. Tap anywhere to skip.
const ANIM = 4000;
const HOLD = 2000;
const EXIT = 500;
const TOTAL = ANIM + HOLD + EXIT;
const END = ANIM + HOLD;

const base = require('../../assets/intro/crest-base.png');
const left = require('../../assets/intro/crest-left.png');
const right = require('../../assets/intro/crest-right.png');
const rays = require('../../assets/intro/crest-rays.png');
const word = require('../../assets/intro/crest-word.png');

const shakePoints = Array.from({ length: 13 }, (_, i) => 1100 + i * 33);

export function LaunchIntro({ onReady, onDone }: { onReady: () => void; onDone: () => void }) {
  const { width, height } = useWindowDimensions();
  const t = useRef(new Animated.Value(0)).current;
  const skip = useRef(new Animated.Value(1)).current;
  const done = useRef(false);

  const size = Math.min(width * 0.95, 400);
  const fly = width * 1.3;

  function finish() {
    if (done.current) return;
    done.current = true;
    onDone();
  }

  useEffect(() => {
    const anim = Animated.timing(t, { toValue: TOTAL, duration: TOTAL, easing: Easing.linear, useNativeDriver: true });
    anim.start(({ finished }) => finished && finish());
    return () => anim.stop();
  }, []);

  function skipNow() {
    Animated.timing(skip, { toValue: 0, duration: 220, useNativeDriver: true }).start(() => finish());
  }

  const r = (input: number[], output: number[] | string[], easing?: (v: number) => number) =>
    t.interpolate({ inputRange: input, outputRange: output as any, easing, extrapolate: 'clamp' });

  const slam = Easing.in(Easing.cubic);
  const halfIn = r([200, 1100], [1, 0], slam);
  const shakeX = r(shakePoints, shakePoints.map((_, i) => (i % 2 ? 1 : -1) * 12 * (1 - i / 12)));
  const shakeY = r(shakePoints, shakePoints.map((_, i) => (i % 3 ? 1 : -1) * 8 * (1 - i / 12)));
  const pop = r([1200, 1800], [0.55, 1], Easing.out(Easing.back(2)));
  const popOpacity = r([1200, 1450], [0, 1]);
  const ring = r([1100, 2000], [0.1, 2.2], Easing.out(Easing.cubic));
  const ringOpacity = r([1100, 2000], [1, 0]);
  const raysOpacity = r([1100, 1300, 2600, 3800], [0, 0.13, 0.13, 0]);
  const raysSpin = r([1100, ANIM], ['0deg', '40deg']);
  const wordOpacity = r([2300, 3300, END, END + 300], [0, 1, 1, 0]);
  const wordRise = r([2300, 3300], [18, 0], Easing.out(Easing.cubic));
  const exitScale = r([END, TOTAL], [1, 0.45], Easing.inOut(Easing.quad));
  const exitRise = r([END, TOTAL], [0, -height * 0.32], Easing.inOut(Easing.quad));
  const fade = r([END + 150, TOTAL], [1, 0]);

  const half = (src: number, dir: -1 | 1) => (
    <Animated.Image
      source={src}
      style={[
        styles.layer,
        { width: size, height: size },
        {
          transform: [
            { translateX: Animated.multiply(halfIn, dir * fly) },
            { translateY: Animated.multiply(halfIn, dir * 40) },
            { rotate: halfIn.interpolate({ inputRange: [0, 1], outputRange: ['0deg', `${dir * 40}deg`] }) },
          ],
        },
      ]}
    />
  );

  return (
    <Animated.View style={[StyleSheet.absoluteFill, styles.screen, { opacity: Animated.multiply(fade, skip) }]}>
      <Pressable style={StyleSheet.absoluteFill} onPress={skipNow} onLayout={onReady}>
        <Animated.View style={[styles.center, { transform: [{ translateX: shakeX }, { translateY: shakeY }] }]}>
          <Animated.Image
            source={rays}
            style={[styles.layer, { width: size * 3, height: size * 3, opacity: raysOpacity, transform: [{ rotate: raysSpin }] }]}
          />
          <Animated.View
            style={[
              styles.ring,
              { width: size, height: size, borderRadius: size / 2, opacity: ringOpacity, transform: [{ scale: ring }] },
            ]}
          />
          <Animated.View style={[styles.layer, { width: size, height: size, transform: [{ translateY: exitRise }, { scale: exitScale }] }]}>
            <Animated.Image source={base} style={[styles.layer, { width: size, height: size, opacity: popOpacity, transform: [{ scale: pop }] }]} />
            {half(left, -1)}
            {half(right, 1)}
          </Animated.View>
        </Animated.View>
        <Animated.View style={[styles.word, { top: height / 2 + size * 0.5, opacity: wordOpacity, transform: [{ translateY: wordRise }] }]}>
          <Image source={word} style={{ width: Math.min(width * 0.8, 320), height: Math.min(width * 0.8, 320) * (130 / 900) }} resizeMode="contain" />
        </Animated.View>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  screen: { backgroundColor: colors.bg, zIndex: 100 },
  center: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, alignItems: 'center', justifyContent: 'center' },
  layer: { position: 'absolute' },
  ring: { position: 'absolute', borderWidth: 6, borderColor: '#F2CF6A' },
  word: { position: 'absolute', left: 0, right: 0, alignItems: 'center' },
});

