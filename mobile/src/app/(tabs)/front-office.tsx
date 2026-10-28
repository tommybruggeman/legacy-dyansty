import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator, KeyboardAvoidingView, Linking, Platform, Pressable, ScrollView, type ScrollViewInstance, StyleSheet, Text, TextInput, View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Markdown } from '../../components/frontoffice/Markdown';
import { ErrorState, Loading } from '../../components/ScreenState';
import { Pill } from '../../components/ui';
import { type ChatEvent, type ChatMessage, type Conversation, type FoHeader, type FoMove, type FrontOffice, apiGet, apiStream, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';

// The Assistant GM: three tiles, this week's lineup moves and the report card,
// starter questions, then the chat. Trades are made in Sleeper, so trade answers link straight there.
export default function FrontOfficeScreen() {
  const { c, q, at } = useLocalSearchParams<{ c?: string; q?: string; at?: string }>();
  const { data, error, loading, reload } = useApi<FrontOffice>('/front-office');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [thinking, setThinking] = useState(false);
  // While the GM works: what it's checking right now, and the answer so far.
  const [status, setStatus] = useState('');
  const [partial, setPartial] = useState('');
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

  const send = async (input: string, convo: string | null = conversationId) => {
    const question = input.trim();
    if (!question || thinking) return;
    setDraft('');
    setProblem(null);
    setMessages((m) => [...m, { role: 'user', content: question }]);
    setThinking(true);
    setStatus('Reading your roster');
    setPartial('');
    let answer = '';
    try {
      await apiStream<ChatEvent>('/front-office/chat/stream', { question, conversation_id: convo }, (event) => {
        if (event.e === 'status') {
          setStatus(event.text);
          setPartial('');
        } else if (event.e === 'text') {
          answer += event.d;
          setPartial(answer);
        } else if (event.e === 'reset') {
          answer = '';
          setPartial('');
        } else if (event.e === 'round' && answer) {
          answer += '\n\n';
        } else if (event.e === 'done') {
          if (event.ok) setMessages((m) => [...m, { role: 'assistant', content: event.text }]);
          else setProblem(event.text);
          if (event.conversation_id) setConversationId(event.conversation_id);
        }
      });
    } catch (e: any) {
      setProblem(e?.message ?? 'Something went wrong.');
    } finally {
      setThinking(false);
      setPartial('');
    }
  };

  // A question typed on Home (q) starts a fresh chat and sends right away;
  // `at` makes asking the same question twice still count.
  const asked = useRef<string | null>(null);
  useEffect(() => {
    if (!q || asked.current === `${at}:${q}`) return;
    asked.current = `${at}:${q}`;
    setMessages([]);
    setConversationId(null);
    send(q, null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, at]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  // Once you tap the box (or the chat has started) the tiles, brief and
  // starter chips step aside and it's just you and the GM.
  const focused = typing || messages.length > 0 || thinking;
  const sleeper = data.sleeper_league_id ? `https://sleeper.com/leagues/${data.sleeper_league_id}` : null;

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
          <Pill label="GM" tone="gold" />
          <Pressable onPress={() => router.push('/chats')} hitSlop={10} style={styles.menu}>
            <Text style={styles.menuText}>☰</Text>
          </Pressable>
        </View>

        {!focused ? (
          <>
            <Tiles header={data.header} />
            <Moves week={data.week} moves={data.moves ?? []} win={data.win} onAsk={send} />
            <Pressable onPress={() => router.push('/report-card')} style={styles.card}>
              <ReportRing record={Object.values(data.report_card.record)[0]} />
              <View style={{ flex: 1 }}>
                <Text style={styles.cardRowText}>GM report card</Text>
                <Text style={styles.cardRowSub}>
                  {Object.entries(data.report_card.record).map(([k, v]) => `${v} on ${k.replace('_', '/')}`).join(' · ') || 'No graded calls yet'}
                </Text>
              </View>
              <Text style={styles.chev}>›</Text>
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
        {thinking && partial ? (
          <View style={styles.theirs}>
            <Text style={styles.gm}>GM</Text>
            <Markdown text={`${partial} ▌`} />
          </View>
        ) : thinking ? (
          <View style={[styles.theirs, styles.thinking]}>
            <ActivityIndicator color={colors.gold} size="small" />
            <Text style={styles.thinkingText}>{status}…</Text>
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

// Change on top, the number in the middle, what it is underneath.
function Tile({ top, topTone, value, valueTone, label }: { top: string; topTone?: 'up' | 'down'; value: string; valueTone?: 'warn'; label: string }) {
  return (
    <View style={styles.tile}>
      <Text style={[styles.tileTop, topTone === 'up' && styles.up, topTone === 'down' && styles.down]} numberOfLines={1}>{top}</Text>
      <Text style={[styles.tileValue, valueTone === 'warn' && styles.down]} numberOfLines={1}>{value}</Text>
      <Text style={styles.tileLabel} numberOfLines={1}>{label}</Text>
    </View>
  );
}

const arrow = (n: number | null | undefined, unit = '') =>
  n ? { top: `${n > 0 ? '▲' : '▼'} ${Math.abs(n)}${unit}`, tone: (n > 0 ? 'up' : 'down') as 'up' | 'down' } : { top: '–', tone: undefined };

function Tiles({ header }: { header?: FoHeader }) {
  const w = header?.weakest, o = header?.odds, p = header?.power;
  const odds = arrow(o?.change, '%'), power = arrow(p?.move);
  return (
    <View style={styles.tiles}>
      <Tile top={w?.note || '–'} topTone={w?.note?.startsWith('-') ? 'down' : undefined} value={w?.slot ?? '—'} valueTone={w ? 'warn' : undefined} label="WEAKEST" />
      <Tile top={odds.top} topTone={odds.tone} value={o ? `${o.pct}%` : '—'} label="PLAYOFF ODDS" />
      <Tile top={power.top} topTone={power.tone} value={p ? `#${p.rank}` : '—'} label="POWER RANK" />
    </View>
  );
}

const initials = (name: string) => name.split(' ').filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase();

// The changes to make in Sleeper this week. Tapping one asks the GM why.
function Moves({ week, moves, win, onAsk }: { week: number; moves: FoMove[]; win?: FrontOffice['win']; onAsk: (q: string) => void }) {
  return (
    <>
      <View style={styles.movesHead}>
        <Text style={styles.eyebrow}>WEEK {week} MOVES</Text>
        {win ? <Text style={[styles.small, win.pct >= 50 ? styles.up : styles.down]}>{win.pct}% to beat {win.opponent.split(' ')[0]}</Text> : null}
      </View>
      <View style={styles.card2}>
        {moves.length ? moves.map((m, i) => {
          const start = m.action === 'start';
          return (
            <Pressable key={`${m.action}-${m.player}`} onPress={() => onAsk(`Why ${start ? 'start' : 'sit'} ${m.player} this week?`)}
              style={[styles.move, i < moves.length - 1 && styles.moveLine]}>
              <View style={[styles.avatar, !start && styles.avatarSit]}>
                <Text style={[styles.avatarText, !start && styles.avatarSitText]}>{initials(m.player)}</Text>
              </View>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={styles.moveName} numberOfLines={1}>{m.player}</Text>
                {m.note ? <Text style={styles.moveNote} numberOfLines={1}>{m.note}</Text> : null}
              </View>
              <Text style={[styles.tag, start ? styles.tagStart : styles.tagSit]}>{start ? (m.slot === 'FLEX' ? 'FLEX' : `START ${m.slot}`.trim()) : 'SIT'}</Text>
            </Pressable>
          );
        }) : (
          <View style={styles.move}>
            <View style={styles.avatar}><Text style={styles.avatarText}>✓</Text></View>
            <Text style={styles.moveName}>Lineup looks set</Text>
          </View>
        )}
      </View>
    </>
  );
}

// '3-2' as a ring: the share of graded calls the GM got right.
function ReportRing({ record }: { record?: string }) {
  const [w, l] = (record ?? '').split('-').map(Number);
  const pct = w + l > 0 ? Math.round((100 * w) / (w + l)) : null;
  return (
    <View style={[styles.ring, pct !== null && pct >= 50 && styles.ringGood]}>
      <Text style={styles.ringText}>{pct === null ? '–' : `${pct}%`}</Text>
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
  hint: { color: colors.muted, fontSize: 14, textAlign: 'center', marginTop: 40, paddingHorizontal: 24 },
  tiles: { flexDirection: 'row', gap: 8, marginBottom: 16, marginTop: 6 },
  tile: { flex: 1, backgroundColor: colors.card, borderRadius: 14, paddingVertical: 12, paddingHorizontal: 6, alignItems: 'center', gap: 3 },
  tileTop: { color: colors.muted, fontSize: 12, fontWeight: '700' },
  tileValue: { color: colors.gold, fontSize: 24, fontWeight: '800' },
  tileLabel: { color: colors.muted, fontSize: 10, fontWeight: '700', letterSpacing: 1.2 },
  up: { color: colors.live },
  down: { color: '#E08A8A' },
  movesHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 },
  eyebrow: { color: colors.gold, fontSize: 12, fontWeight: '800', letterSpacing: 1.5 },
  small: { color: colors.muted, fontSize: 12 },
  card2: { backgroundColor: colors.card, borderRadius: 16, overflow: 'hidden', marginBottom: 12 },
  move: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12, paddingHorizontal: 14 },
  moveLine: { borderBottomWidth: 1, borderBottomColor: '#1E3A2D' },
  avatar: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.green, alignItems: 'center', justifyContent: 'center' },
  avatarSit: { backgroundColor: colors.line },
  avatarText: { color: colors.bg, fontWeight: '800', fontSize: 13 },
  avatarSitText: { color: colors.muted },
  moveName: { color: colors.text, fontSize: 15, fontWeight: '700' },
  moveNote: { color: colors.muted, fontSize: 12, marginTop: 1 },
  tag: { fontSize: 11, fontWeight: '800', borderRadius: 8, paddingHorizontal: 8, paddingVertical: 5, overflow: 'hidden' },
  tagStart: { color: colors.live, backgroundColor: '#10352A' },
  tagSit: { color: '#E08A8A', backgroundColor: '#3A1E1E' },
  card: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: colors.card, borderRadius: 16, padding: 12, marginBottom: 12 },
  ring: { width: 42, height: 42, borderRadius: 21, borderWidth: 4, borderColor: colors.line, alignItems: 'center', justifyContent: 'center' },
  ringGood: { borderColor: colors.gold },
  ringText: { color: colors.text, fontSize: 11, fontWeight: '800' },
  cardRowText: { color: colors.text, fontWeight: '700', fontSize: 15 },
  cardRowSub: { color: colors.muted, fontSize: 12, marginTop: 1 },
  chev: { color: colors.gold, fontSize: 20 },
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
