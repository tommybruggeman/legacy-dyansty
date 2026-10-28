import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator, KeyboardAvoidingView, Linking, Platform, Pressable, ScrollView, type ScrollViewInstance, StyleSheet, Text, TextInput, View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Markdown } from '../../components/frontoffice/Markdown';
import { ErrorState, Loading } from '../../components/ScreenState';
import { Pill } from '../../components/ui';
import { type ChatMessage, type ChatReply, type Conversation, type FoTile, type FrontOffice, apiGet, apiSend, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

// The Assistant GM: this week's tiles and brief, starter questions, then the
// chat. Trades are made in Sleeper, so trade answers link straight there.
export default function FrontOfficeScreen() {
  const { c } = useLocalSearchParams<{ c?: string }>();
  const { data, error, loading, reload } = useApi<FrontOffice>('/front-office');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [thinking, setThinking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [typing, setTyping] = useState(false);
  const scroll = useRef<ScrollViewInstance>(null);
  const insets = useSafeAreaInsets();

  // Opening a past chat (c=id) or starting a new one (c=new) from the chats list.
  useEffect(() => {
    if (!c) return;
    setProblem(null);
    if (c === 'new') {
      setMessages([]);
      setConversationId(null);
      return;
    }
    apiGet<Conversation>(`/front-office/conversations/${c}`)
      .then((convo) => { setMessages(convo.messages); setConversationId(convo.id); })
      .catch((e) => setProblem(e?.message ?? 'Couldn’t open that chat.'));
  }, [c]);

  const send = async (text: string) => {
    const question = text.trim();
    if (!question || thinking) return;
    setDraft('');
    setProblem(null);
    setMessages((m) => [...m, { role: 'user', content: question }]);
    setThinking(true);
    try {
      const reply = await apiSend<ChatReply>('POST', '/front-office/chat', { question, conversation_id: conversationId });
      setMessages((m) => [...m, { role: 'assistant', content: reply.text }]);
      if (reply.conversation_id) setConversationId(reply.conversation_id);
    } catch (e: any) {
      setProblem(e?.message ?? 'Something went wrong.');
    } finally {
      setThinking(false);
    }
  };

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  // Once you tap the box (or the chat has started) the tiles, brief and
  // starter chips step aside and it's just you and the GM.
  const focused = typing || messages.length > 0 || thinking;
  const sleeper = data.sleeper_league_id ? `https://sleeper.com/leagues/${data.sleeper_league_id}` : null;
  const lastQuestion = [...messages].reverse().find((m) => m.role === 'user')?.content ?? '';

  return (
    <KeyboardAvoidingView style={styles.screen} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView
        ref={scroll}
        contentContainerStyle={[styles.content, { paddingTop: insets.top + 8 }]}
        onContentSizeChange={() => messages.length && scroll.current?.scrollToEnd({ animated: true })}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.header}>
          <Text style={styles.title}>Front Office</Text>
          <Pill label="ASSISTANT GM" tone="gold" />
          <Pressable onPress={() => router.push('/chats')} hitSlop={10} style={styles.menu}>
            <Text style={styles.menuText}>☰</Text>
          </Pressable>
        </View>

        {!focused ? (
          <>
            <Text style={styles.greeting}>{data.greeting} What are we working on?</Text>
            <Tiles tiles={data.tiles} />
            <Brief data={data} />
            <Pressable onPress={() => router.push('/report-card')} style={styles.cardRow}>
              <Text style={styles.cardRowText}>GM report card</Text>
              <Text style={styles.cardRowSub}>
                {Object.entries(data.report_card.record).map(([k, v]) => `${k.replace('_', '/')} ${v}`).join(' · ') || 'No graded calls yet'} ›
              </Text>
            </Pressable>
          </>
        ) : messages.length === 0 ? (
          <Text style={styles.hint}>Ask anything about your team, another team, a player, a trade or the draft.</Text>
        ) : null}

        {messages.map((m, i) => (
          <View key={i} style={m.role === 'user' ? styles.mine : styles.theirs}>
            {m.role === 'user' ? (
              <Text style={styles.mineText}>{m.content}</Text>
            ) : (
              <>
                <Text style={styles.gm}>GM</Text>
                <Markdown text={m.content} />
                {sleeper && /trade/i.test(messages[i - 1]?.content ?? '') ? (
                  <Pressable style={styles.sleeper} onPress={() => Linking.openURL(sleeper)}>
                    <Text style={styles.sleeperText}>Open in Sleeper ↗</Text>
                  </Pressable>
                ) : null}
              </>
            )}
          </View>
        ))}
        {thinking ? (
          <View style={[styles.theirs, styles.thinking]}>
            <ActivityIndicator color={colors.gold} size="small" />
            <Text style={styles.thinkingText}>{/trade/i.test(lastQuestion) ? 'Scanning every roster…' : 'Thinking…'}</Text>
          </View>
        ) : null}
        {problem ? <Text style={styles.problem}>{problem}</Text> : null}
      </ScrollView>

      {!focused ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipsBar} contentContainerStyle={styles.chips} keyboardShouldPersistTaps="handled">
          {data.prompts.map((p) => (
            <Pressable key={p} onPress={() => send(p)} style={styles.chip}>
              <Text style={styles.chipText}>{p}</Text>
            </Pressable>
          ))}
        </ScrollView>
      ) : null}

      <View style={styles.inputBar}>
        <TextInput
          value={draft}
          onChangeText={setDraft}
          onFocus={() => setTyping(true)}
          onBlur={() => setTyping(!!draft.trim())}
          placeholder={data.ai_ready ? 'Ask your assistant GM…' : 'The Assistant GM is off for now'}
          placeholderTextColor={colors.muted}
          editable={data.ai_ready && !thinking}
          style={styles.input}
          multiline
          onSubmitEditing={() => send(draft)}
          blurOnSubmit
          returnKeyType="send"
        />
        <Pressable onPress={() => send(draft)} disabled={!draft.trim() || thinking} style={[styles.sendButton, (!draft.trim() || thinking) && styles.sendOff]}>
          <Text style={styles.sendText}>↑</Text>
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

// Weakest spot, opponent and power: the three tiles from the design.
const PICK = ['Weakest spot', 'Opponent', 'Power'];

function Tiles({ tiles }: { tiles: FoTile[] }) {
  const shown = PICK.map((name) => tiles.find((t) => t.label.startsWith(name))).filter(Boolean) as FoTile[];
  if (!shown.length) return null;
  return (
    <View style={styles.tiles}>
      {shown.map((t) => {
        const [name, ...rest] = t.label.split(' · ');
        return (
          <View key={name} style={[styles.tile, t.tone === 'warn' && styles.tileWarn]}>
            <Text style={styles.tileLabel}>{name.replace(' spot', '').toUpperCase()}</Text>
            <Text style={[styles.tileValue, t.tone === 'warn' && { color: '#F3A79F' }]} numberOfLines={1}>{t.value.startsWith('vs ') ? t.value.slice(3).split(' ')[0] : t.value}</Text>
            {rest.length ? <Text style={styles.tileSub} numberOfLines={1}>{rest.join(' · ').replace('Head to head ', 'H2H ')}</Text> : null}
          </View>
        );
      })}
    </View>
  );
}

function Brief({ data }: { data: FrontOffice }) {
  const { injuries, matchup } = data.brief;
  const call = injuries.call || matchup.stakes;
  const items = injuries.items.slice(0, 3);
  if (!call && !items.length && !matchup.lines.length) return null;
  return (
    <View style={styles.brief}>
      <View style={styles.briefTop}>
        <Text style={styles.eyebrow}>THIS WEEK'S BRIEF</Text>
        <Text style={styles.small}>Week {data.week}</Text>
      </View>
      {call ? <Text style={styles.briefText}><Text style={styles.gold}>Call: </Text>{call}</Text> : null}
      {items.length ? (
        <Text style={styles.briefText}>{items.map((i) => `${i.player} (${i.status})`).join(', ')}</Text>
      ) : null}
      {!call && matchup.lines[0] ? <Text style={styles.briefText}>{matchup.headline}: {matchup.lines[0]}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 16, paddingBottom: 20 },
  header: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 6 },
  title: { color: colors.text, fontSize: 26, fontWeight: '800' },
  menu: { marginLeft: 'auto', width: 34, height: 34, borderRadius: 17, backgroundColor: colors.card, alignItems: 'center', justifyContent: 'center' },
  menuText: { color: colors.gold, fontSize: 16 },
  greeting: { color: colors.muted, fontSize: 14, marginBottom: 12 },
  hint: { color: colors.muted, fontSize: 14, textAlign: 'center', marginTop: 40, paddingHorizontal: 24 },
  tiles: { flexDirection: 'row', gap: 8, marginBottom: 12 },
  tile: { flex: 1, backgroundColor: colors.row, borderWidth: 1, borderColor: colors.line, borderRadius: 14, paddingVertical: 10, paddingHorizontal: 6, alignItems: 'center' },
  tileWarn: { borderColor: '#5A2E2A' },
  tileLabel: { color: colors.muted, fontSize: 10, fontWeight: '700', letterSpacing: 1 },
  tileValue: { color: colors.gold, fontSize: 19, fontWeight: '800', marginTop: 3 },
  tileSub: { color: colors.muted, fontSize: 11, marginTop: 2 },
  brief: { backgroundColor: colors.row, borderWidth: 1, borderColor: colors.line, borderRadius: 16, padding: 14, marginBottom: 12, gap: 4 },
  briefTop: { flexDirection: 'row', justifyContent: 'space-between' },
  eyebrow: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1.2 },
  small: { color: colors.muted, fontSize: 12 },
  briefText: { color: colors.text, fontSize: 14, lineHeight: 20 },
  gold: { color: colors.gold },
  cardRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderWidth: 1, borderColor: colors.line, borderRadius: 14, padding: 12, marginBottom: 12 },
  cardRowText: { color: colors.text, fontWeight: '700', fontSize: 14 },
  cardRowSub: { color: colors.muted, fontSize: 12 },
  mine: { alignSelf: 'flex-end', maxWidth: '85%', backgroundColor: colors.gold, borderRadius: 16, padding: 12, marginBottom: 10 },
  mineText: { color: colors.bg, fontSize: 15, lineHeight: 21 },
  theirs: { backgroundColor: colors.row, borderRadius: 16, padding: 12, marginBottom: 10 },
  gm: { color: colors.gold, fontWeight: '800', fontSize: 12, marginBottom: 4 },
  thinking: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  thinkingText: { color: colors.muted, fontSize: 14 },
  problem: { color: '#E39A9A', fontSize: 13, textAlign: 'center', marginTop: 4 },
  sleeper: { marginTop: 10, backgroundColor: colors.gold, borderRadius: 12, paddingVertical: 10, alignItems: 'center' },
  sleeperText: { color: colors.bg, fontWeight: '800', fontSize: 14 },
  chipsBar: { flexGrow: 0 },
  chips: { gap: 8, paddingHorizontal: 16, paddingBottom: 8 },
  chip: { borderWidth: 1, borderColor: colors.line, borderRadius: 16, paddingHorizontal: 12, paddingVertical: 7 },
  chipText: { color: colors.text, fontSize: 13, fontWeight: '600' },
  // Extra bottom room: the raised GM tab button sits over the bar's lower edge.
  inputBar: { flexDirection: 'row', alignItems: 'flex-end', gap: 8, paddingHorizontal: 16, paddingBottom: 30, paddingTop: 4 },
  input: {
    flex: 1, maxHeight: 120, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.line, borderRadius: 22,
    paddingHorizontal: 16, paddingTop: 11, paddingBottom: 11, minHeight: 44, color: colors.text, fontSize: 15,
  },
  sendButton: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.gold, alignItems: 'center', justifyContent: 'center' },
  sendOff: { opacity: 0.4 },
  sendText: { color: colors.bg, fontSize: 18, fontWeight: '800' },
});
