// The lock screen Live Activity (and Dynamic Island) for my live matchup: design B "Odds bar + last play",
// in the app's green and gold. The server builds the props (api/live_activity.py) and the game-day watcher
// pushes new ones to Apple while games are on, so this keeps updating with the app closed.
//
// The 'widget' function runs in a separate runtime inside the widget extension: no hooks, no imports
// beyond @expo/ui, and nothing from outside the function (colors and helpers are declared inside it).
import { HStack, Rectangle, Spacer, Text, VStack, ZStack } from '@expo/ui/swift-ui';
import {
  activityBackgroundTint, background, font, foregroundStyle, frame, lineLimit, monospacedDigit, opacity, padding,
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
  const TRACK = '#3A5446';
  const DARK = '#071A12';
  const quiet = environment.isLuminanceReduced === true;
  const stale = environment.isStale === true;

  const pts = (n: number) => (Math.round((n || 0) * 10) / 10).toFixed(1);
  const counts = (s: LiveSide) => (s.playing || s.left ? `${s.playing} playing · ${s.left} left` : 'all done');
  const odds = typeof props.odds === 'number' ? Math.max(0, Math.min(100, Math.round(props.odds))) : null;
  const status = props.final ? 'FINAL' : stale ? 'PAUSED' : '● LIVE';
  const statusColor = props.final ? GOLD : stale ? MUTED : LIVE;

  // Win odds as one bar: gold for me, muted green for them. A gradient with 50 even color stops gives
  // a sharp split at the odds without needing measured widths.
  const stops: string[] = [];
  for (let i = 0; i < 50; i++) stops.push(odds !== null && i * 2 < odds ? GOLD : TRACK);
  const bar = (height: number) => (
    <Rectangle
      modifiers={[
        frame({ height }),
        background({ type: 'linearGradient', colors: stops, startPoint: { x: 0, y: 0.5 }, endPoint: { x: 1, y: 0.5 } }),
        opacity(odds === null ? 0.35 : 1),
      ]}
    />
  );

  const change = props.last;
  const changeLine = change ? (
    <HStack spacing={6}>
      <Text modifiers={[font({ size: 14, weight: 'bold' }), monospacedDigit(), foregroundStyle(change.good ? LIVE : RED)]}>{change.pts}</Text>
      <Text modifiers={[font({ size: 14, weight: 'semibold' }), foregroundStyle(CREAM), lineLimit(1)]}>{change.who}</Text>
      <Text modifiers={[font({ size: 13 }), foregroundStyle(MUTED), lineLimit(1)]}>
        {[change.what, change.game].filter(Boolean).join(' · ')}
      </Text>
      <Spacer />
    </HStack>
  ) : null;

  const scoreBlock = (s: LiveSide, mine: boolean) => (
    <VStack alignment={mine ? 'leading' : 'trailing'} spacing={1}>
      <Text modifiers={[font({ size: 11, weight: 'semibold' }), foregroundStyle(mine ? GOLD : MUTED)]}>{s.name}</Text>
      <Text modifiers={[font({ size: 32, weight: 'heavy' }), monospacedDigit(), foregroundStyle(mine ? CREAM : DIM)]}>{pts(s.score)}</Text>
      <Text modifiers={[font({ size: 11 }), foregroundStyle(MUTED)]}>{counts(s)}</Text>
    </VStack>
  );

  const banner = (
    <ZStack>
      <Rectangle modifiers={[background({ type: 'linearGradient', colors: ['#123526', DARK], startPoint: { x: 0.5, y: 0 }, endPoint: { x: 0.5, y: 1 } })]} />
      {quiet ? null : (
        <Rectangle
          modifiers={[
            background({ type: 'radialGradient', colors: [GOLD, 'clear'], center: { x: 1, y: 0 }, startRadius: 0, endRadius: 170 }),
            opacity(0.35),
          ]}
        />
      )}
      <VStack spacing={8} modifiers={[padding({ horizontal: 16, vertical: 14 })]}>
        <HStack alignment="top">
          {scoreBlock(props.me, true)}
          <Spacer />
          <VStack spacing={2}>
            <Text modifiers={[font({ size: 12, weight: 'bold' }), foregroundStyle(statusColor)]}>{status}</Text>
            <Text modifiers={[font({ size: 11 }), foregroundStyle(MUTED)]}>{`Week ${props.week}`}</Text>
          </VStack>
          <Spacer />
          {scoreBlock(props.them, false)}
        </HStack>
        {bar(7)}
        <HStack>
          <Text modifiers={[font({ size: 12, weight: 'semibold' }), foregroundStyle(GOLD)]}>{odds === null ? '' : `${odds}% win`}</Text>
          <Spacer />
          <Text modifiers={[font({ size: 12 }), foregroundStyle(MUTED)]}>{`proj ${props.me.proj} – ${props.them.proj}`}</Text>
          <Spacer />
          <Text modifiers={[font({ size: 12, weight: 'semibold' }), foregroundStyle(CREAM)]}>{odds === null ? '' : `${100 - odds}%`}</Text>
        </HStack>
        {changeLine ? <Rectangle modifiers={[frame({ height: 1 }), background(GOLD), opacity(0.28)]} /> : null}
        {changeLine}
      </VStack>
    </ZStack>
  );

  return {
    banner: <VStack modifiers={[activityBackgroundTint(DARK)]}>{banner}</VStack>,
    compactLeading: <Text modifiers={[font({ size: 15, weight: 'heavy' }), monospacedDigit(), foregroundStyle(GOLD)]}>{pts(props.me.score)}</Text>,
    compactTrailing: <Text modifiers={[font({ size: 15, weight: 'heavy' }), monospacedDigit(), foregroundStyle(DIM)]}>{pts(props.them.score)}</Text>,
    minimal: <Text modifiers={[font({ size: 12, weight: 'heavy' }), foregroundStyle(GOLD)]}>{odds === null ? 'LD' : `${odds}%`}</Text>,
    expandedLeading: (
      <VStack alignment="leading" spacing={1} modifiers={[padding({ leading: 6 })]}>
        <Text modifiers={[font({ size: 11, weight: 'semibold' }), foregroundStyle(GOLD)]}>{props.me.name}</Text>
        <Text modifiers={[font({ size: 26, weight: 'heavy' }), monospacedDigit(), foregroundStyle(CREAM)]}>{pts(props.me.score)}</Text>
      </VStack>
    ),
    expandedTrailing: (
      <VStack alignment="trailing" spacing={1} modifiers={[padding({ trailing: 6 })]}>
        <Text modifiers={[font({ size: 11, weight: 'semibold' }), foregroundStyle(MUTED)]}>{props.them.name}</Text>
        <Text modifiers={[font({ size: 26, weight: 'heavy' }), monospacedDigit(), foregroundStyle(DIM)]}>{pts(props.them.score)}</Text>
      </VStack>
    ),
    expandedCenter: (
      <VStack spacing={1}>
        <Text modifiers={[font({ size: 11, weight: 'bold' }), foregroundStyle(statusColor)]}>{status}</Text>
        <Text modifiers={[font({ size: 16, weight: 'heavy' }), foregroundStyle(GOLD)]}>{odds === null ? '' : `${odds}%`}</Text>
      </VStack>
    ),
    expandedBottom: (
      <VStack spacing={6} modifiers={[padding({ horizontal: 6 })]}>
        {bar(5)}
        {changeLine}
      </VStack>
    ),
  };
};

export default createLiveActivity<LiveMatchupProps>('LiveMatchup', LiveMatchup);
