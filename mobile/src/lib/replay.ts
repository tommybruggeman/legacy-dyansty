import { Dimensions, Keyboard } from 'react-native';
import { captureScreen } from 'react-native-view-shot';

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
let userId: string | null = null;

export function setFrameReporter(fn: (path: string) => void) {
  report = fn;
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
  if (busy || !userId || !session || count >= MAX_PER_SESSION) return;
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
    const { error } = await supabase.storage.from('replays').upload(path, bytes, { contentType: 'image/jpeg', upsert: true });
    if (!error && report) report(path);
  } catch {
    // no picture this time
  } finally {
    busy = false;
  }
}
