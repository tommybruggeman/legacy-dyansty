import { StyleSheet, Text, View } from 'react-native';

import { colors } from '../../lib/theme';

// Just enough Markdown for Assistant GM answers: headings, bullets,
// numbered lists, **bold** and tables (a real grid: first column wide, numbers right-aligned).

function Inline({ text, style }: { text: string; style?: object | (object | false)[] }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return (
    <Text style={[styles.text, style]}>
      {parts.map((part, i) =>
        part.startsWith('**') && part.endsWith('**') ? (
          <Text key={i} style={styles.bold}>{part.slice(2, -2)}</Text>
        ) : (
          part.replace(/(^|\s)\*([^*]+)\*/g, '$1$2').replace(/`([^`]+)`/g, '$1')
        ),
      )}
    </Text>
  );
}

const isTableRule = (line: string) => /^\|?\s*:?-{2,}/.test(line.trim());
const cells = (line: string) => line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
const numeric = (cell: string) => /^[-+$]?[\d.,]+%?$|^[-+]?\$?[\d.,]+(\s?(ppg|pts|yrs?))?$/i.test(cell.replace(/\*\*/g, ''));

function Table({ rows }: { rows: { cells: string[]; header: boolean }[] }) {
  const width = Math.max(...rows.map((r) => r.cells.length));
  return (
    <View style={styles.table}>
      {rows.map((r, ri) => (
        <View key={ri} style={[styles.tr, r.header ? styles.thRow : ri > 0 && styles.trLine]}>
          {Array.from({ length: width }, (_, ci) => {
            const cell = r.cells[ci] ?? '';
            const right = ci > 0 && (r.header || numeric(cell));
            return (
              <View key={ci} style={[ci === 0 ? styles.tdFirst : styles.td, right && styles.tdRight]}>
                <Inline text={cell} style={[r.header ? styles.th : styles.tdText, right && styles.alignRight]} />
              </View>
            );
          })}
        </View>
      ))}
    </View>
  );
}

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r/g, '').split('\n');
  const blocks: React.ReactNode[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push(<Inline key={`p${blocks.length}`} text={para.join(' ')} />);
    para = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed) { flush(); continue; }
    const heading = trimmed.match(/^#{1,6}\s+(.*)$/);
    const bullet = trimmed.match(/^[-*•]\s+(.*)$/);
    const numbered = trimmed.match(/^(\d+)[.)]\s+(.*)$/);
    if (heading) {
      flush();
      blocks.push(<Inline key={`h${i}`} text={heading[1]} style={styles.heading} />);
    } else if (bullet || numbered) {
      flush();
      blocks.push(
        <View key={`b${i}`} style={styles.item}>
          <Text style={styles.marker}>{bullet ? '•' : `${numbered![1]}.`}</Text>
          <View style={styles.flex}><Inline text={bullet ? bullet[1] : numbered![2]} /></View>
        </View>,
      );
    } else if (trimmed.startsWith('|')) {
      flush();
      const rows: { cells: string[]; header: boolean }[] = [];
      const start = i;
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        const row = lines[i].trim();
        if (!isTableRule(row)) rows.push({ cells: cells(row), header: i + 1 < lines.length && isTableRule(lines[i + 1]) });
        i++;
      }
      i--;
      if (rows.length) blocks.push(<Table key={`t${start}`} rows={rows} />);
    } else {
      para.push(trimmed);
    }
  }
  flush();
  return <View style={styles.wrap}>{blocks}</View>;
}

const styles = StyleSheet.create({
  wrap: { gap: 8 },
  flex: { flex: 1 },
  text: { color: colors.text, fontSize: 15, lineHeight: 22 },
  bold: { fontWeight: '800' },
  heading: { color: colors.gold, fontWeight: '800', fontSize: 15 },
  item: { flexDirection: 'row', gap: 8 },
  marker: { color: colors.gold, fontSize: 15, lineHeight: 22, minWidth: 14 },
  table: { borderRadius: 10, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.gold + '55', overflow: 'hidden' },
  tr: { flexDirection: 'row', paddingHorizontal: 10, paddingVertical: 7, gap: 8 },
  thRow: { backgroundColor: colors.gold + '1F' },
  trLine: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.gold + '33' },
  tdFirst: { flex: 1.6 },
  td: { flex: 1 },
  tdRight: { alignItems: 'flex-end' },
  th: { color: colors.gold, fontSize: 12, lineHeight: 16, fontWeight: '800' },
  tdText: { fontSize: 14, lineHeight: 19 },
  alignRight: { textAlign: 'right' },
});
