// The lock screen Live Activity (and Dynamic Island) for my live matchup: design 5 "Odds ring" with the
// latest points change along the bottom, on the app's dark green with gold. The server builds the props
// (api/live_activity.py) and the game-day watcher pushes new ones to Apple while games are on, so this keeps
// updating with the app closed.
//
// The background is set on the root view itself (activityBackgroundTint + a gradient background) and no
// shape is drawn behind the content: shapes fill with the system foreground, which washed design B out white.
//
// The 'widget' function runs in a separate runtime inside the widget extension: no hooks, no imports
// beyond @expo/ui, and nothing from outside the function (colors and helpers are declared inside it).
import { Gauge, HStack, Rectangle, Spacer, Text, VStack, ZStack } from '@expo/ui/swift-ui';
import {
  activityBackgroundTint, background, font, foregroundStyle, frame, gaugeStyle, lineLimit, minimumScaleFactor, monospacedDigit,
  opacity, padding, scaleEffect, tint,
} from '@expo/ui/swift-ui/modifiers';
import { createLiveActivity, type LiveActivityEnvironment } from 'expo-widgets';

export type LiveSide = { name: string; score: number; proj: number; playing: number; left: number };
export type LiveChange = { pts: string; good: boolean; mine: boolean; who: string; what: string; game: string };
export type LiveMatchupProps = {
  week: number;
  me: LiveSide;
  them: LiveSide;
  odds: number | null;
  last: LiveChange | null;
  final: boolean;
};

const LiveMatchup = (props: LiveMatchupProps, environment: LiveActivityEnvironment) => {
  'widget';
  const GOLD = '#C89B4A';
  const CREAM = '#F5EBD7';
  const MUTED = '#A9A18F';
  const DIM = '#8F9A8F';
  const LIVE = '#4CC38A';
  const RED = '#E05555';
  const DARK = '#071A12';
  const stale = environment.isStale === true;

  const pts = (n: number) => (Math.round((n || 0) * 10) / 10).toFixed(1);
  const odds = typeof props.odds === 'number' ? Math.max(0, Math.min(100, Math.round(props.odds))) : null;
  // No "paused" label: between game windows the widget just shows the latest numbers.
  const status = props.final ? 'FINAL' : stale ? '' : '● LIVE';
  const statusColor = props.final ? GOLD : LIVE;

  // Win odds as a ring: gold fill on a muted green track, the percent drawn on top in the middle (the gauge's
  // own value label doesn't show on the lock screen). Fixed frame so the scaled ring never overlaps the scores.
  const ring = (size: number) => (
    <ZStack modifiers={[frame({ width: size, height: size })]}>
      <Gauge
        value={odds === null ? 0 : odds / 100}
        min={0}
        max={1}
        modifiers={[gaugeStyle('circularCapacity'), tint(GOLD), scaleEffect(size / 50), opacity(odds === null ? 0.5 : 1)]}
      />
      <VStack spacing={0}>
        <Text modifiers={[font({ size: size * 0.27, weight: 'heavy' }), monospacedDigit(), foregroundStyle(GOLD)]}>
          {odds === null ? '–' : `${odds}%`}
        </Text>
        <Text modifiers={[font({ size: size * 0.13, weight: 'bold' }), foregroundStyle(MUTED)]}>WIN</Text>
      </VStack>
    </ZStack>
  );

  const change = props.last;
  const changeLine = change ? (
    <HStack spacing={6}>
      <Text modifiers={[font({ size: 14, weight: 'bold' }), monospacedDigit(), foregroundStyle(change.good ? LIVE : RED)]}>{change.pts}</Text>
      <Text modifiers={[font({ size: 14, weight: 'semibold' }), foregroundStyle(change.mine ? CREAM : DIM), lineLimit(1)]}>{change.who}</Text>
      <Text modifiers={[font({ size: 13 }), foregroundStyle(MUTED), lineLimit(1)]}>
        {[change.what, change.game].filter(Boolean).join(' · ')}
      </Text>
      <Spacer />
    </HStack>
  ) : null;

  const side = (s: LiveSide, mine: boolean, size: number) => (
    <VStack alignment={mine ? 'leading' : 'trailing'} spacing={1}>
      <Text modifiers={[font({ size: 11, weight: 'bold' }), foregroundStyle(mine ? GOLD : MUTED), lineLimit(1), minimumScaleFactor(0.7)]}>{s.name}</Text>
      <Text modifiers={[font({ size, weight: 'heavy' }), monospacedDigit(), foregroundStyle(mine ? CREAM : DIM)]}>{pts(s.score)}</Text>
      <Text modifiers={[font({ size: 11 }), monospacedDigit(), foregroundStyle(MUTED)]}>{`proj ${s.proj}`}</Text>
    </VStack>
  );

  const divider = <Rectangle modifiers={[frame({ height: 1 }), foregroundStyle(GOLD), opacity(0.3)]} />;

  const banner = (
    <VStack
      spacing={9}
      modifiers={[
        padding({ horizontal: 16, vertical: 13 }),
        frame({ maxWidth: Infinity }),
        background({ type: 'linearGradient', colors: ['#123526', DARK], startPoint: { x: 0.5, y: 0 }, endPoint: { x: 0.5, y: 1 } }),
        activityBackgroundTint(DARK),
      ]}
    >
      <HStack>
        <Text modifiers={[font({ size: 11, weight: 'bold' }), foregroundStyle(statusColor)]}>{status}</Text>
        <Spacer />
        <Text modifiers={[font({ size: 11, weight: 'semibold' }), foregroundStyle(MUTED)]}>{`LEGACY · WEEK ${props.week}`}</Text>
      </HStack>
      <HStack alignment="center">
        {side(props.me, true, 32)}
        <Spacer />
        {ring(66)}
        <Spacer />
        {side(props.them, false, 32)}
      </HStack>
      {changeLine ? divider : null}
      {changeLine}
    </VStack>
  );

  return {
    banner,
    compactLeading: <Text modifiers={[font({ size: 15, weight: 'heavy' }), monospacedDigit(), foregroundStyle(GOLD)]}>{pts(props.me.score)}</Text>,
    compactTrailing: <Text modifiers={[font({ size: 15, weight: 'heavy' }), monospacedDigit(), foregroundStyle(DIM)]}>{pts(props.them.score)}</Text>,
    minimal: odds === null
      ? <Text modifiers={[font({ size: 12, weight: 'heavy' }), foregroundStyle(GOLD)]}>LD</Text>
      : (
        <ZStack>
          <Gauge value={odds / 100} modifiers={[gaugeStyle('circularCapacity'), tint(GOLD), scaleEffect(0.45)]} />
          <Text modifiers={[font({ size: 9, weight: 'heavy' }), foregroundStyle(GOLD)]}>{`${odds}`}</Text>
        </ZStack>
      ),
    expandedLeading: <VStack modifiers={[padding({ leading: 6 })]}>{side(props.me, true, 26)}</VStack>,
    expandedTrailing: <VStack modifiers={[padding({ trailing: 6 })]}>{side(props.them, false, 26)}</VStack>,
    expandedCenter: (
      <VStack spacing={2}>
        <Text modifiers={[font({ size: 10, weight: 'bold' }), foregroundStyle(statusColor)]}>{status}</Text>
        {ring(52)}
      </VStack>
    ),
    expandedBottom: <VStack modifiers={[padding({ horizontal: 6, top: 4 })]}>{changeLine}</VStack>,
  };
};

export default createLiveActivity<LiveMatchupProps>('LiveMatchup', LiveMatchup);
