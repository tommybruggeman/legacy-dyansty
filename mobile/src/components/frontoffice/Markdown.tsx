import { StyleSheet, Text, View } from 'react-native';

import { colors } from '../../lib/theme';

// Just enough Markdown for Assistant GM answers: headings, bullets,
// numbered lists, **bold** and simple tables (shown as rows of text).

function Inline({ text, style }: { text: string; style?: object }) {
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
      if (isTableRule(trimmed)) continue;
      const row = cells(trimmed);
      const header = i + 1 < lines.length && isTableRule(lines[i + 1]);
      blocks.push(<Inline key={`t${i}`} text={row.join('  ·  ')} style={header ? styles.tableHead : styles.tableRow} />);
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
  tableHead: { color: colors.gold, fontSize: 13, fontWeight: '800' },
  tableRow: { fontSize: 14, lineHeight: 20 },
});
