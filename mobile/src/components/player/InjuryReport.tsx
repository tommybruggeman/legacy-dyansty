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
  const days = week?.days ?? [];
  const missed = h.missed ?? [];
  const injury = [h.detail, r?.detail && r.detail.toLowerCase() !== (h.detail ?? '').toLowerCase() ? r.detail : null].filter(Boolean).join(' · ');
  const est = r?.estimate?.past_usual ? 'Past the usual time out' : weeks(r?.estimate?.weeks);
  const newsDate = shortDate(r?.news?.date);
  const newsText = r?.news?.text ?? h.update;

  return (
    <View style={styles.card}>
      <View style={styles.head}>
        <Text style={styles.title}>Injury report</Text>
        <Text style={[styles.pill, h.healthy ? styles.pillOk : styles.pillOut]}>{h.healthy ? 'HEALTHY' : h.status.toUpperCase()}</Text>
      </View>
      {week && !h.healthy ? <Text style={styles.meta}>Week {week.week} report</Text> : null}

      {h.healthy ? (
        <Text style={styles.small}>Not on the injury report.</Text>
      ) : (
        <View style={styles.table}>
          <Row label="Injury">
            <Text style={styles.value}>
              <Text style={styles.strong}>{injury || week?.injury || 'Not listed'}</Text>
              {r?.hurt ? ` · hurt Week ${r.hurt.week}${r.hurt.opp ? ` ${r.hurt.opp}` : ''}` : ''}
            </Text>
          </Row>
          {r?.missed_weeks.length ? (
            <Row label="Games missed">
              <Text style={styles.value}>{r.missed_weeks.length} so far ({r.missed_weeks.map((w) => `W${w}`).join(', ')})</Text>
            </Row>
          ) : null}
          {days.length ? (
            <Row label={`Practice W${week?.week}`}>
              <View style={styles.days}>
                {days.map((d, i) => {
                  const s = DAY[d.practice ?? ''];
                  return (
                    <View key={`${d.day}-${i}`} style={styles.day}>
                      <Text style={styles.dayName}>{(d.day ?? '').toUpperCase()}</Text>
                      <Text style={[styles.dayValue, { color: s?.color ?? colors.muted }]}>{s?.label ?? '—'}</Text>
                    </View>
                  );
                })}
              </View>
            </Row>
          ) : week?.practice ? (
            <Row label={`Practice W${week.week}`}><Text style={styles.value}>{week.practice}</Text></Row>
          ) : null}
          {week?.game_status ? (
            <Row label="Game status"><Text style={styles.value}>{week.game_status} (W{week.week})</Text></Row>
          ) : null}
          {r?.on_ir && r.eligible_week ? (
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

      {!h.healthy && newsText ? (
        <View>
          <Text style={styles.section}>LATEST NEWS</Text>
          <View style={styles.quote}>
            <Text style={styles.quoteText}>{newsText}</Text>
            <Text style={styles.quoteSrc}>ESPN{newsDate ? ` · ${newsDate}` : ''}</Text>
          </View>
        </View>
      ) : null}

      <View>
        <Text style={styles.section}>HISTORY</Text>
        {missed.length ? missed.map((m, i) => (
          <View key={`${m.injury}-${m.season}-${i}`} style={[styles.hist, i === missed.length - 1 && styles.last]}>
            <Text style={styles.histName}>{m.injury.charAt(0).toUpperCase() + m.injury.slice(1)}</Text>
            <Text style={styles.histMeta}>{m.season} · {m.games} game{m.games === 1 ? '' : 's'}{m.back ? '' : ', ongoing'}</Text>
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
  section: { color: '#8E8676', fontSize: 10, fontWeight: '700', letterSpacing: 1.2, marginTop: 6, marginBottom: 6 },
  quote: { borderLeftWidth: 3, borderLeftColor: colors.gold, paddingLeft: 10, gap: 3 },
  quoteText: { color: colors.text, fontSize: 13, lineHeight: 19 },
  quoteSrc: { color: '#8E8676', fontSize: 11 },
  hist: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 7, borderBottomWidth: 1, borderBottomColor: colors.line },
  last: { borderBottomWidth: 0 },
  histName: { color: colors.text, fontSize: 14 },
  histMeta: { color: colors.muted, fontSize: 13 },
});
