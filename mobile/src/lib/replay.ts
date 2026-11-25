import { Dimensions, Keyboard } from 'react-native';
import { captureRef, captureScreen } from 'react-native-view-shot';

import { supabase } from './supabase';

// Session replays, our own way: a small, low-quality picture of the screen after each screen change, tap and
// swipe, saved to the private 'replays' bucket in our database (only the league owner's account can look).
// Played back in order with the tap spots, it shows what each owner saw and did.
// Nothing is captured while the keyboard is up, so typed text isn't recorded. Never blocks or breaks the app.

const WIDTH = 300; // pixels wide; about 15 KB a picture
const MIN_GAP_MS = 1200;
const MAX_PER_SESSION = 200;

let session = '';
let count = 0;
let lastAt = 0;
let timer: ReturnType<typeof setTimeout> | null = null;
let busy = false;
let report: ((path: string) => void) | null = null;
let reportPage: ((path: string, screen: string, h: number) => void) | null = null;
let userId: string | null = null;
let paused = false;

// The Owner activity console turns pictures off while it's open, so it never records itself.
export function pauseReplays(on: boolean) {
  paused = on;
}

export function setFrameReporter(fn: (path: string) => void) {
  report = fn;
}

export function setPageShotReporter(fn: (path: string, screen: string, h: number) => void) {
  reportPage = fn;
}

export function newReplaySession() {
  session = `${Date.now()}`;
  count = 0;
  supabase.auth.getSession().then(({ data }) => {
    userId = data.session?.user?.id ?? null;
  }).catch(() => undefined);
}

// A picture shortly after something changed (waits for the screen to settle).
export function replaySoon(delayMs = 600) {
  if (!session) return;
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    timer = null;
    capture().catch(() => undefined);
  }, delayMs);
}

async function capture() {
  if (paused || busy || !userId || !session || count >= MAX_PER_SESSION) return;
  if (Date.now() - lastAt < MIN_GAP_MS) {
    replaySoon(MIN_GAP_MS - (Date.now() - lastAt));
    return;
  }
  if (Keyboard.isVisible()) return;
  busy = true;
  lastAt = Date.now();
  try {
    const { width, height } = Dimensions.get('window');
    const base64 = await captureScreen({ format: 'jpg', quality: 0.35, result: 'base64', width: WIDTH, height: Math.round((WIDTH * height) / Math.max(1, width)) });
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    count += 1;
    const path = `${userId}/${session}/${String(count).padStart(3, '0')}.jpg`;
    const { error } = await supabase.storage.from('replays').upload(path, bytes, { contentType: 'image/jpeg' });
    if (!error && report) report(path);
  } catch {
    // no picture this time
  } finally {
    busy = false;
  }
}

// One picture of a whole page, top to bottom (the scroll view's full content), so the Owner activity heat map can
// lay taps and scrolling over the real page. At most once a week per page, a few per visit, never with the keyboard up.
const PAGE_SHOTS_PER_VISIT = 6;
const MAX_SCREENS_TALL = 8;
const WEEK_MS = 7 * 24 * 3600 * 1000;
const shotAt = new Map<string, number>();
let shotsThisVisit = 0;

export async function pageShot(view: unknown, screen: string, contentH: number, viewH: number, stillOn: () => boolean) {
  if (paused || busy || !userId || !session || !view || !contentH || !viewH) return;
  if (shotsThisVisit >= PAGE_SHOTS_PER_VISIT || contentH / viewH > MAX_SCREENS_TALL) return;
  if (Date.now() - (shotAt.get(screen) ?? 0) < WEEK_MS) return;
  if (Keyboard.isVisible() || !stillOn()) return;
  busy = true;
  try {
    shotAt.set(screen, Date.now());
    shotsThisVisit += 1;
    const { width } = Dimensions.get('window');
    const base64 = await captureRef(view as never, {
      format: 'jpg', quality: 0.35, result: 'base64', snapshotContentContainer: true,
      width: WIDTH, height: Math.round((WIDTH * contentH) / Math.max(1, width)),
    });
    if (!stillOn()) return;
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    const path = `${userId}/pages/${Date.now()}.jpg`;
    const { error } = await supabase.storage.from('replays').upload(path, bytes, { contentType: 'image/jpeg' });
    if (!error && reportPage) reportPage(path, screen, Math.round((contentH / viewH) * 100) / 100);
  } catch {
    // no picture this time
  } finally {
    busy = false;
  }
}
