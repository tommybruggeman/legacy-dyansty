import { useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Dimensions, Image, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Card, Note, Row, Screen, Tile, Tiles, Title, ui } from '../components/ui';
import { type ActSession, type Activity, apiSend, useApi } from '../lib/api';
import { supabase } from '../lib/supabase';
import { colors } from '../lib/theme';

// Owner activity, only for the league owner's account: every page, flow, session and replay, by day, week or
// month, plus an analyst to ask. Same numbers as the website's Settings > League tools > Owner activity.

const RANGES = [{ label: '7 days', days: 7 }, { label: '30 days', days: 30 }, { label: '90 days', days: 90 }, { label: 'Year', days: 365 }] as const;
const GROUPS = ['day', 'week', 'month'] as const;
const TABS = ['Overview', 'Pages', 'Flows', 'When', 'Owners', 'Sessions', 'Ask'] as const;
type Tab = (typeof TABS)[number];

function tz() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return 'America/Denver';
  }
}

export default function OwnerActivity() {
  const [days, setDays] = useState<number>(30);
  const [by, setBy] = useState<(typeof GROUPS)[number]>('day');
  const [tab, setTab] = useState<Tab>('Overview');
  const [owner, setOwner] = useState<{ id: string; name: string } | null>(null);
  const path = `/commissioner/activity?days=${days}&by=${by}&tz=${encodeURIComponent(tz())}${owner ? `&owner=${owner.id}` : ''}`;
  const { data, error, loading, reload } = useApi<Activity>(path);

  return (
    <Screen>
      <Back label="League tools" />
      <Title eyebrow="ONLY YOU SEE THIS">Owner activity</Title>
      <Chips options={RANGES.map((r) => r.label)} value={RANGES.find((r) => r.days === days)?.label ?? ''} onChange={(l) => setDays(RANGES.find((r) => r.label === l)!.days)} />
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={s.tabs} contentContainerStyle={{ gap: 6 }}>
        {TABS.map((t) => (
          <Pressable key={t} onPress={() => setTab(t)} style={[s.tab, t === tab && s.tabOn]}>
            <Text style={[s.tabText, t === tab && s.tabTextOn]}>{t}</Text>
          </Pressable>
        ))}
      </ScrollView>
      {loading && !data ? <Loading /> : error && !data ? <ErrorState message={error} onRetry={reload} /> : !data ? null : (
        <>
          {tab === 'Overview' ? <Overview a={data} /> : null}
          {tab === 'Pages' ? <Pages a={data} by={by} setBy={setBy} /> : null}
          {tab === 'Flows' ? <Flows a={data} /> : null}
          {tab === 'When' ? <When a={data} /> : null}
          {tab === 'Owners' ? <Owners a={data} onPick={(id, name) => { setOwner({ id, name }); setTab('Sessions'); }} /> : null}
          {tab === 'Sessions' ? <Sessions a={data} owner={owner} clear={() => setOwner(null)} /> : null}
          {tab === 'Ask' ? <Ask days={days} /> : null}
        </>
      )}
    </Screen>
  );
}

function Chips({ options, value, onChange }: { options: string[]; value: string; onChange: (v: string) => void }) {
  return (
    <View style={s.chips}>
      {options.map((o) => (
        <Pressable key={o} onPress={() => onChange(o)} style={[s.chip, o === value && s.chipOn]}>
          <Text style={[s.chipText, o === value && s.chipTextOn]}>{o}</Text>
        </Pressable>
      ))}
    </View>
  );
}

function BarRow({ label, value, max, right }: { label: string; value: number; max: number; right: string }) {
  return (
    <View style={s.barRow}>
      <View style={s.barHead}>
        <Text style={s.barLabel} numberOfLines={1}>{label}</Text>
        <Text style={s.barRight}>{right}</Text>
      </View>
      <View style={s.track}><View style={[s.fill, { width: `${max ? Math.max(2, (100 * value) / max) : 0}%` }]} /></View>
    </View>
  );
}

function Overview({ a }: { a: Activity }) {
  const t = a.totals;
  const maxDay = Math.max(1, ...a.daily.map((d) => d.owners));
  return (
    <>
      <Tiles>
        <Tile label="TODAY" value={`${t.active_today}/${t.owners}`} sub="owners" />
        <Tile label="7 DAYS" value={`${t.active_7d}/${t.owners}`} sub="owners" />
        <Tile label="30 DAYS" value={`${t.active_30d}/${t.owners}`} sub="owners" />
      </Tiles>
      <Tiles>
        <Tile label="SESSIONS" value={`${t.sessions}`} sub={`${t.sessions_per_owner_day}/owner/day`} />
        <Tile label="TYPICAL" value={`${t.median_session_min}m`} sub="per session" />
        <Tile label="TIME" value={`${Math.round(t.minutes / 60)}h`} sub={`${t.minutes} min`} />
      </Tiles>
      <Tiles>
        <Tile label="ACTIONS" value={`${t.actions}`} sub={`${t.taps} taps`} />
        <Tile label="GM ASKS" value={`${t.gm_questions}`} sub={`$${t.gm_cost.toFixed(2)}`} />
        <Tile label="ERRORS" value={`${t.errors}`} tone={t.errors ? 'red' : undefined} sub={`${t.push_opens} alert opens`} />
      </Tiles>
      <Card title="Active owners by day">
        <View style={s.spark}>
          {a.daily.slice(-30).map((d) => (
            <View key={d.date} style={s.sparkCol}>
              <View style={[s.sparkBar, { height: `${(100 * d.owners) / maxDay}%` }]} />
            </View>
          ))}
        </View>
        <Note>Last {Math.min(30, a.daily.length)} days · tallest = {maxDay} owners</Note>
      </Card>
      {a.quiet.length ? (
        <Card title="Gone quiet (7+ days)">
          <Text style={ui.value}>{a.quiet.join(', ')}</Text>
        </Card>
      ) : null}
      <Card title="Builds in use">
        {a.builds.length ? a.builds.map((b, i) => (
          <Row key={b.build} first={i === 0}><Text style={[ui.value, ui.flex]}>Build {b.build}</Text><Text style={ui.sub}>{b.owners} owners</Text></Row>
        )) : <Note>No app opens yet.</Note>}
      </Card>
    </>
  );
}

function Pages({ a, by, setBy }: { a: Activity; by: (typeof GROUPS)[number]; setBy: (b: (typeof GROUPS)[number]) => void }) {
  const [pick, setPick] = useState<string | null>(null);
  const periods = [...a.periods].reverse();
  const period = periods.find((p) => p.period === pick) ?? null;
  const maxOpens = Math.max(1, ...a.screens.map((x) => x.opens));
  return (
    <>
      <Card title={`Page visits by ${by}`}>
        <Chips options={['Day', 'Week', 'Month']} value={by[0].toUpperCase() + by.slice(1)} onChange={(v) => { setBy(v.toLowerCase() as typeof by); setPick(null); }} />
        {periods.map((p, i) => (
          <View key={p.period}>
            <Row first={i === 0} onPress={() => setPick(pick === p.period ? null : p.period)}>
              <View style={ui.flex}>
                <Text style={ui.value}>{p.period}</Text>
                <Text style={ui.sub}>{p.owners} owners · {p.sessions} sessions · {p.minutes} min · {p.actions} actions</Text>
              </View>
              <Text style={ui.name}>{p.page_visits}</Text>
            </Row>
            {period?.period === p.period ? (
              <View style={s.inset}>
                {p.visits.slice(0, 12).map((v) => <BarRow key={v.name} label={v.name} value={v.count} max={p.visits[0]?.count ?? 1} right={`${v.count}`} />)}
                {p.top_flows.length ? <Text style={s.h}>Top flows</Text> : null}
                {p.top_flows.slice(0, 5).map((f) => <Text key={f.path} style={ui.sub}>{f.count}× {f.path}</Text>)}
              </View>
            ) : null}
          </View>
        ))}
        {!periods.length ? <Note>Nothing recorded in this range yet.</Note> : null}
      </Card>
      <Card title="All pages" right="visits · avg time">
        {a.screens.map((x) => <BarRow key={x.name} label={x.name} value={x.opens} max={maxOpens} right={`${x.opens} · ${x.avg_secs}s · ${x.owners} owners`} />)}
      </Card>
      <Card title="Where people tap">
        {a.taps.slice(0, 6).map((t) => <TapMap key={t.name} name={t.name} count={t.count} spots={t.spots} />)}
        {!a.taps.length ? <Note>Taps show up once owners are on the new build.</Note> : null}
      </Card>
      <Card title="Never opened">
        <Text style={ui.value}>{a.unused.screens.join(', ') || 'Every page has been opened.'}</Text>
        <Text style={s.h}>Never done</Text>
        <Text style={ui.value}>{a.unused.actions.join(', ') || 'Every action has been used.'}</Text>
        {a.unused.rare_screens.length ? <><Text style={s.h}>Only one owner</Text><Text style={ui.value}>{a.unused.rare_screens.join(', ')}</Text></> : null}
      </Card>
    </>
  );
}

function TapMap({ name, count, spots }: { name: string; count: number; spots: number[][] }) {
  const w = 92;
  const h = 190;
  return (
    <View style={s.tapRow}>
      <View style={[s.phone, { width: w, height: h }]}>
        {spots.map(([x, y], i) => <View key={i} style={[s.dot, { left: x * w - 4, top: y * h - 4 }]} />)}
      </View>
      <View style={ui.flex}>
        <Text style={ui.name}>{name}</Text>
        <Text style={ui.sub}>{count} taps</Text>
      </View>
    </View>
  );
}

function Flows({ a }: { a: Activity }) {
  return (
    <>
      <Card title="First move each day">
        {a.first_moves.map((f, i) => <BarRow key={f.name} label={`${i + 1}. ${f.name}`} value={f.count} max={a.first_moves[0]?.count ?? 1} right={`${f.count} days`} />)}
        {!a.first_moves.length ? <Note>Not enough yet.</Note> : null}
      </Card>
      <Card title="How sessions start" right="first 3 steps">
        {a.paths.starts.map((p) => <Row key={p.path}><Text style={[ui.value, ui.flex]}>{p.path}</Text><Text style={ui.sub}>{p.count}×</Text></Row>)}
      </Card>
      <Card title="Most common next step">
        {a.paths.pairs.map((p) => <Row key={p.path}><Text style={[ui.value, ui.flex]}>{p.path}</Text><Text style={ui.sub}>{p.count}×</Text></Row>)}
      </Card>
      <Card title="What gets done">
        {a.actions.map((x) => <BarRow key={x.name} label={x.name} value={x.count} max={a.actions[0]?.count ?? 1} right={`${x.count} · ${x.owners} owners`} />)}
        {!a.actions.length ? <Note>No actions in this range.</Note> : null}
      </Card>
    </>
  );
}

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function When({ a }: { a: Activity }) {
  const max = Math.max(1, ...a.heatmap.flat());
  const cell = Math.floor((Dimensions.get('window').width - 32 - 28 - 40) / 24);
  return (
    <>
      <Card title="Day and hour" right={a.tz.split('/').pop()?.replace('_', ' ')}>
        {a.heatmap.map((row, d) => (
          <View key={d} style={s.heatRow}>
            <Text style={s.heatDay}>{DAYS[d]}</Text>
            {row.map((v, h) => <View key={h} style={{ width: cell, height: cell, margin: 0.5, borderRadius: 2, backgroundColor: v ? colors.gold : colors.card, opacity: v ? 0.2 + (0.8 * v) / max : 1 }} />)}
          </View>
        ))}
        <View style={s.heatRow}><Text style={s.heatDay} />{[0, 6, 12, 18].map((h) => <Text key={h} style={[s.heatHour, { width: cell * 6 + 3 }]}>{h === 0 ? '12a' : h === 12 ? '12p' : h > 12 ? `${h - 12}p` : `${h}a`}</Text>)}</View>
      </Card>
      <Card title="Each day of the week">
        {a.weekdays.map((w, i) => (
          <Row key={w.day} first={i === 0}>
            <Text style={[ui.name, { width: 40 }]}>{w.day}</Text>
            <View style={ui.flex}>
              <Text style={ui.value} numberOfLines={1}>{w.top_screens.join(' · ') || '—'}</Text>
              <Text style={ui.sub} numberOfLines={1}>{w.top_actions.length ? `Does: ${w.top_actions.join(', ')}` : 'No actions'}</Text>
            </View>
            <Text style={ui.sub}>{w.avg_owners} avg</Text>
          </Row>
        ))}
      </Card>
    </>
  );
}

function ago(o: { days_since: number | null; last_seen: string | null }) {
  if (o.days_since == null) return 'Never opened';
  if (o.days_since === 0) return `Today ${new Date(o.last_seen!).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;
  return o.days_since === 1 ? 'Yesterday' : `${o.days_since} days ago`;
}

function Owners({ a, onPick }: { a: Activity; onPick: (id: string, name: string) => void }) {
  return (
    <Card title="Every owner" right="tap for sessions">
      {a.owners.map((o, i) => (
        <Row key={o.user_id} first={i === 0} onPress={() => onPick(o.user_id, o.name)}>
          <View style={ui.flex}>
            <Text style={ui.name}>{o.name}</Text>
            <Text style={ui.sub}>{ago(o)} · {o.days_active} days · {o.sessions} sessions · {o.minutes} min</Text>
            <Text style={ui.sub}>
              {o.actions} actions · {o.gm_questions} GM asks (${o.gm_cost.toFixed(2)}) · {o.app_share == null ? '—' : `${o.app_share}% app`}
              {o.build ? ` · build ${o.build}` : ''}
            </Text>
            {o.favorite_first_move ? <Text style={ui.sub}>Usually starts with: {o.favorite_first_move}</Text> : null}
            {o.device ? <Text style={ui.sub}>{o.device.os} · {o.device.width}×{o.device.height} · {o.device.tz}</Text> : null}
          </View>
          <Text style={ui.link}>›</Text>
        </Row>
      ))}
    </Card>
  );
}

function Sessions({ a, owner, clear }: { a: Activity; owner: { id: string; name: string } | null; clear: () => void }) {
  const [open, setOpen] = useState<ActSession | null>(null);
  if (open) return <Replay session={open} close={() => setOpen(null)} />;
  return (
    <Card title={owner ? `${owner.name}'s sessions` : 'Latest sessions'} right={owner ? <Pressable onPress={clear}><Text style={ui.link}>Everyone</Text></Pressable> : undefined}>
      {a.sessions.map((x, i) => (
        <Row key={`${x.user_id}${x.start}`} first={i === 0} onPress={() => setOpen(x)}>
          <View style={ui.flex}>
            <Text style={ui.name}>{x.owner}</Text>
            <Text style={ui.sub}>{new Date(x.start).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} · {x.minutes} min · {x.source === 'website' ? 'website' : 'app'}</Text>
            <Text style={ui.sub} numberOfLines={1}>{x.steps.filter((st) => st.kind === 'screen' || st.kind === 'action').map((st) => st.name).join(' → ')}</Text>
          </View>
          <Text style={ui.link}>{x.frames.length ? '▶' : '›'}</Text>
        </Row>
      ))}
      {!a.sessions.length ? <Note>No sessions in this range.</Note> : null}
    </Card>
  );
}

// Plays the session's pictures in order with each tap drawn where it happened; the step list follows along.
function Replay({ session, close }: { session: ActSession; close: () => void }) {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  const frames = session.frames;
  useEffect(() => {
    if (!frames.length) return;
    supabase.storage.from('replays').createSignedUrls(frames.map((f) => f.path), 3600).then(({ data }) => {
      const out: Record<string, string> = {};
      for (const d of data ?? []) if (d.path && d.signedUrl) out[d.path] = d.signedUrl;
      setUrls(out);
      for (const u of Object.values(out).slice(0, 20)) Image.prefetch(u).catch(() => undefined);
    }).catch(() => undefined);
  }, [frames]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!playing) return;
    if (i >= frames.length - 1) { setPlaying(false); return; }
    const gap = new Date(frames[i + 1].at).getTime() - new Date(frames[i].at).getTime();
    timer.current = setTimeout(() => setI(i + 1), Math.max(350, Math.min(gap, 2500)));
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [playing, i, frames]);
  const frame = frames[i];
  const at = frame ? new Date(frame.at).getTime() : 0;
  const next = frames[i + 1] ? new Date(frames[i + 1].at).getTime() : Infinity;
  // taps that happened just before this picture (the picture shows what the tap did)
  const taps = useMemo(() => session.steps.filter((st) => st.kind === 'tap' && st.detail && new Date(st.t).getTime() <= at && new Date(st.t).getTime() > at - 2000), [session, at]);
  const current = session.steps.filter((st) => new Date(st.t).getTime() <= (frame ? next : Infinity));
  const w = Dimensions.get('window').width - 120;
  const h = (w * Dimensions.get('window').height) / Dimensions.get('window').width;
  return (
    <>
      <Card title={`${session.owner} · ${session.minutes} min`} right={<Pressable onPress={close}><Text style={ui.link}>Back</Text></Pressable>}>
        {frames.length ? (
          <View style={{ alignItems: 'center' }}>
            <View style={[s.screen, { width: w, height: h }]}>
              {frame && urls[frame.path] ? <Image source={{ uri: urls[frame.path] }} style={{ width: w, height: h }} /> : <ActivityIndicator color={colors.gold} style={{ marginTop: h / 2 - 10 }} />}
              {taps.map((t, k) => <View key={k} style={[s.tapRing, { left: (t.detail!.x ?? 0) * w - 14, top: (t.detail!.y ?? 0) * h - 14 }]} />)}
            </View>
            <View style={s.controls}>
              <Pressable onPress={() => setI(Math.max(0, i - 1))}><Text style={s.ctl}>‹</Text></Pressable>
              <Pressable onPress={() => { if (i >= frames.length - 1) setI(0); setPlaying(!playing); }}><Text style={s.ctl}>{playing ? 'Pause' : 'Play'}</Text></Pressable>
              <Pressable onPress={() => setI(Math.min(frames.length - 1, i + 1))}><Text style={s.ctl}>›</Text></Pressable>
            </View>
            <Note>{i + 1} of {frames.length} · {frame ? new Date(frame.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' }) : ''}</Note>
          </View>
        ) : <Note>No replay pictures for this session (website, or an older app build). The steps are below.</Note>}
      </Card>
      <Card title="Step by step">
        {session.steps.map((st, k) => (
          <Row key={k} first={k === 0}>
            <Text style={[s.stepTime, current.length - 1 === k && frames.length ? ui.gold : null]}>{st.at}</Text>
            <Text style={[ui.value, ui.flex, st.kind === 'tap' || st.kind === 'swipe' ? s.minor : null, st.kind === 'action' ? ui.gold : null]}>{st.name}</Text>
            {st.secs ? <Text style={ui.sub}>{st.secs}s</Text> : null}
          </Row>
        ))}
      </Card>
    </>
  );
}

type Qa = { q: string; a: string; cost?: number | null };

function Ask({ days }: { days: number }) {
  const [text, setText] = useState('');
  const [deep, setDeep] = useState(false);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<Qa[]>([]);
  const ideas = ['What do owners do first on Thursdays?', 'Who hasn\'t set a lineup this week?', 'Which pages should we cut or improve?', 'How did usage change after the last build?'];
  const ask = async (q: string) => {
    if (!q.trim() || busy) return;
    setBusy(true);
    setText('');
    const history = log.flatMap((x) => [{ role: 'user', content: x.q }, { role: 'assistant', content: x.a }]);
    try {
      const r = await apiSend<{ answer: string; cost: number | null }>('POST', '/commissioner/activity/ask', { question: q, days, tz: tz(), deep, history });
      setLog((l) => [...l, { q, a: r.answer, cost: r.cost }]);
    } catch (e: any) {
      setLog((l) => [...l, { q, a: e?.message ?? 'Something went wrong.' }]);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Card title="Ask about usage">
        <TextInput value={text} onChangeText={setText} placeholder="Ask anything about how owners use the app" placeholderTextColor={colors.muted}
          style={s.input} multiline onSubmitEditing={() => ask(text)} />
        <View style={s.askRow}>
          <Pressable onPress={() => setDeep(!deep)}><Text style={ui.sub}>{deep ? '☑' : '☐'} Dig deeper (slower, a few cents)</Text></Pressable>
          <Pressable onPress={() => ask(text)} style={s.send}><Text style={s.sendText}>{busy ? '…' : 'Ask'}</Text></Pressable>
        </View>
        {!log.length ? ideas.map((q) => <Pressable key={q} onPress={() => ask(q)}><Text style={[ui.link, { marginTop: 8 }]}>{q}</Text></Pressable>) : null}
      </Card>
      {[...log].reverse().map((x, k) => (
        <Card key={k}>
          <Text style={ui.name}>{x.q}</Text>
          <Text style={[ui.value, { marginTop: 6, lineHeight: 20 }]}>{x.a}</Text>
          {x.cost != null ? <Text style={[ui.sub, { marginTop: 6 }]}>${x.cost.toFixed(3)}</Text> : null}
        </Card>
      ))}
      {busy ? <ActivityIndicator color={colors.gold} /> : null}
    </>
  );
}

const s = StyleSheet.create({
  chips: { flexDirection: 'row', gap: 6, marginBottom: 10, flexWrap: 'wrap' },
  chip: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 14, backgroundColor: colors.card },
  chipOn: { backgroundColor: colors.gold },
  chipText: { color: colors.muted, fontSize: 12, fontWeight: '700' },
  chipTextOn: { color: colors.bg },
  tabs: { marginBottom: 12, flexGrow: 0 },
  tab: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: colors.line },
  tabOn: { borderColor: colors.gold, backgroundColor: '#2A2A1A' },
  tabText: { color: colors.muted, fontSize: 13, fontWeight: '700' },
  tabTextOn: { color: colors.gold },
  barRow: { marginTop: 9 },
  barHead: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4, gap: 8 },
  barLabel: { color: colors.text, fontSize: 13, flex: 1 },
  barRight: { color: colors.muted, fontSize: 12 },
  track: { height: 6, borderRadius: 3, backgroundColor: colors.card, overflow: 'hidden' },
  fill: { height: '100%', backgroundColor: colors.gold, borderRadius: 3 },
  spark: { flexDirection: 'row', alignItems: 'flex-end', height: 70, gap: 2, marginTop: 8 },
  sparkCol: { flex: 1, height: '100%', justifyContent: 'flex-end' },
  sparkBar: { backgroundColor: colors.gold, borderRadius: 2, minHeight: 2 },
  inset: { paddingBottom: 10, paddingLeft: 4 },
  h: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1, marginTop: 12, marginBottom: 2 },
  tapRow: { flexDirection: 'row', alignItems: 'center', gap: 14, marginTop: 12 },
  phone: { borderRadius: 14, borderWidth: 2, borderColor: colors.line, backgroundColor: colors.bg, overflow: 'hidden' },
  dot: { position: 'absolute', width: 8, height: 8, borderRadius: 4, backgroundColor: colors.gold, opacity: 0.45 },
  heatRow: { flexDirection: 'row', alignItems: 'center' },
  heatDay: { width: 32, color: colors.muted, fontSize: 11 },
  heatHour: { color: colors.muted, fontSize: 10 },
  screen: { borderRadius: 22, borderWidth: 3, borderColor: colors.line, overflow: 'hidden', backgroundColor: colors.bg },
  tapRing: { position: 'absolute', width: 28, height: 28, borderRadius: 14, borderWidth: 3, borderColor: '#FF5A5A', backgroundColor: 'rgba(255,90,90,0.25)' },
  controls: { flexDirection: 'row', gap: 28, marginTop: 12, alignItems: 'center' },
  ctl: { color: colors.gold, fontSize: 18, fontWeight: '800' },
  stepTime: { color: colors.muted, fontSize: 11, width: 78 },
  minor: { color: colors.muted, fontSize: 12 },
  input: { color: colors.text, backgroundColor: colors.card, borderRadius: 12, padding: 12, minHeight: 60, fontSize: 15, marginTop: 6 },
  askRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: 10 },
  send: { backgroundColor: colors.gold, borderRadius: 10, paddingHorizontal: 18, paddingVertical: 8 },
  sendText: { color: colors.bg, fontWeight: '800' },
});
