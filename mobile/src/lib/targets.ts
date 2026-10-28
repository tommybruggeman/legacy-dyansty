import { useCallback, useEffect, useState } from 'react';
import { Alert } from 'react-native';

import { apiGet, apiSend } from './api';
import { supabase } from './supabase';

// Transfer targets: players the owner saved with ☆. One list shared by every
// screen that shows a star, so saving on one page shows everywhere at once.
type Saved = { ids: string[]; ready: boolean };

let saved: Saved | null = null;
let loading: Promise<void> | null = null;
const listeners = new Set<(s: Saved) => void>();
const savedListeners = new Set<() => void>(); // told after the server confirms a change

function publish(next: Saved) {
  saved = next;
  listeners.forEach((l) => l(next));
}

function load() {
  loading ??= apiGet<Saved>('/market/targets')
    .then(publish)
    .catch(() => undefined)
    .finally(() => { loading = null; });
  return loading;
}

supabase.auth.onAuthStateChange((event) => {
  if (event === 'SIGNED_OUT') saved = null;
});

// onSaved runs after any screen saves or removes a target, e.g. to reload a list that shows them.
export function useTargets(onSaved?: () => void) {
  const [state, setState] = useState<Saved>(saved ?? { ids: [], ready: true });

  useEffect(() => {
    listeners.add(setState);
    if (saved) setState(saved);
    else load();
    return () => { listeners.delete(setState); };
  }, []);

  useEffect(() => {
    if (!onSaved) return undefined;
    savedListeners.add(onSaved);
    return () => { savedListeners.delete(onSaved); };
  }, [onSaved]);

  const toggle = useCallback(async (playerId: string) => {
    const before = saved ?? state;
    const on = before.ids.includes(playerId);
    publish({ ...before, ids: on ? before.ids.filter((id) => id !== playerId) : [playerId, ...before.ids] });
    try {
      publish(await apiSend<Saved>(on ? 'DELETE' : 'POST', `/market/targets/${encodeURIComponent(playerId)}`));
      savedListeners.forEach((l) => l());
    } catch (e: any) {
      publish(before);
      Alert.alert('Not saved', e?.message ?? 'Try again.');
    }
  }, [state]);

  return { ids: state.ids, isTarget: (id: string) => state.ids.includes(id), toggle };
}
