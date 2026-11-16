import { StyleSheet, Text, View } from 'react-native';

import type { PlayerPage } from '../../lib/api';
import { colors } from '../../lib/theme';

// Player page Health card, laid out like the NFL injury sheet: labelled rows, then news and history.
type Health = PlayerPage['health'];

const RED = '#E8826F';
const AMBER = '#E2B655';
const DAY: Record<string, { label: string; color: string }> = {
  DNP: { label: 'DNP', color: RED },
  Limited: { label: 'LP', color: AMBER },
  Full: { label: 'FP', color: colors.live },
};

function shortDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(`${iso.slice(0, 10)}T12:00:00`);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function weeks(range: [number, number | null] | undefined): string | null {
  if (!range) return null;
  const [lo, hi] = range;
  return hi && hi !== lo ? `Week ${lo}–${hi}` : `Week ${lo}`;
}

function tone(pct: number): string {
  return pct >= 65 ? colors.live : pct >= 40 ? AMBER : RED;
}

function statusColor(status: string): string {
  const s = status.toLowerCase();
  return s === 'questionable' ? AMBER : s === 'out' || s === 'doubtful' || s === 'ir' ? RED : colors.text;
}

function Trio({ label, value, sub, color }: { label: string; value: string; sub: string; color?: string }) {
  return (
    <View style={styles.trioBox}>
      <Text style={styles.trioLabel}>{label}</Text>
      <Text style={[styles.trioValue, color ? { color } : null]}>{value}</Text>
      <Text style={styles.trioSub}>{sub}</Text>
    </View>
  );
}

const PRACTICE_SHORT: Record<string, string> = { DNP: 'DNP', Limited: 'LP', Full: 'FP' };

// The week track: one block per practice day, coloured by how much he did, then game day.
// A healthy player gets full green days; a player ruled out gets a red game-day block.
function WeekTrack({ h }: { h: Health }) {
  const week = h.this_week;
  const days = h.healthy ? [] : week?.days ?? [];
  const blocks: { key: string; day: string; sub: string; color: string }[] = days.length
    ? days.map((d, i) => ({ key: `${d.day}-${i}`, day: (d.day ?? '').toUpperCase(), sub: PRACTICE_SHORT[d.practice ?? ''] ?? '—', color: DAY[d.practice ?? '']?.color ?? '#23392F' }))
    : h.healthy
      ? ['WED', 'THU', 'FRI'].map((d) => ({ key: d, day: d, sub: 'OK', color: colors.live }))
      : [];
  if (!blocks.length) return null;
  const status = (week?.game_status ?? '').toLowerCase();
  const game = status === 'out' ? { sub: 'OUT', color: RED } : status === 'doubtful' ? { sub: 'DOUBT', color: RED } : { sub: 'GAME', color: '#23392F' };
  return (
    <View>
      <Text style={styles.section}>THIS WEEK</Text>
      <View style={styles.track}>
        {[...blocks, { key: 'game', day: 'GAME', sub: game.sub, color: game.color }].map((b) => (
          <View key={b.key} style={styles.trackCol}>
            <View style={[styles.trackBar, { backgroundColor: b.color }]} />
            <Text style={styles.trackDay}>{b.day}</Text>
            <Text style={styles.trackSub}>{b.sub}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <View style={styles.rowValue}>{children}</View>
    </View>
  );
}

export function InjuryReport({ h }: { h: Health }) {
  const r = h.report ?? null;
  const week = h.this_week;
  const missed = h.missed ?? [];
  const injury = [h.detail, r?.detail && r.detail.toLowerCase() !== (h.detail ?? '').toLowerCase() ? r.detail : null].filter(Boolean).join(' · ');
  const est = r?.estimate?.past_usual ? 'Past the usual time out' : weeks(r?.estimate?.weeks);
  const call = h.call ?? null;
  const summaryText = h.summary?.text ?? r?.news?.text ?? h.update;
  const summaryDate = shortDate(h.summary?.date ?? r?.news?.date);

  return (
    <View style={styles.card}>
      <View style={styles.head}>
        <Text style={styles.title}>Injury report</Text>
        <Text style={[styles.pill, h.healthy ? styles.pillOk : styles.pillOut]}>{h.healthy ? 'HEALTHY' : h.status.toUpperCase()}</Text>
      </View>
      {week && !h.healthy ? <Text style={styles.meta}>Week {week.week} report{week.game_status ? ` · ${week.game_status}` : ''}</Text> : null}

      <WeekTrack h={h} />

      {h.healthy ? (
        <Text style={styles.small}>Not on the injury report.</Text>
      ) : null}

      {call ? (
        <>
          <View style={[styles.verdict, { borderColor: tone(call.pct) + '66', backgroundColor: tone(call.pct) + '14' }]}>
            <View style={{ flex: 1 }}>
              <Text style={styles.verdictTitle}>{call.verdict === 'Coin flip' ? 'Coin flip to play' : `${call.verdict} to play`}</Text>
              {call.why ? <Text style={styles.note}>{call.why}</Text> : null}
            </View>
            <Text style={[styles.verdictPct, { color: tone(call.pct) }]}>{call.pct}%</Text>
          </View>
          <View style={styles.trio}>
            <Trio label="OFFICIAL" value={call.official ?? 'No status yet'} sub={call.official ? 'Team report' : 'Comes out Friday'} color={call.official ? statusColor(call.official) : colors.muted} />
            <Trio label="REPORTED" value={call.reported ?? 'No word yet'} sub={call.reported ? `ESPN${shortDate(call.reported_date) ? ` · ${shortDate(call.reported_date)}` : ''}` : 'Coach and beat writers'} />
            <Trio label="OUR CALL" value={call.ours} sub="Check again before kickoff" color={call.ours === 'Start him' ? colors.live : call.ours === 'Sit him' ? RED : AMBER} />
          </View>
        </>
      ) : null}

      {!h.healthy && summaryText ? (
        <View>
          <Text style={styles.section}>WHERE HE'S AT</Text>
          <View style={styles.quote}>
            <Text style={styles.quoteText}>{summaryText}</Text>
            <Text style={styles.quoteSrc}>{h.summary?.source ?? 'ESPN'}{summaryDate ? ` · ${summaryDate}` : ''}</Text>
          </View>
        </View>
      ) : null}

      {h.healthy ? null : (
        <View style={styles.table}>
          <Row label="Injury">
            <Text style={styles.value}>
              <Text style={styles.strong}>{injury || week?.injury || 'Not listed'}</Text>
              {r?.hurt ? ` · hurt ${r.hurt.season ? `${r.hurt.season} ` : ''}Week ${r.hurt.week}${r.hurt.opp ? ` ${r.hurt.opp}` : ''}` : ''}
            </Text>
          </Row>
          {r?.missed_weeks.length ? (
            <Row label="Games missed">
              <Text style={styles.value}>
                {r.missed_weeks.length + (r.missed_before ?? 0)} so far
                {r.missed_before ? ` (${r.missed_before} last season, ${r.missed_weeks.length} this season)` : ` (${weekList(r.missed_weeks)})`}
              </Text>
            </Row>
          ) : null}
          {r?.on_ir && r.eligible_now ? (
            <Row label="Eligible back">
              <Text style={[styles.value, styles.strong]}>Now</Text>
              <Text style={styles.note}>His 4 IR games are done; he's back once the team activates him</Text>
            </Row>
          ) : r?.on_ir && r.eligible_week ? (
            <Row label="Eligible back">
              <Text style={styles.value}><Text style={styles.strong}>Week {r.eligible_week}</Text> at the earliest</Text>
              <Text style={styles.note}>IR is a 4-game minimum</Text>
            </Row>
          ) : null}
          {est || h.estimate ? (
            <Row label="Our estimate">
              {est ? <Text style={[styles.value, styles.strong]}>{est}</Text> : null}
              {h.estimate ? <Text style={styles.note}>{h.estimate.text}</Text> : null}
            </Row>
          ) : null}
          {h.return ? (
            <Row label="ESPN estimate"><Text style={styles.value}>Back {shortDate(h.return) ?? h.return}</Text></Row>
          ) : null}
        </View>
      )}

      <View>
        <Text style={styles.section}>HISTORY</Text>
        {missed.length ? missed.map((m, i) => (
          <View key={`${m.injury}-${m.season}-${i}`} style={[styles.hist, i === missed.length - 1 && styles.last]}>
            <Text style={styles.histName}>{m.injury.charAt(0).toUpperCase() + m.injury.slice(1)}</Text>
            <Text style={styles.histMeta}>{m.season ? `${m.season} · ` : ''}{m.games} game{m.games === 1 ? '' : 's'}{m.back ? '' : ', ongoing'}</Text>
          </View>
        )) : <Text style={styles.small}>No missed games on file.</Text>}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: colors.card, borderRadius: 16, padding: 16, gap: 10 },
  head: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  title: { color: colors.text, fontSize: 15, fontWeight: '700' },
  meta: { color: colors.muted, fontSize: 12, marginTop: -6 },
  pill: { fontSize: 10, fontWeight: '800', letterSpacing: 0.8, borderRadius: 10, overflow: 'hidden', paddingHorizontal: 8, paddingVertical: 3 },
  pillOk: { color: colors.live, backgroundColor: '#143D2B' },
  pillOut: { color: RED, backgroundColor: '#2A1D18' },
  table: { borderTopWidth: 1, borderTopColor: colors.line },
  row: { flexDirection: 'row', gap: 10, paddingVertical: 9, borderBottomWidth: 1, borderBottomColor: colors.line },
  rowLabel: { width: 96, color: '#8E8676', fontSize: 12, paddingTop: 1 },
  rowValue: { flex: 1, gap: 2 },
  value: { color: colors.text, fontSize: 14 },
  strong: { fontWeight: '700' },
  note: { color: colors.muted, fontSize: 12 },
  small: { color: colors.muted, fontSize: 13 },
  days: { flexDirection: 'row', gap: 6 },
  day: { flex: 1, backgroundColor: colors.row, borderRadius: 8, paddingVertical: 5, alignItems: 'center' },
  dayName: { color: '#8E8676', fontSize: 10, fontWeight: '600', letterSpacing: 0.5 },
  dayValue: { fontSize: 12, fontWeight: '800' },
  track: { flexDirection: 'row', gap: 5 },
  trackCol: { flex: 1, alignItems: 'center', gap: 4 },
  trackBar: { alignSelf: 'stretch', height: 12, borderRadius: 6 },
  trackDay: { color: colors.muted, fontSize: 11, fontWeight: '700' },
  trackSub: { color: '#8E8676', fontSize: 10, marginTop: -3 },
  verdict: { flexDirection: 'row', alignItems: 'center', gap: 10, borderWidth: 1, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 10 },
  verdictTitle: { color: colors.text, fontSize: 15, fontWeight: '700' },
  verdictPct: { fontSize: 24, fontWeight: '800' },
  trio: { flexDirection: 'row', gap: 6 },
  trioBox: { flex: 1, backgroundColor: colors.row, borderRadius: 12, padding: 9, gap: 3 },
  trioLabel: { color: '#8E8676', fontSize: 9, fontWeight: '700', letterSpacing: 0.8 },
  trioValue: { color: colors.text, fontSize: 13, fontWeight: '800' },
  trioSub: { color: colors.muted, fontSize: 11 },
  section: { color: '#8E8676', fontSize: 10, fontWeight: '700', letterSpacing: 1.2, marginTop: 6, marginBottom: 6 },
  quote: { borderLeftWidth: 3, borderLeftColor: colors.gold, paddingLeft: 10, gap: 3 },
  quoteText: { color: colors.text, fontSize: 13, lineHeight: 19 },
  quoteSrc: { color: '#8E8676', fontSize: 11 },
  hist: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 7, borderBottomWidth: 1, borderBottomColor: colors.line },
  last: { borderBottomWidth: 0 },
  histName: { color: colors.text, fontSize: 14 },
  histMeta: { color: colors.muted, fontSize: 13 },
});

// "W1–W7, W9, W10": runs of back-to-back weeks collapse into a range.
function weekList(weeks: number[]): string {
  const out: string[] = [];
  let start = weeks[0];
  let prev = weeks[0];
  for (const w of [...weeks.slice(1), Infinity]) {
    if (w === prev + 1) { prev = w; continue; }
    out.push(start === prev ? `W${start}` : prev === start + 1 ? `W${start}, W${prev}` : `W${start}–W${prev}`);
    start = w; prev = w;
  }
  return out.join(', ');
}
