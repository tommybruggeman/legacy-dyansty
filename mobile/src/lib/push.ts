// Push alerts are off until the iOS signing profile includes Push Notifications (Apple rejects the
// build otherwise). The full version (expo-notifications: ask permission, send the Expo token to
// POST /me/push-token, open data.url on tap) comes back with the next build that has the new profile.
export async function registerForPush(): Promise<void> {}

export function listenForTaps(): () => void {
  return () => {};
}
