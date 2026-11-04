import * as Notifications from 'expo-notifications';
import { router } from 'expo-router';
import { Platform } from 'react-native';

import { apiSend } from './api';

// League alerts come straight from our server through Apple (no Expo push service): this phone's Apple device
// token goes to POST /me/push-token with the phone's time zone (for quiet hours, 11 PM to 8 AM).

// Alerts show while the app is open too.
Notifications.setNotificationHandler({
  handleNotification: async () => ({ shouldShowBanner: true, shouldShowList: true, shouldPlaySound: true, shouldSetBadge: false }),
});

function timeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return undefined;
  }
}

// Ask once for permission and hand this phone's token to the server. Quietly does nothing if the owner says no.
export async function registerForPush(): Promise<void> {
  if (Platform.OS !== 'ios') return;
  try {
    let { status } = await Notifications.getPermissionsAsync();
    if (status !== 'granted') status = (await Notifications.requestPermissionsAsync()).status;
    if (status !== 'granted') return;
    const { data: token } = await Notifications.getDevicePushTokenAsync();
    if (typeof token !== 'string' || !token) return;
    await apiSend('POST', '/me/push-token', { token, platform: Platform.OS, tz: timeZone() });
  } catch {
    // Alerts are a nice-to-have: never block the app on them.
  }
}

export async function alertsAllowed(): Promise<boolean> {
  try {
    return (await Notifications.getPermissionsAsync()).status === 'granted';
  } catch {
    return false;
  }
}

function urlOf(response: Notifications.NotificationResponse | null): string | null {
  if (!response) return null;
  const { content, trigger } = response.notification.request;
  const fromData = (content.data as { url?: unknown } | undefined)?.url;
  if (typeof fromData === 'string' && fromData) return fromData;
  const payload = (trigger as { payload?: { url?: unknown; body?: { url?: unknown } } } | null)?.payload;
  const raw = payload?.url ?? payload?.body?.url;
  return typeof raw === 'string' && raw ? raw : null;
}

// Tapping an alert opens the page it's about (lineup, box score, Activity, Transactions...).
export function listenForTaps(): () => void {
  const open = (response: Notifications.NotificationResponse | null) => {
    const url = urlOf(response);
    if (url) router.push(url as never);
  };
  Notifications.getLastNotificationResponseAsync().then(open).catch(() => {});
  const sub = Notifications.addNotificationResponseReceivedListener(open);
  return () => sub.remove();
}
