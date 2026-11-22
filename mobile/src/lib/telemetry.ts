import Constants from 'expo-constants';
import { usePathname } from 'expo-router';
import { useEffect, useRef } from 'react';
import { AppState, Dimensions, type GestureResponderEvent, PixelRatio, Platform } from 'react-native';

import { apiSend } from './api';
import { newReplaySession, replaySoon, setFrameReporter } from './replay';

// Our own usage numbers and crash reports. Both go to the Legacy API (stored in our database / server log);
// no outside analytics or crash service. Nothing here may ever block or break the app.

const build = String(Constants.expoConfig?.ios?.buildNumber ?? '');
const version = String(Constants.expoConfig?.version ?? '');

// kind: screen (a timed page view), tap, swipe, push_open, app_open, app_close. detail holds tap spots (0-1 of the screen) etc.
type View = { screen: string; seconds: number; at: string; kind?: string; detail?: Record<string, unknown> };
let queue: View[] = [];
let sending = false;
let currentScreen = '/';

function push(view: View) {
  queue.push(view);
  if (queue.length > 400) queue = queue.slice(-400);
}

// One event that isn't a timed screen view (taps, swipes, alert opens...). Sent with the next batch.
export function track(kind: string, detail?: Record<string, unknown>, screen?: string) {
  push({ screen: screen ?? currentScreen, seconds: 0, at: new Date().toISOString(), kind, detail });
}

function device(): Record<string, unknown> {
  const { width, height } = Dimensions.get('window');
  const c = (Platform.constants ?? {}) as { osVersion?: string; interfaceIdiom?: string };
  let tz: string | null = null;
  try {
    tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    tz = null;
  }
  return { os: `${Platform.OS} ${c.osVersion ?? Platform.Version}`, idiom: c.interfaceIdiom ?? null, version, build,
    width: Math.round(width), height: Math.round(height), font_scale: PixelRatio.getFontScale(), tz };
}

// Every tap and swipe anywhere in the app, as a spot on the screen (fractions, so any phone size lines up).
let touchStart: { x: number; y: number; t: number } | null = null;
export const touchLogger = {
  onTouchStart(e: GestureResponderEvent) {
    const { pageX, pageY } = e.nativeEvent;
    touchStart = { x: pageX, y: pageY, t: Date.now() };
  },
  onTouchEnd(e: GestureResponderEvent) {
    const start = touchStart;
    touchStart = null;
    if (!start) return;
    try {
      const { pageX, pageY } = e.nativeEvent;
      const { width, height } = Dimensions.get('window');
      const dx = pageX - start.x;
      const dy = pageY - start.y;
      const ms = Date.now() - start.t;
      const r = (v: number, of: number) => Math.round((v / Math.max(1, of)) * 1000) / 1000;
      if (Math.abs(dx) < 12 && Math.abs(dy) < 12) {
        track('tap', { x: r(pageX, width), y: r(pageY, height), ms });
        replaySoon(600);
      } else {
        const dir = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 'right' : 'left') : dy > 0 ? 'down' : 'up';
        track('swipe', { dir, x: r(start.x, width), y: r(start.y, height), to_x: r(pageX, width), to_y: r(pageY, height), ms });
        replaySoon(900); // after the scroll glides to a stop
      }
    } catch {
      // never break a touch
    }
  },
};

async function flush() {
  if (sending || queue.length === 0) return;
  sending = true;
  const batch = queue;
  queue = [];
  try {
    await apiSend('POST', '/me/activity', { views: batch, build });
  } catch {
    // Offline: keep the views for next time (capped so a long offline stretch can't grow forever).
    queue = [...batch, ...queue].slice(-400);
  } finally {
    sending = false;
  }
}

// Times how long each screen stays open; finished views are sent every minute and when the app goes to the background.
export function useScreenTime(enabled: boolean) {
  const pathname = usePathname();
  const current = useRef<{ screen: string; since: number } | null>(null);
  const path = useRef(pathname);
  path.current = pathname;

  const close = () => {
    const open = current.current;
    if (!open) return;
    const seconds = Math.round((Date.now() - open.since) / 1000);
    if (seconds >= 1) push({ screen: open.screen, seconds, at: new Date(open.since).toISOString() });
    current.current = null;
  };

  useEffect(() => {
    if (!enabled) return;
    close();
    currentScreen = pathname || '/';
    current.current = { screen: currentScreen, since: Date.now() };
    replaySoon(700);
  }, [pathname, enabled]);

  useEffect(() => {
    if (!enabled) return;
    const openedAt = { t: Date.now() };
    const closedAt = { t: Date.now() };
    setFrameReporter((p) => track('frame', { path: p }));
    newReplaySession();
    track('app_open', { cold: true, ...device() }, path.current || '/');
    let last = AppState.currentState;
    const sub = AppState.addEventListener('change', (state) => {
      if (state === last) return;
      if (state === 'active') {
        // Back within 30 minutes continues the same replay; longer starts a new one.
        if (Date.now() - closedAt.t > 30 * 60_000) newReplaySession();
        openedAt.t = Date.now();
        track('app_open', { cold: false, ...device() }, path.current || '/');
        replaySoon(800);
        current.current = { screen: path.current || '/', since: Date.now() };
      } else if (last === 'active') {
        close();
        closedAt.t = Date.now();
        track('app_close', { seconds_open: Math.round((Date.now() - openedAt.t) / 1000) }, path.current || '/');
        flush();
      }
      last = state;
    });
    // Finished views go up once a minute. (Going to the background, which comes before any force-quit, closes the open one.)
    const timer = setInterval(() => {
      flush();
    }, 60_000);
    return () => {
      sub.remove();
      clearInterval(timer);
      close();
      flush();
    };
  }, [enabled]);
}

// Sends one crash or screen error to the server log. Quiet if offline or signed out.
export function reportCrash(error: unknown, screen?: string) {
  const e = error as { message?: string; stack?: string } | undefined;
  apiSend('POST', '/me/crash', {
    message: e?.message ?? String(error),
    stack: e?.stack ?? null,
    screen: screen ?? null,
    build,
    version,
    os: `${Platform.OS} ${Platform.Version}`,
  }).catch(() => undefined);
}

let installed = false;

// Catches errors nothing else handled (outside any screen), reports them, then lets React Native carry on as before.
export function installCrashReporter() {
  if (installed) return;
  installed = true;
  const g = globalThis as { ErrorUtils?: { getGlobalHandler: () => (e: unknown, fatal?: boolean) => void; setGlobalHandler: (h: (e: unknown, fatal?: boolean) => void) => void } };
  const utils = g.ErrorUtils;
  if (!utils) return;
  const previous = utils.getGlobalHandler();
  utils.setGlobalHandler((error, fatal) => {
    reportCrash(error, fatal ? 'fatal' : 'uncaught');
    previous(error, fatal);
  });
}
