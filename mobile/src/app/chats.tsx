import { router } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Card, Note, Row, Screen, Title, ui } from '../components/ui';
import { type Conversations, useApi } from '../lib/api';

const when = (iso: string | null) => {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString() ? 'Today' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
};

// Past Assistant GM chats (shared with the website's Front Office).
export default function Chats() {
  const { data, error, loading, reload } = useApi<Conversations>('/front-office/conversations');

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  const open = (c: string) => router.navigate({ pathname: '/front-office', params: { c } });

  return (
    <Screen loading={loading} onRefresh={reload}>
      <Back label="Front Office" />
      <Title right={<Pressable onPress={() => open('new')} hitSlop={8}><Text style={ui.link}>+ New</Text></Pressable>}>Conversations</Title>
      <Card>
        {data.conversations.length === 0 ? <Note>No chats yet. Ask your assistant GM anything.</Note> : null}
        {data.conversations.map((c, i) => (
          <Row key={c.id} first={i === 0} onPress={() => open(c.id)}>
            <View style={ui.flex}>
              <Text style={ui.name} numberOfLines={2}>{c.title}</Text>
            </View>
            <Text style={[ui.sub, styles.date]}>{when(c.updated_at)}</Text>
          </Row>
        ))}
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  date: { alignSelf: 'flex-start', marginTop: 2 },
});
