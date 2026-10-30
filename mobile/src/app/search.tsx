import AsyncStorage from '@react-native-async-storage/async-storage';
import { router } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';
import { Keyboard, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { PlayerFace } from '../components/PlayerFace';
import { ErrorState, Loading } from '../components/ScreenState';
import { money, ui } from '../components/ui';
import { type LeagueOwner, type LeaguePlayer, type LeaguePlayers, useApi } from '../lib/api';
import { nameMatches } from '../lib/players';
import { colors } from '../lib/theme';

export { ScreenError as ErrorBoundary } from '../components/ScreenState';

// Pull-down search: one box for every player in the league (rostered or free agent) and every team.
// Uses the same /market/players list as Market > Players, so it's usually already loaded.
const MAX_PLAYERS = 25;
const RECENT_KEY = 'search:recent';
const MAX_RECENT = 6;
type Recent = { kind: 'player' | 'team'; id: string; label: string };

const initials = (name: string) => name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();

export default function Search() {
  const insets = useSafeAreaInsets();
  const { data, error, loading, reload } = useApi<LeaguePlayers>('/market/players');
  const [text, setText] = useState('');
  const [recent, setRecent] = useState<Recent[]>([]);

  useEffect(() => {
    AsyncStorage.getItem(RECENT_KEY).then((saved) => saved && setRecent(JSON.parse(saved))).catch(() => undefined);
  }, []);

  const needle = text.trim();
  const players = useMemo(
    () => (needle ? (data?.players ?? []).filter((p) => nameMatches(p.name, needle)).slice(0, MAX_PLAYERS) : []),
    [data, needle],
  );
  const teams = useMemo(
    () => (needle ? (data?.owners ?? []).filter((o) => nameMatches(`${o.owner_name} ${o.team_name}`, needle)) : []),
    [data, needle],
  );

  const open = (item: Recent) => {
    const next = [item, ...recent.filter((r) => !(r.kind === item.kind && r.id === item.id))].slice(0, MAX_RECENT);
    setRecent(next);
    AsyncStorage.setItem(RECENT_KEY, JSON.stringify(next)).catch(() => undefined);
    Keyboard.dismiss();
    router.push(item.kind === 'player' ? `/player/${item.id}` : `/team/${item.id}`);
  };
  const seeAll = () => {
    router.back();
    router.navigate({ pathname: '/market', params: { q: needle } });
  };

  return (
    <View style={[styles.page, { paddingTop: insets.top + 8 }]}>
      <View style={styles.bar}>
        <TextInput
          value={text}
          onChangeText={setText}
          placeholder="Search players and teams"
          placeholderTextColor={colors.muted}
          style={styles.input}
          autoFocus
          autoCorrect={false}
          returnKeyType="search"
          clearButtonMode="while-editing"
        />
        <Pressable onPress={() => router.back()} hitSlop={10}>
          <Text style={ui.link}>Cancel</Text>
        </Pressable>
      </View>
      {loading && !data ? <Loading /> : error && !data ? <ErrorState message={error} onRetry={reload} /> : (
        <ScrollView keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag" contentContainerStyle={{ paddingBottom: insets.bottom + 24 }}>
          {!needle ? (
            recent.length ? (
              <>
                <Text style={styles.group}>RECENT</Text>
                {recent.map((r) => (
                  <Pressable key={`${r.kind}${r.id}`} onPress={() => open(r)} style={styles.row}>
                    <Text style={[ui.value, ui.flex]}>{r.label}</Text>
                    <Text style={ui.sub}>{r.kind === 'team' ? 'Team' : ''}</Text>
                  </Pressable>
                ))}
              </>
            ) : <Text style={styles.empty}>Find any player in the league, rostered or free agent, or any team.</Text>
          ) : null}
          {players.length ? <Text style={styles.group}>PLAYERS</Text> : null}
          {players.map((p) => <PlayerRow key={p.player_id} p={p} onPress={() => open({ kind: 'player', id: p.player_id, label: p.name })} />)}
          {teams.length ? <Text style={styles.group}>TEAMS</Text> : null}
          {teams.map((o) => <TeamRow key={o.team_id} o={o} onPress={() => open({ kind: 'team', id: o.team_id, label: o.is_mine ? 'Your team' : `${o.owner_name}'s team` })} />)}
          {needle && !players.length && !teams.length ? <Text style={styles.empty}>Nothing matches “{needle}”.</Text> : null}
          {players.length ? (
            <Pressable onPress={seeAll} style={styles.seeAll}>
              <Text style={ui.link}>See all in Players, with filters ›</Text>
            </Pressable>
          ) : null}
        </ScrollView>
      )}
    </View>
  );
}

function PlayerRow({ p, onPress }: { p: LeaguePlayer; onPress: () => void }) {
  const contract = p.salary != null ? ` · ${money(p.salary)}${p.years_left ? ` · ${p.years_left}y` : ''}` : '';
  return (
    <Pressable onPress={onPress} style={styles.row}>
      <PlayerFace name={p.name} team={p.nfl_team} pos={p.pos} size={40} hurt={!!p.injury} />
      <View style={ui.flex}>
        <Text style={ui.name} numberOfLines={1}>{p.name}</Text>
        <Text style={ui.sub} numberOfLines={1}>{p.nfl_team}{p.team_id ? contract : ' · free agent'}{p.designation ? ` · ${p.designation.toUpperCase()}` : ''}</Text>
      </View>
      <View style={ui.right}>
        <Text style={[styles.owner, !p.team_id && styles.fa]}>{p.team_id ? (p.is_mine ? 'You' : p.owner_name) : 'FA'}</Text>
        <Text style={ui.sub}>{p.rank_ppg != null ? `${p.rank_ppg.toFixed(1)} PPG` : '—'}</Text>
      </View>
    </Pressable>
  );
}

function TeamRow({ o, onPress }: { o: LeagueOwner; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={styles.row}>
      <View style={styles.badge}><Text style={styles.badgeText}>{initials(o.owner_name)}</Text></View>
      <View style={ui.flex}>
        <Text style={ui.name} numberOfLines={1}>{o.is_mine ? 'You' : o.owner_name}</Text>
        <Text style={ui.sub} numberOfLines={1}>{o.team_name}</Text>
      </View>
      <Text style={ui.sub}>›</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.bg, paddingHorizontal: 16 },
  bar: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 8 },
  input: {
    flex: 1, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.line, borderRadius: 22,
    paddingHorizontal: 16, paddingVertical: 11, color: colors.text, fontSize: 15,
  },
  group: { color: colors.gold, fontSize: 11, fontWeight: '800', letterSpacing: 1, marginTop: 16, marginBottom: 2 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 10, borderTopWidth: 1, borderTopColor: colors.card },
  owner: { color: colors.text, fontSize: 13, fontWeight: '700' },
  fa: { color: colors.live },
  badge: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.card, borderWidth: 2, borderColor: colors.gold, alignItems: 'center', justifyContent: 'center' },
  badgeText: { color: colors.gold, fontWeight: '800', fontSize: 13 },
  empty: { color: colors.muted, fontSize: 13, textAlign: 'center', marginTop: 32, paddingHorizontal: 24 },
  seeAll: { alignItems: 'center', paddingVertical: 16 },
});
