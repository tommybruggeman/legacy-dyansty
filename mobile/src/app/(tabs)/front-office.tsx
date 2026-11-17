import { router, useLocalSearchParams, useNavigation } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator, Alert, AppState, Image, Keyboard, KeyboardAvoidingView, Linking, Platform, Pressable, ScrollView, type ScrollViewInstance, StyleSheet, Text, TextInput, View,
} from 'react-native';
import ImageCropPicker from 'react-native-image-crop-picker';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Markdown } from '../../components/frontoffice/Markdown';
import { MoveCard } from '../../components/frontoffice/MoveCard';
import { ErrorState, Loading } from '../../components/ScreenState';
import { usePullDown } from '../../components/PullDown';
import { Pill, useScrollTopOnTab } from '../../components/ui';
import { type ChatEvent, type ChatMessage, type Conversation, type FoHeader, type FoMove, type FrontOffice, apiGet, apiSend, apiStream, useApi } from '../../lib/api';
import { clearLastChat, saveLastChat } from '../../lib/lastChat';
import { isTradeQuestion } from '../../lib/tradeQuestion';
import { colors } from '../../lib/theme';

// Screenshots per question (a trade offer, a roster, another league). The server allows the same.
const MAX_PHOTOS = 10;
const PHOTO_QUESTION = 'What do you make of this?';
type Photo = { uri: string; data: string };

// Resolves right away when the app is on screen, otherwise the next time it comes back.
function whenOnScreen(): Promise<void> {
  if (AppState.currentState === 'active') return Promise.resolve();
  return new Promise((resolve) => {
    const sub = AppState.addEventListener('change', (state) => {
      if (state === 'active') { sub.remove(); resolve(); }
    });
  });
}

// The Assistant GM: three tiles, this week's lineup moves and the report card,
// starter questions, then the chat. Trades are made in Sleeper, so trade answers link straight there.
export default function FrontOfficeScreen() {
  const { c, q, at, resume } = useLocalSearchParams<{ c?: string; q?: string; at?: string; resume?: string }>();
  const { data, error, loading, reload } = useApi<FrontOffice>('/front-office');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [thinking, setThinking] = useState(false);
  // While the GM works: what it's checking right now, and the answer so far.
  const [status, setStatus] = useState('');
  const [partial, setPartial] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [typing, setTyping] = useState(false);
  // Something sent in the split second after an answer finished goes out as the next question.
  const queuedRef = useRef<string | null>(null);
  const busy = useRef(false);
  const convoRef = useRef<string | null>(null);
  convoRef.current = conversationId;
  const scroll = useRef<ScrollViewInstance>(null);
  const insets = useSafeAreaInsets();
  // Front Office is a chat, so a pull only refreshes here (no pull to search).
  const pull = usePullDown({ search: false, top: insets.top + 8 });
  const navigation = useNavigation();
  // Bumped when a chat is closed, so an answer still streaming for it is dropped.
  const chat = useRef(0);
  useScrollTopOnTab(scroll);
  const focusedChat = useRef(0);

  // Tapping the GM tab while already in a chat goes back to the GM homepage (tiles, brief, starters).
  // The chat is saved and stays in the chats list.
  useEffect(() => navigation.addListener('tabPress' as never, () => {
    if (!navigation.isFocused()) return;
    chat.current += 1;
    Keyboard.dismiss();
    setMessages([]);
    setConversationId(null);
    setDraft('');
    setPhotos([]);
    setTyping(false);
    setThinking(false);
    busy.current = false;
    queuedRef.current = null;
    setPartial('');
    setProblem(null);
    clearLastChat();
    scroll.current?.scrollTo({ y: 0, animated: false });
  }), [navigation]);

  // Leaving the app mid-chat starts the 30-minute "drop me back in" clock.
  const inChat = useRef(false);
  inChat.current = messages.length > 0 || thinking;
  useEffect(() => {
    const sub = AppState.addEventListener('change', (state) => {
      if (state !== 'active' && inChat.current) saveLastChat({ id: convoRef.current, pending: busy.current });
    });
    return () => sub.remove();
  }, []);

  // Opening a past chat (c=id) or starting a new one (c=new) from the chats list.
  // resume=1 (reopening the app mid-answer) then picks that answer back up from the server.
  useEffect(() => {
    if (!c) return;
    setProblem(null);
    const pickUp = () => { if (resume === '1' && !busy.current) follow(chat.current, null); };
    if (c === 'new') {
      setMessages([]);
      setConversationId(null);
      convoRef.current = null;
      pickUp();
      return;
    }
    apiGet<Conversation>(`/front-office/conversations/${c}`)
      .then((convo) => { setMessages(convo.messages); setConversationId(convo.id); convoRef.current = convo.id; })
      .catch((e) => setProblem(e?.message ?? 'Couldn’t open that chat.'))
      .finally(pickUp);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c, at]);

  // One answer's events, from a fresh question or from picking one back up.
  const listen = (mine: number) => {
    let answer = '';
    let stale = false; // picked up an answer this chat already shows
    return (event: ChatEvent) => {
      if (chat.current !== mine || stale) return;
      if (event.e === 'job') {
        setStatus('Picking up where the GM left off');
        setMessages((m) => {
          const last = m[m.length - 1];
          if (last?.role === 'assistant' && m[m.length - 2]?.content === event.question) {
            stale = true;
            return m;
          }
          return last?.role === 'user' && last.content === event.question ? m : [...m, { role: 'user', content: event.question }];
        });
      } else if (event.e === 'added') {
        setMessages((m) => [...m, { role: 'user', content: event.text }]);
      } else if (event.e === 'none') {
        stale = true;
      } else if (event.e === 'status') {
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
        if (event.ok) setMessages((m) => [...m, { role: 'assistant', content: event.text, ...(event.moves?.length ? { moves: event.moves } : {}) }]);
        else setProblem(event.text);
        if (event.conversation_id) {
          convoRef.current = event.conversation_id;
          setConversationId(event.conversation_id);
        }
        saveLastChat({ id: event.conversation_id ?? convoRef.current, pending: false });
      }
    };
  };

  // Wrap up an answer (or a failed one), then send whatever was typed while it ran.
  const finish = (mine: number) => {
    if (chat.current !== mine) return;
    busy.current = false;
    setThinking(false);
    setPartial('');
    const next = queuedRef.current;
    queuedRef.current = null;
    if (next) send(next, convoRef.current);
  };

  // The answer keeps going on the server if the phone locks or the app closes.
  // Follow it from the start (once the app is back on screen) until it's done.
  const follow = async (mine: number, question: string | null) => {
    busy.current = true;
    setThinking(true);
    setStatus('Picking up where the GM left off');
    setPartial('');
    let got: ChatEvent['e'] | null = null;
    try {
      await whenOnScreen();
      await apiStream<ChatEvent>('/front-office/chat/resume', {}, (event) => {
        got = event.e;
        listen(mine)(event);
      });
      if (got === 'none' && question && chat.current === mine) {
        setDraft(question);
        setProblem('That answer got lost while the app was closed. Tap send to ask again.');
      }
    } catch (e: any) {
      if (chat.current === mine) setProblem(e?.message ?? 'Something went wrong.');
    } finally {
      finish(mine);
    }
  };

  // Screenshots shrink on the phone to the size the GM reads anyway (1568px, about 200 KB each).
  const attach = async () => {
    const left = MAX_PHOTOS - photos.length;
    if (left <= 0) return;
    const res = await ImageCropPicker.openPicker({
      mediaType: 'photo', multiple: true, maxFiles: left, includeBase64: true, forceJpg: true,
      compressImageMaxWidth: 1568, compressImageMaxHeight: 1568, compressImageQuality: 0.8,
    }).catch(() => null); // cancelled
    if (!res) return;
    const picked = (Array.isArray(res) ? res : [res]).filter((r) => r.data).map((r) => ({ uri: r.path, data: r.data as string }));
    setPhotos((p) => [...p, ...picked].slice(0, MAX_PHOTOS));
    if (picked.length < (Array.isArray(res) ? res.length : 1) || photos.length + picked.length > MAX_PHOTOS) {
      setProblem(`Up to ${MAX_PHOTOS} screenshots per question.`);
    }
  };

  const send = async (input: string, convo: string | null = convoRef.current, shots: Photo[] = []) => {
    const question = input.trim() || (shots.length ? PHOTO_QUESTION : '');
    if (!question) return;
    if (busy.current && shots.length) {
      setProblem('Send the screenshots once this answer is done.');
      return;
    }
    if (busy.current) {
      // Still answering: this goes straight into the answer the GM is working on,
      // and shows up in the chat as soon as the GM has it.
      setDraft('');
      try {
        const { added } = await apiSend<{ added: boolean }>('POST', '/front-office/chat/add', { text: question });
        if (!added) {
          // That answer just finished: ask this as the next question.
          if (busy.current) queuedRef.current = question;
          else send(question);
        }
      } catch (e: any) {
        setDraft(question);
        setProblem(e?.message ?? 'Couldn’t send that. Try again.');
      }
      return;
    }
    busy.current = true;
    setDraft('');
    setPhotos([]);
    setProblem(null);
    setMessages((m) => [...m, { role: 'user', content: question, ...(shots.length ? { photos: shots.length, uris: shots.map((p) => p.uri) } : {}) }]);
    setThinking(true);
    setStatus('Reading your roster');
    setPartial('');
    const mine = chat.current;
    saveLastChat({ id: convo, pending: true });
    let started = false;
    let done = false;
    const onEvent = listen(mine);
    try {
      const images = shots.map((p) => ({ media_type: 'image/jpeg', data: p.data }));
      await apiStream<ChatEvent>('/front-office/chat/stream', { question, conversation_id: convo, ...(images.length ? { images } : {}) }, (event) => {
        started = true;
        if (event.e === 'done') done = true;
        onEvent(event);
      });
    } catch (e: any) {
      if (started && !done && chat.current === mine) {
        // The connection dropped mid-answer (phone locked, app in the background).
        // The server is still on it: pick it back up when the app is on screen again.
        follow(mine, question);
        return;
      }
      if (chat.current === mine) {
        setProblem(e?.message ?? 'Something went wrong.');
        if (shots.length && !started) setPhotos(shots); // not sent: keep them attached to try again
      }
    }
    finish(mine);
  };

  // A question typed on Home (q) starts a fresh chat and sends right away;
  // `at` makes asking the same question twice still count.
  const asked = useRef<string | null>(null);
  useEffect(() => {
    if (!q || asked.current === `${at}:${q}`) return;
    asked.current = `${at}:${q}`;
    setMessages([]);
    setConversationId(null);
    convoRef.current = null;
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
        // Dragging the chat (a swipe down) puts the keyboard away, like Messages.
        keyboardDismissMode="on-drag"
        {...pull.scrollProps}
      >
        {pull.spacer}
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
          <Text style={styles.hint}>Ask anything about your team, another team, a player, a trade or the draft. Tap + to add screenshots, even from another league.</Text>
        ) : null}

        {messages.map((m, i) => (
          <View key={i} style={m.role === 'user' ? styles.mine : styles.theirs}>
            {m.role === 'user' ? (
              <>
                {m.uris?.length ? (
                  <View style={styles.shots}>
                    {m.uris.map((u) => <Image key={u} source={{ uri: u }} style={styles.shot} />)}
                  </View>
                ) : m.photos ? (
                  <Text style={styles.shotNote}>📎 {m.photos === 1 ? 'a screenshot' : `${m.photos} screenshots`}</Text>
                ) : null}
                <Text style={styles.mineText}>{m.content}</Text>
              </>
            ) : (
              <>
                <Text style={styles.gm}>GM</Text>
                <Markdown text={m.content} />
                {m.moves?.map((card) => (
                  <MoveCard key={card.id} card={card} conversationId={conversationId}
                    onChange={(next) => setMessages((all) => all.map((x, j) => (j === i ? { ...x, moves: x.moves?.map((y) => (y.id === next.id ? next : y)) } : x)))} />
                ))}
                {sleeper && !messages[i - 1]?.photos && isTradeQuestion(messages[i - 1]?.content) ? (
                  <Pressable style={styles.sleeper} onPress={() => Linking.openURL(sleeper)}>
                    <Text style={styles.sleeperText}>Open in Sleeper ↗</Text>
                  </Pressable>
                ) : null}
                <Pressable onPress={() => reportAnswer(messages[i - 1]?.content, m.content, conversationId)} hitSlop={8} style={styles.report}>
                  <Text style={styles.reportText}>⚑ Report</Text>
                </Pressable>
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
      {pull.overlay}

      {!focused ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipsBar} contentContainerStyle={styles.chips} keyboardShouldPersistTaps="handled">
          {data.prompts.map((p) => (
            <Pressable key={p} onPress={() => send(p)} style={styles.chip}>
              <Text style={styles.chipText}>{p}</Text>
            </Pressable>
          ))}
        </ScrollView>
      ) : null}

      {photos.length ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipsBar} contentContainerStyle={styles.tray} keyboardShouldPersistTaps="handled">
          {photos.map((p, i) => (
            <View key={p.uri}>
              <Image source={{ uri: p.uri }} style={styles.trayShot} />
              <Pressable onPress={() => setPhotos((all) => all.filter((_, j) => j !== i))} hitSlop={8} style={styles.trayX}>
                <Text style={styles.trayXText}>×</Text>
              </Pressable>
            </View>
          ))}
        </ScrollView>
      ) : null}

      <View style={styles.inputBar}>
        <Pressable onPress={attach} disabled={!data.ai_ready || thinking || photos.length >= MAX_PHOTOS} hitSlop={6}
          style={[styles.attach, (!data.ai_ready || thinking || photos.length >= MAX_PHOTOS) && styles.sendOff]}>
          <Text style={styles.attachText}>+</Text>
        </Pressable>
        <TextInput
          value={draft}
          onChangeText={setDraft}
          onFocus={() => { focusedChat.current = chat.current; setTyping(true); }}
          onBlur={() => setTyping(focusedChat.current === chat.current && !!draft.trim())}
          placeholder={data.ai_ready ? 'Ask your assistant GM…' : 'The Assistant GM is off for now'}
          placeholderTextColor={colors.muted}
          editable={data.ai_ready}
          style={styles.input}
          multiline
          onSubmitEditing={() => send(draft, convoRef.current, photos)}
          blurOnSubmit
          returnKeyType="send"
        />
        <Pressable onPress={() => send(draft, convoRef.current, photos)} disabled={(!draft.trim() && !photos.length) || !data.ai_ready}
          style={[styles.sendButton, ((!draft.trim() && !photos.length) || !data.ai_ready) && styles.sendOff]}>
          <Text style={styles.sendText}>↑</Text>
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

// Change on top, the number in the middle, what it is underneath.
// Apple asks AI apps for a way to flag a bad answer. Flags go to the weekly AI report so the GM can learn from them.
function reportAnswer(question: string | undefined, answer: string, conversationId: string | null) {
  const send = (reason: string) =>
    apiSend('POST', '/front-office/report', { reason, question: question ?? null, answer, conversation_id: conversationId })
      .then(() => Alert.alert('Thanks', 'We’ll look at this answer.'))
      .catch((e: any) => Alert.alert('Couldn’t send that', e?.message ?? 'Please try again.'));
  Alert.alert('Report this answer', 'What’s wrong with it?', [
    { text: 'It’s wrong', onPress: () => send('wrong') },
    { text: 'It’s out of date', onPress: () => send('outdated') },
    { text: 'Offensive or harmful', onPress: () => send('harmful') },
    { text: 'Cancel', style: 'cancel' },
  ]);
}

const arrow = (n: number | null | undefined, unit = '') =>
  n ? { top: `${n > 0 ? '▲' : '▼'} ${Math.abs(n)}${unit}`, tone: (n > 0 ? 'up' : 'down') as 'up' | 'down' } : { top: '–', tone: undefined };

// The top of the GM page (Tommy, 2026-11-17, option 3): playoff odds in a ring with its label inside,
// power rank and weakest spot as two quiet rows beside it.
function Tiles({ header }: { header?: FoHeader }) {
  const w = header?.weakest, o = header?.odds, p = header?.power;
  const odds = arrow(o?.change, '%'), power = arrow(p?.move);
  return (
    <View style={styles.hero}>
      <OddsRing pct={o?.pct ?? null} change={o?.change ? odds : null} />
      <View style={styles.heroRows}>
        <View style={styles.heroRow}>
          <Text style={styles.heroKey}>Power rank</Text>
          <Text style={styles.heroVal}>
            {p ? `#${p.rank}` : '—'}
            {p?.move ? <Text style={[styles.heroMove, power.tone === 'up' ? styles.up : styles.down]}>  {power.top}</Text> : null}
          </Text>
        </View>
        <View style={[styles.heroRow, styles.heroRowLine]}>
          <Text style={styles.heroKey}>Weakest spot</Text>
          <Text style={[styles.heroVal, w && styles.down]} numberOfLines={1}>{w ? `${w.slot}${w.note ? ` ${w.note.replace('-', '−')}` : ''}` : '—'}</Text>
        </View>
      </View>
    </View>
  );
}

const RING = 92;
const RING_W = 7;

// Playoff odds as a ring filled clockwise from the top, drawn from two clipped half circles (no SVG in the app),
// with the number, "PLAYOFF ODDS" and the weekly change inside.
function OddsRing({ pct, change }: { pct: number | null; change: { top: string; tone?: 'up' | 'down' } | null }) {
  const deg = Math.max(0, Math.min(100, pct ?? 0)) * 3.6;
  const arc = { borderTopColor: colors.gold, borderRightColor: colors.gold };
  return (
    <View style={styles.oddsRing}>
      <View style={[styles.ringHalf, { left: RING / 2 }]}>
        <View style={[styles.ringArc, arc, { left: -RING / 2, transform: [{ rotate: `${45 + Math.min(deg, 180) - 180}deg` }] }]} />
      </View>
      <View style={[styles.ringHalf, { left: 0 }]}>
        <View style={[styles.ringArc, arc, { left: 0, transform: [{ rotate: `${225 + Math.max(deg - 180, 0) - 180}deg` }] }]} />
      </View>
      <Text style={styles.ringPct}>{pct != null ? `${pct}%` : '—'}</Text>
      <Text style={styles.ringLabel}>PLAYOFF ODDS</Text>
      {change ? <Text style={[styles.ringChange, change.tone === 'up' ? styles.up : styles.down]}>{change.top}</Text> : null}
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
  hero: { flexDirection: 'row', alignItems: 'center', gap: 16, backgroundColor: colors.card, borderRadius: 18, padding: 14, marginBottom: 16, marginTop: 6 },
  heroRows: { flex: 1 },
  heroRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', paddingVertical: 8 },
  heroRowLine: { borderTopWidth: 1, borderTopColor: '#1E3A2D' },
  heroKey: { color: colors.muted, fontSize: 14 },
  heroVal: { color: colors.text, fontSize: 16, fontWeight: '800' },
  heroMove: { fontSize: 11, fontWeight: '700' },
  oddsRing: { width: RING, height: RING, borderRadius: RING / 2, borderWidth: RING_W, borderColor: '#22352B', alignItems: 'center', justifyContent: 'center' },
  ringHalf: { position: 'absolute', top: -RING_W, width: RING / 2, height: RING, overflow: 'hidden', marginLeft: -RING_W },
  ringArc: { position: 'absolute', top: 0, width: RING, height: RING, borderRadius: RING / 2, borderWidth: RING_W, borderColor: 'transparent' },
  ringPct: { color: colors.gold, fontSize: 22, fontWeight: '800', lineHeight: 24 },
  ringLabel: { color: colors.muted, fontSize: 7.5, fontWeight: '800', letterSpacing: 0.8, marginTop: 1 },
  ringChange: { fontSize: 9.5, fontWeight: '800', marginTop: 1 },
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
  report: { alignSelf: 'flex-end', marginTop: 6 },
  reportText: { color: colors.muted, fontSize: 12, fontWeight: '600' },
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
  attach: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.line, alignItems: 'center', justifyContent: 'center' },
  attachText: { color: colors.gold, fontSize: 22, fontWeight: '700', marginTop: -2 },
  tray: { gap: 8, paddingHorizontal: 16, paddingTop: 4, paddingBottom: 6 },
  trayShot: { width: 56, height: 84, borderRadius: 8, backgroundColor: colors.card },
  trayX: { position: 'absolute', top: -4, right: -4, width: 20, height: 20, borderRadius: 10, backgroundColor: colors.bg, borderWidth: 1, borderColor: colors.line, alignItems: 'center', justifyContent: 'center' },
  trayXText: { color: colors.text, fontSize: 13, fontWeight: '800', marginTop: -1 },
  shots: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginBottom: 8 },
  shot: { width: 64, height: 96, borderRadius: 8, backgroundColor: colors.card },
  shotNote: { color: colors.bg, fontSize: 12, fontWeight: '700', marginBottom: 4, opacity: 0.8 },
  sendText: { color: colors.bg, fontSize: 18, fontWeight: '800' },
});
