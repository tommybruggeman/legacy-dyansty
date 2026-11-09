import AsyncStorage from '@react-native-async-storage/async-storage';
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { AppState, Pressable, StyleSheet, Text, View } from 'react-native';

import { colors } from './theme';

// Loaded only when used, inside try/catch, so a problem with the native Face ID module can never stop the app opening.
type LocalAuth = typeof import('expo-local-authentication');
let localAuth: LocalAuth | null | undefined;
function auth(): LocalAuth | null {
  if (localAuth === undefined) {
    try {
      localAuth = require('expo-local-authentication') as LocalAuth;
    } catch {
      localAuth = null;
    }
  }
  return localAuth;
}

// Optional Face ID (or Touch ID / passcode) to open the app, switched on in Settings > Account. It asks when the
// app opens and when it comes back after more than a minute away, so quick app switches don't nag.
const KEY = 'app-lock:on';
const GRACE_MS = 60_000;

type Lock = { available: boolean; label: string; enabled: boolean; setEnabled: (on: boolean) => void };
const LockContext = createContext<Lock>({ available: false, label: 'Face ID', enabled: false, setEnabled: () => undefined });

export function AppLockProvider({ children, active }: { children: ReactNode; active: boolean }) {
  const [available, setAvailable] = useState(false);
  const [label, setLabel] = useState('Face ID');
  const [enabled, setEnabledState] = useState(false);
  const [locked, setLocked] = useState(false);
  const leftAt = useRef<number | null>(null);

  const unlock = useCallback(async () => {
    const la = auth();
    if (!la) return setLocked(false);
    const result = await la.authenticateAsync({ promptMessage: 'Open Legacy Dynasty' }).catch(() => null);
    if (result?.success) setLocked(false);
  }, []);

  useEffect(() => {
    (async () => {
      const la = auth();
      if (!la) return;
      const hardware = await la.hasHardwareAsync().catch(() => false);
      const enrolled = hardware && (await la.isEnrolledAsync().catch(() => false));
      setAvailable(!!enrolled);
      const types = await la.supportedAuthenticationTypesAsync().catch(() => [] as number[]);
      if (!types.includes(la.AuthenticationType.FACIAL_RECOGNITION)) setLabel('Touch ID');
      const on = (await AsyncStorage.getItem(KEY).catch(() => null)) === '1';
      setEnabledState(on && !!enrolled);
      if (on && enrolled) setLocked(true);
    })().catch(() => undefined);
  }, []);

  useEffect(() => {
    const sub = AppState.addEventListener('change', (state) => {
      if (state === 'background') leftAt.current = Date.now();
      if (state === 'active' && enabled && leftAt.current && Date.now() - leftAt.current > GRACE_MS) setLocked(true);
    });
    return () => sub.remove();
  }, [enabled]);

  useEffect(() => {
    if (locked && active) unlock();
  }, [locked, active, unlock]);

  const setEnabled = useCallback(async (on: boolean) => {
    if (on) {
      const result = await auth()?.authenticateAsync({ promptMessage: 'Turn on to open Legacy Dynasty' }).catch(() => null);
      if (!result?.success) return;
    }
    setEnabledState(on);
    AsyncStorage.setItem(KEY, on ? '1' : '0').catch(() => undefined);
  }, []);

  return (
    <LockContext.Provider value={{ available, label, enabled, setEnabled }}>
      {children}
      {locked && active ? (
        <View style={styles.cover}>
          <Text style={styles.title}>Legacy Dynasty</Text>
          <Pressable style={styles.button} onPress={unlock}>
            <Text style={styles.buttonText}>Unlock with {label}</Text>
          </Pressable>
        </View>
      ) : null}
    </LockContext.Provider>
  );
}

export const useAppLock = () => useContext(LockContext);

const styles = StyleSheet.create({
  cover: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: colors.bg, alignItems: 'center', justifyContent: 'center', zIndex: 90, gap: 20 },
  title: { color: colors.gold, fontSize: 26, fontWeight: '800' },
  button: { backgroundColor: colors.gold, borderRadius: 12, paddingHorizontal: 22, paddingVertical: 12 },
  buttonText: { color: colors.bg, fontWeight: '800', fontSize: 15 },
});
