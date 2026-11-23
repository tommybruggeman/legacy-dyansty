import { useEffect, useState } from 'react';
import { ActivityIndicator, KeyboardAvoidingView, Modal, Platform, StyleSheet, Text, TextInput, View } from 'react-native';

import { apiSend } from '../lib/api';
import { onPulse } from '../lib/pulse';
import { currentPage, track } from '../lib/telemetry';
import { colors } from '../lib/theme';
import { Pressable } from './Pressable';

// Feedback from owners, two ways: Settings > Send feedback, and the weekly check-in pop-up (lib/pulse.ts).
// Each note is tied to the moment it was sent, so Owner activity shows it inside that session's replay.

const FACES = ['1', '2', '3', '4', '5'];

export function FeedbackSheet({ open, kind, onClose }: { open: boolean; kind: 'button' | 'pulse'; onClose: () => void }) {
  const [rating, setRating] = useState<number | null>(null);
  const [text, setText] = useState('');
  const [state, setState] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle');
  const [page, setPage] = useState('/');

  useEffect(() => {
    if (!open) return;
    setRating(null);
    setText('');
    setState('idle');
    setPage(currentPage());
    if (kind === 'pulse') track('feedback', { kind: 'pulse_shown' });
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const send = async () => {
    if (!text.trim() && rating == null) return;
    setState('sending');
    try {
      await apiSend('POST', '/me/feedback', { kind, rating, text: text.trim(), screen: page, at: new Date().toISOString() });
      setState('sent');
      setTimeout(onClose, 1200);
    } catch {
      setState('error');
    }
  };

  return (
    <Modal visible={open} transparent animationType="fade" onRequestClose={onClose}>
      <KeyboardAvoidingView style={s.backdrop} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={s.card}>
          <Text style={s.title}>{kind === 'pulse' ? 'Quick check-in' : 'Send feedback'}</Text>
          <Text style={s.sub}>
            {kind === 'pulse' ? 'How useful was the app for you this week?' : 'A bug, an idea, anything. It goes straight to the commissioner.'}
          </Text>
          {kind === 'pulse' ? (
            <View style={s.scale}>
              {FACES.map((f, i) => (
                <Pressable key={f} onPress={() => setRating(i + 1)} style={[s.dot, rating === i + 1 && s.dotOn]} accessibilityLabel={`${i + 1} out of 5`}>
                  <Text style={[s.dotText, rating === i + 1 && s.dotTextOn]}>{f}</Text>
                </Pressable>
              ))}
            </View>
          ) : null}
          {kind === 'pulse' ? <View style={s.ends}><Text style={s.end}>Not useful</Text><Text style={s.end}>Couldn't live without it</Text></View> : null}
          <TextInput
            value={text}
            onChangeText={setText}
            placeholder={kind === 'pulse' ? 'Why? What would make it better? (optional)' : 'What happened, or what would you like?'}
            placeholderTextColor={colors.muted}
            multiline
            maxLength={2000}
            style={s.input}
          />
          {state === 'error' ? <Text style={s.error}>Couldn't send. Try again in a moment.</Text> : null}
          <View style={s.row}>
            <Pressable onPress={() => { if (kind === 'pulse' && state !== 'sent') track('feedback', { kind: 'pulse_skipped' }); onClose(); }} hitSlop={8} style={s.later}>
              <Text style={s.laterText}>{kind === 'pulse' ? 'Not now' : 'Cancel'}</Text>
            </Pressable>
            <Pressable onPress={send} style={[s.send, !text.trim() && rating == null && s.sendOff]} disabled={state === 'sending' || state === 'sent'}>
              {state === 'sending' ? <ActivityIndicator color={colors.bg} /> : <Text style={s.sendText}>{state === 'sent' ? 'Thanks!' : 'Send'}</Text>}
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

// Lives once in the app shell and opens the check-in when lib/pulse.ts says it's time.
export function PulseHost() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    onPulse(() => setOpen(true));
    return () => onPulse(null);
  }, []);
  return <FeedbackSheet open={open} kind="pulse" onClose={() => setOpen(false)} />;
}

const s = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'center', padding: 20 },
  card: { backgroundColor: colors.card, borderRadius: 18, borderWidth: 1, borderColor: colors.line, padding: 18 },
  title: { color: colors.text, fontSize: 19, fontWeight: '800' },
  sub: { color: colors.muted, fontSize: 14, marginTop: 4 },
  scale: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 16 },
  dot: { width: 48, height: 48, borderRadius: 24, borderWidth: 1, borderColor: colors.line, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.row },
  dotOn: { backgroundColor: colors.gold, borderColor: colors.gold },
  dotText: { color: colors.text, fontSize: 17, fontWeight: '700' },
  dotTextOn: { color: colors.bg },
  ends: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 6 },
  end: { color: colors.muted, fontSize: 11 },
  input: { marginTop: 14, minHeight: 84, maxHeight: 180, borderRadius: 12, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.row,
    color: colors.text, padding: 12, fontSize: 15, textAlignVertical: 'top' },
  error: { color: colors.danger, marginTop: 8 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-end', gap: 16, marginTop: 14 },
  later: { paddingVertical: 10 },
  laterText: { color: colors.muted, fontSize: 15, fontWeight: '600' },
  send: { backgroundColor: colors.gold, borderRadius: 12, paddingHorizontal: 22, paddingVertical: 11, minWidth: 92, alignItems: 'center' },
  sendOff: { opacity: 0.5 },
  sendText: { color: colors.bg, fontSize: 15, fontWeight: '800' },
});
