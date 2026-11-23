import { type ComponentType, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { WebView } from 'react-native-webview';

import { Back } from '../components/ui';
import { pauseReplays } from '../lib/replay';
import { supabase } from '../lib/supabase';
import { colors } from '../lib/theme';

// Owner activity, only for the league owner's account: the console (charts, flows, funnels, retention, owners,
// session replays, saved reports and the AI analyst). It's one web page on our server (api/console), shown here
// and on the website, so it can change without a new app build. The page asks the server with this account's
// sign-in, and the server and database only answer accounts in app_activity_viewers.

// react-native-webview 14's class typings don't line up with React 19's; the props below are its documented ones.
const Web = WebView as unknown as ComponentType<Record<string, unknown>>;

const API_URL = (process.env.EXPO_PUBLIC_API_URL ?? '').replace(/\/$/, '');

function tz() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return 'America/Denver';
  }
}

export default function OwnerActivity() {
  const insets = useSafeAreaInsets();
  const web = useRef<{ injectJavaScript: (js: string) => void } | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    pauseReplays(true);
    let alive = true;
    const hand = async (first: boolean) => {
      const { data } = await supabase.auth.getSession();
      const t = data.session?.access_token ?? null;
      if (!alive || !t) return;
      if (first) setToken(t);
      else web.current?.injectJavaScript(`window.legacySetToken && window.legacySetToken(${JSON.stringify(t)}); true;`);
    };
    hand(true).catch(() => setFailed(true));
    const every = setInterval(() => hand(false).catch(() => undefined), 5 * 60 * 1000); // sign-ins last an hour; keep the page's fresh
    return () => {
      alive = false;
      clearInterval(every);
      pauseReplays(false);
    };
  }, []);

  const boot = token ? `window.LEGACY = ${JSON.stringify({ token, api: API_URL, tz: tz() })}; true;` : '';

  return (
    <View style={[s.wrap, { paddingTop: insets.top }]}>
      <View style={s.bar}>
        <Back label="League tools" />
      </View>
      {failed ? (
        <Text style={s.note}>Couldn't open Owner activity. Sign in again and try once more.</Text>
      ) : !token ? (
        <ActivityIndicator color={colors.gold} style={{ marginTop: 40 }} />
      ) : (
        <Web
          ref={web}
          source={{ uri: `${API_URL}/console` }}
          injectedJavaScriptBeforeContentLoaded={boot}
          style={s.web}
          containerStyle={s.web}
          originWhitelist={['https://*']}
          setSupportMultipleWindows={false}
          allowsBackForwardNavigationGestures={false}
          startInLoadingState
          renderLoading={() => <ActivityIndicator color={colors.gold} style={s.loading} />}
          onError={() => setFailed(true)}
        />
      )}
    </View>
  );
}

const s = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: '#06140E' },
  bar: { paddingHorizontal: 16, paddingBottom: 4 },
  web: { flex: 1, backgroundColor: '#06140E' },
  loading: { position: 'absolute', top: 40, left: 0, right: 0 },
  note: { color: colors.muted, padding: 16 },
});
