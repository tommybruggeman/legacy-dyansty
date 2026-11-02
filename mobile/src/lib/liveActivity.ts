// Starts, refreshes and ends the lock screen Live Activity for my live matchup.
//
// While the app is open it starts the activity (when one of the starters in my game is playing or kicks
// off within the hour) and hands its push token to the server. From then on the game-day watcher pushes
// score updates through Apple, so the lock screen keeps moving with the app closed. iPhone only.
import { Platform } from 'react-native';

import { apiGet, apiSend } from './api';
import type { LiveMatchupProps } from '../live/LiveMatchupActivity';

type LiveNow = { live: boolean; week?: number; matchup_id?: number; url?: string; props?: LiveMatchupProps | null };

const MIN_GAP_MS = 60_000;
let lastRun = 0;
let running = false;
const sentTokens = new Set<string>();
const listening = new Set<string>();

function factory() {
  if (Platform.OS !== 'ios') return null;
  try {
    // Loaded lazily so the website preview and Android never touch the native module.
    return require('../live/LiveMatchupActivity').default as typeof import('../live/LiveMatchupActivity').default;
  } catch {
    return null;
  }
}

async function saveToken(activityId: string, token: string | null, week: number, matchupId: number) {
  if (!token || sentTokens.has(token)) return;
  try {
    await apiSend('POST', '/me/live-activity', { activity_id: activityId, push_token: token, week, matchup_id: matchupId });
    sentTokens.add(token);
  } catch {
    // The server can't take it yet (table not set up): the activity still works while the app is open.
  }
}

export async function syncLiveActivity(force = false): Promise<void> {
  const Live = factory();
  if (!Live || running) return;
  if (!force && Date.now() - lastRun < MIN_GAP_MS) return;
  running = true;
  lastRun = Date.now();
  try {
    const now = await apiGet<LiveNow>('/live-activity');
    let instances = [] as ReturnType<typeof Live.getInstances>;
    try {
      instances = Live.getInstances();
    } catch {
      instances = [];
    }
    if (!now.props || !now.week || !now.matchup_id) return;
    const { week, matchup_id: matchupId, props } = now;

    if (!now.live) {
      if (props.final) {
        for (const a of instances) {
          await a.end('default', { ...props, final: true }).catch(() => undefined);
          apiSend('POST', `/me/live-activity/${encodeURIComponent(a.getId())}/end`).catch(() => undefined);
        }
      }
      return;
    }

    // Keep one running activity: refresh it if there is one, otherwise start it.
    let activity = instances[0];
    for (const extra of instances.slice(1)) await extra.end('immediate').catch(() => undefined);
    if (activity) {
      await activity.update(props).catch(() => undefined);
    } else {
      try {
        activity = Live.start(props, now.url);
      } catch {
        return; // Live Activities switched off for this app in iOS Settings
      }
    }
    const id = activity.getId();
    if (!listening.has(id)) {
      listening.add(id);
      activity.addPushTokenListener((event) => saveToken(event.activityId, event.pushToken, week, matchupId));
    }
    await saveToken(id, await activity.getPushToken().catch(() => null), week, matchupId);
  } catch {
    // Offline or signed out: try again next time.
  } finally {
    running = false;
  }
}
