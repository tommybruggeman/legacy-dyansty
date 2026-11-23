import Constants from 'expo-constants';
import { usePathname } from 'expo-router';
import { useEffect, useRef } from 'react';
import { AppState, Dimensions, type GestureResponderEvent, Keyboard, Linking, PixelRatio, Platform } from 'react-native';

import { apiSend, setLoadReporter } from './api';
import { noteVisit } from './pulse';
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
export function track(kind: string, detail?: Record<string, unknown>, screen?: string, at?: number) {
  push({ screen: screen ?? currentScreen, seconds: 0, at: new Date(at ?? Date.now()).toISOString(), kind, detail });
}

const r3 = (v: number) => Math.round(v * 1000) / 1000;
const r1 = (v: number) => Math.round(v * 10) / 10;

// --- Attention (no camera): which part of each page is on screen, for how long, and where scrolling stops.
// Pages are cut into 10 bands top to bottom; each second a band is visible counts toward it. A scroll that comes
// to rest for over a second marks the band in the middle of the screen as a stop. Sent with the screen view.
type Scroll = { screen: string; y: number; contentH: number; viewH: number; at: number; maxBottom: number; attn: number[]; stops: number[] };
let sc: Scroll | null = null;
let stopTimer: ReturnType<typeof setTimeout> | null = null;

function scrollFor(screen: string): Scroll {
  if (!sc || sc.screen !== screen) sc = { screen, y: 0, contentH: 0, viewH: 0, at: Date.now(), maxBottom: 0, attn: Array(10).fill(0), stops: Array(10).fill(0) };
  return sc;
}

function accrue(s: Scroll) {
  const now = Date.now();
  const dt = Math.min((now - s.at) / 1000, 120);
  s.at = now;
  if (dt <= 0 || s.contentH <= 0 || s.viewH <= 0) return;
  const top = Math.max(0, s.y) / s.contentH;
  const bottom = Math.min(s.contentH, Math.max(0, s.y) + s.viewH) / s.contentH;
  s.maxBottom = Math.max(s.maxBottom, bottom);
  for (let i = 0; i < 10; i++) {
    const overlap = Math.max(0, Math.min(bottom, (i + 1) / 10) - Math.max(top, i / 10));
    if (overlap > 0) s.attn[i] += dt * Math.min(1, overlap / 0.1);
  }
}

// Called by every page's scroll view (components/PullDown.tsx): where it is and how tall the page is.
export function noteScroll(m: { y?: number; contentH?: number; viewH?: number }) {
  try {
    const s = scrollFor(currentScreen);
    accrue(s);
    if (m.contentH) s.contentH = m.contentH;
    if (m.viewH) s.viewH = m.viewH;
    if (m.y == null) {
      s.maxBottom = Math.max(s.maxBottom, s.contentH ? Math.min(1, (Math.max(0, s.y) + s.viewH) / s.contentH) : 0);
      return;
    }
    s.y = m.y;
    if (stopTimer) clearTimeout(stopTimer);
    stopTimer = setTimeout(() => {
      if (sc !== s || !s.contentH) return;
      const mid = (Math.max(0, s.y) + s.viewH / 2) / s.contentH;
      s.stops[Math.max(0, Math.min(9, Math.floor(mid * 10)))] += 1;
    }, 1000);
  } catch {
    // never break scrolling
  }
}

function attentionDetail(screen: string): Record<string, unknown> | undefined {
  const s = sc;
  sc = null;
  if (!s || s.screen !== screen || !s.contentH || !s.viewH) return undefined;
  accrue(s);
  return {
    depth: Math.round(Math.max(s.maxBottom, Math.min(1, s.viewH / s.contentH)) * 100), // how far down they got, % of the page
    fold: Math.round(Math.min(1, s.viewH / s.contentH) * 100), // how much of the page fits on one screen
    attn: s.attn.map(r1).join(','), // seconds each tenth of the page was on screen
    stops: s.stops.join(','), // scroll stops in each tenth
  };
}

// --- Taps that go somewhere vs taps that don't. Our Pressable (components/Pressable.tsx) marks each press.
let lastPress = 0;
let navCount = 0;
export function markPress() {
  lastPress = Date.now();
}
let recentTaps: { x: number; y: number; t: number }[] = [];
let lastRage = 0;

// Screen load time: how long a page waited for its data the first time (no saved copy to show).
const apiName = (api: string) => api.split('?')[0].replace(/\/\d+(?=\/|$)/g, '/[id]').slice(0, 100);
export function trackLoad(api: string, ms: number, failed?: string) {
  if (failed != null) {
    // A page that couldn't get its data: offline, or the server said no.
    track('fail', { api: apiName(api), ms: Math.round(ms), offline: /network|internet|offline|timed out/i.test(failed), error: failed.slice(0, 80) });
    return;
  }
  track('load', { api: apiName(api), ms: Math.round(ms) });
}

// Links that leave the app (Open in Sleeper, website, privacy...). Shows what we still send people elsewhere for.
let outboundInstalled = false;
function installOutbound() {
  if (outboundInstalled) return;
  outboundInstalled = true;
  try {
    const open = Linking.openURL.bind(Linking);
    (Linking as { openURL: (url: string) => Promise<unknown> }).openURL = (url: string) => {
      try {
        const u = String(url);
        track('outbound', { url: u.replace(/^https?:\/\//, '').slice(0, 110), to: u.includes('sleeper.com') ? 'sleeper' : u.split('/')[2] ?? u.slice(0, 30) });
      } catch {
        // still open it
      }
      return open(url);
    };
  } catch {
    // tracking off for links
  }
}

export function currentPage() {
  return currentScreen;
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
        const x = r3(pageX / Math.max(1, width));
        const y = r3(pageY / Math.max(1, height));
        const t = Date.now();
        const screen = currentScreen;
        const nav = navCount;
        const keyboard = Keyboard.isVisible();
        const scroll = sc && sc.screen === screen && sc.contentH ? sc : null;
        const py = scroll ? r3(Math.min(1, (pageY + Math.max(0, scroll.y)) / scroll.contentH)) : undefined;
        // Rage taps: three or more quick taps in about the same spot.
        recentTaps = [...recentTaps.filter((p) => t - p.t < 1000), { x, y, t }];
        const near = recentTaps.filter((p) => Math.abs(p.x - x) < 0.08 && Math.abs(p.y - y) < 0.05);
        if (near.length >= 3 && t - lastRage > 1500) {
          lastRage = t;
          track('rage', { x, y, py, n: near.length }, screen, t);
        }
        // A second later, say whether it did anything: opened a page, hit a button, opened the keyboard, or nothing.
        setTimeout(() => {
          const went = navCount !== nav || currentScreen !== screen ? 'page'
            : lastPress >= start.t - 50 && lastPress <= t + 900 ? 'press'
              : !keyboard && Keyboard.isVisible() ? 'input' : 'none';
          track('tap', { x, y, py, went, ms }, screen, t);
        }, 1000);
        replaySoon(600);
      } else {
        const dir = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 'right' : 'left') : dy > 0 ? 'down' : 'up';
        track('swipe', { dir, x: r(start.x, width), y: r(start.y, height), to_x: r(pageX, width), to_y: r(pageY, height), ms,
          py: sc && sc.screen === currentScreen && sc.contentH ? r3(Math.min(1, (start.y + Math.max(0, sc.y)) / sc.contentH)) : undefined });
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
    const attention = attentionDetail(open.screen);
    // Pages about one thing keep which one (a matchup, a player), so "most watched" works.
    const which = /\d/.test(open.screen) ? { path: open.screen.slice(0, 100) } : undefined;
    const detail = attention || which ? { ...attention, ...which } : undefined;
    if (seconds >= 1) push({ screen: open.screen, seconds, at: new Date(open.since).toISOString(), detail });
    current.current = null;
  };

  useEffect(() => {
    if (!enabled) return;
    close();
    navCount += 1;
    currentScreen = pathname || '/';
    current.current = { screen: currentScreen, since: Date.now() };
    replaySoon(700);
  }, [pathname, enabled]);

  useEffect(() => {
    if (!enabled) return;
    const openedAt = { t: Date.now() };
    const closedAt = { t: Date.now() };
    setFrameReporter((p) => track('frame', { path: p }));
    setLoadReporter(trackLoad);
    newReplaySession();
    noteVisit();
    track('app_open', { cold: true, ...device() }, path.current || '/');
    let last = AppState.currentState;
    const sub = AppState.addEventListener('change', (state) => {
      if (state === last) return;
      if (state === 'active') {
        // Back within 30 minutes continues the same replay; longer starts a new one.
        if (Date.now() - closedAt.t > 30 * 60_000) {
          newReplaySession();
          noteVisit();
        }
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
  installOutbound();
  const g = globalThis as { ErrorUtils?: { getGlobalHandler: () => (e: unknown, fatal?: boolean) => void; setGlobalHandler: (h: (e: unknown, fatal?: boolean) => void) => void } };
  const utils = g.ErrorUtils;
  if (!utils) return;
  const previous = utils.getGlobalHandler();
  utils.setGlobalHandler((error, fatal) => {
    reportCrash(error, fatal ? 'fatal' : 'uncaught');
    previous(error, fatal);
  });
}
