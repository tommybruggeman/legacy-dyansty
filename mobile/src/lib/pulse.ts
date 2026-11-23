import AsyncStorage from '@react-native-async-storage/async-storage';
import { AppState } from 'react-native';

import { apiGet } from './api';

// The weekly check-in: on an owner's 3rd visit of each week (Monday to Sunday), during the season only, a
// feedback pop-up appears after 5 seconds of use. Asked at most once a week. A visit is opening the app,
// or coming back after 30 minutes away (the same thing that starts a new session in Owner activity).

const KEY = 'pulse.v1';
const VISIT = 3;
const AFTER_MS = 5000;

let show: (() => void) | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;

export function onPulse(fn: (() => void) | null) {
  show = fn;
}

function weekKey(d = new Date()) {
  const monday = new Date(d);
  monday.setHours(12, 0, 0, 0);
  monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
  return monday.toISOString().slice(0, 10);
}

export async function noteVisit() {
  try {
    const week = weekKey();
    const saved = JSON.parse((await AsyncStorage.getItem(KEY)) ?? '{}') as { week?: string; visits?: number; asked?: string };
    const visits = saved.week === week ? (saved.visits ?? 0) + 1 : 1;
    await AsyncStorage.setItem(KEY, JSON.stringify({ week, visits, asked: saved.asked }));
    if (visits !== VISIT || saved.asked === week) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(async () => {
      timer = null;
      if (AppState.currentState !== 'active' || !show) return;
      try {
        const { ask } = await apiGet<{ ask: boolean }>('/me/feedback/pulse');
        if (!ask) return;
      } catch {
        return;
      }
      const now = JSON.parse((await AsyncStorage.getItem(KEY)) ?? '{}');
      await AsyncStorage.setItem(KEY, JSON.stringify({ ...now, asked: week }));
      show?.();
    }, AFTER_MS);
  } catch {
    // never in the way
  }
}
