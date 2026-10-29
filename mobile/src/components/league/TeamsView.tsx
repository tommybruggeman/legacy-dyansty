import { router } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';

import { type TeamList, useApi } from '../../lib/api';
import { colors } from '../../lib/theme';
import { LatestActivity } from './ActivityView';
import { ErrorState, Loading } from '../ScreenState';
import { Card, Row, money, ordinal, record, ui } from '../ui';

const initials = (name: string) =>
  name.split(' ').filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join('');

// Every owner in standings order (tap one to open their team page), then the league's newest moves.
export function TeamsView() {
  const { data, error, loading, reload } = useApi<TeamList>('/teams');

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;

  return (
    <>
      <Card style={styles.list}>
        {data.teams.map((team, i) => (
          <Row key={team.team_id} first={i === 0} onPress={() => router.push(`/team/${team.team_id}`)}>
            <View style={[styles.avatar, team.is_me && styles.avatarMe]}>
              <Text style={styles.avatarText}>{initials(team.owner_name)}</Text>
            </View>
            <View style={ui.flex}>
              <Text style={[ui.name, team.is_me && ui.gold]} numberOfLines={1}>{team.owner_name}</Text>
              <Text style={ui.sub} numberOfLines={1}>
                {team.team_name} · {record(team.wins, team.losses, team.ties)} · {ordinal(team.rank)}
              </Text>
            </View>
            <View style={ui.right}>
              <Text style={ui.sub}>{money(team.cap_space)} cap</Text>
            </View>
          </Row>
        ))}
      </Card>
      <LatestActivity />
    </>
  );
}

const styles = StyleSheet.create({
  list: { paddingVertical: 4 },
  avatar: {
    width: 38, height: 38, borderRadius: 19, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.line,
    alignItems: 'center', justifyContent: 'center',
  },
  avatarMe: { borderColor: colors.gold },
  avatarText: { color: colors.gold, fontWeight: '800', fontSize: 13 },
});
