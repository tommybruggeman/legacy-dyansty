import type { ComponentProps } from 'react';
import { type GestureResponderEvent, Pressable as RNPressable } from 'react-native';

import { markPress } from '../lib/telemetry';

// React Native's Pressable, plus a note that a press happened, so Owner activity can tell taps that did
// something from taps on things that aren't buttons. Use this one everywhere instead of react-native's.
// (React 19 passes ref as an ordinary prop, so it rides along in rest.)
export function Pressable({ onPress, onLongPress, ...rest }: ComponentProps<typeof RNPressable>) {
  return (
    <RNPressable
      {...rest}
      onPress={onPress ? (e: GestureResponderEvent) => { markPress(); onPress(e); } : undefined}
      onLongPress={onLongPress ? (e: GestureResponderEvent) => { markPress(); onLongPress(e); } : undefined}
    />
  );
}
