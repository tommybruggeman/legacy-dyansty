import Constants from 'expo-constants';
import { usePathname } from 'expo-router';
import { useEffect, useRef } from 'react';
import { AppState, Platform } from 'react-native';

import { apiSend } from './api';

// Our own usage numbers and crash reports. Both go to the Legacy API (stored in our database / server log);
// no outside analytics or crash service. Nothing here may ever block or break the app.

const build = String(Constants.expoConfig?.ios?.buildNumber ?? '');
const version = String(Constants.expoConfig?.version ?? '');

type View = { screen: string; seconds: number; at: string };
let queue: View[] = [];
let sending = false;

async function flush() {
  if (sending || queue.length === 0) return;
  sending = true;
  const batch = queue;
  queue = [];
  try {
    await apiSend('POST', '/me/activity', { views: batch, build });
  } catch {
    // Offline: keep the views for next time (capped so a long offline stretch can't grow forever).
    queue = [...batch, ...queue].slice(-200);
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
    if (seconds >= 1) queue.push({ screen: open.screen, seconds, at: new Date(open.since).toISOString() });
    current.current = null;
  };

  useEffect(() => {
    if (!enabled) return;
    close();
    current.current = { screen: pathname || '/', since: Date.now() };
  }, [pathname, enabled]);

  useEffect(() => {
    if (!enabled) return;
    const sub = AppState.addEventListener('change', (state) => {
      if (state === 'active') {
        current.current = { screen: path.current || '/', since: Date.now() };
      } else {
        close();
        flush();
      }
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
