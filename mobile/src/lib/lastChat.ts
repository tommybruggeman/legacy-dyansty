import AsyncStorage from '@react-native-async-storage/async-storage';

// The Front Office chat you were last in, so reopening the app within
// 30 minutes drops you straight back into it (and picks up an answer
// that was still being written when you left).
const KEY = 'fo:last-chat';
export const BACK_WITHIN_MS = 30 * 60 * 1000;

export type LastChat = { id: string | null; pending: boolean; at: number };

export function saveLastChat(chat: Omit<LastChat, 'at'>) {
  AsyncStorage.setItem(KEY, JSON.stringify({ ...chat, at: Date.now() })).catch(() => undefined);
}

export function clearLastChat() {
  AsyncStorage.removeItem(KEY).catch(() => undefined);
}

export async function recentChat(): Promise<LastChat | null> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    const chat = raw ? (JSON.parse(raw) as LastChat) : null;
    return chat && Date.now() - chat.at < BACK_WITHIN_MS ? chat : null;
  } catch {
    return null;
  }
}
