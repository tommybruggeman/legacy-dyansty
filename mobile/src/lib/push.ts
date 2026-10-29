import Constants from 'expo-constants';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { router } from 'expo-router';
import { Platform } from 'react-native';

import { apiSend } from './api';

// Alerts show while the app is open too.
Notifications.setNotificationHandler({
  handleNotification: async () => ({ shouldShowBanner: true, shouldShowList: true, shouldPlaySound: true, shouldSetBadge: false }),
});

// Ask once for permission and hand this phone's Expo push token to the server, so league alerts
// (a completed trade, for now) reach it. Quietly does nothing on a simulator or if the owner says no.
export async function registerForPush(): Promise<void> {
  if (!Device.isDevice) return;
  try {
    let { status } = await Notifications.getPermissionsAsync();
    if (status !== 'granted') status = (await Notifications.requestPermissionsAsync()).status;
    if (status !== 'granted') return;
    const projectId = Constants.expoConfig?.extra?.eas?.projectId;
    const { data: token } = await Notifications.getExpoPushTokenAsync({ projectId });
    await apiSend('POST', '/me/push-token', { token, platform: Platform.OS });
  } catch {
    // Alerts are a nice-to-have: never block the app on them.
  }
}

// Tapping an alert opens the page it's about (data.url, e.g. League > Activity).
export function listenForTaps(): () => void {
  const open = (response: Notifications.NotificationResponse | null) => {
    const url = response?.notification.request.content.data?.url;
    if (typeof url === 'string' && url) router.push(url as never);
  };
  Notifications.getLastNotificationResponseAsync().then(open).catch(() => {});
  const sub = Notifications.addNotificationResponseReceivedListener(open);
  return () => sub.remove();
}
