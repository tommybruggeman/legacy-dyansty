import { useState } from 'react';
import { ActivityIndicator, Alert, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { Avatar } from '../components/Pictures';
import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Card, Note, Row, Screen, Title, money, ui } from '../components/ui';
import { type ContractFix, type DropPreview, type Player, type TeamPage, type TeamSummary, apiGet, apiSend, clearApiCache, useApi } from '../lib/api';
import { colors } from '../lib/theme';

// Commissioner: pick a team, then a player, then drop him (with the dead cap shown first) or fix his contract.
export default function CommishTeam() {
  const teams = useApi<{ teams: TeamSummary[] }>('/teams');
  const [teamId, setTeamId] = useState<string | null>(null);
  if (teams.loading && !teams.data) return <Loading />;
  if (teams.error && !teams.data) return <ErrorState message={teams.error} onRetry={teams.reload} />;
  return (
    <Screen>
      <Back label={teamId ? 'Teams' : 'League tools'} />
      {teamId ? <TeamPlayers teamId={teamId} onBack={() => setTeamId(null)} /> : (
        <>
          <Title>Pick a team</Title>
          <Card>
            {(teams.data?.teams ?? []).map((t, i) => (
              <Row key={t.team_id} first={i === 0} onPress={() => setTeamId(t.team_id)}>
                <Avatar name={t.team_name || t.owner_name} size={30} />
                <Text style={[ui.value, ui.flex]} numberOfLines={1}>{t.team_name}</Text>
                <Text style={ui.link}>›</Text>
              </Row>
            ))}
          </Card>
        </>
      )}
    </Screen>
  );
}

function TeamPlayers({ teamId, onBack }: { teamId: string; onBack: () => void }) {
  const { data, error, loading, reload, refresh } = useApi<TeamPage>(`/teams/${teamId}`);
  const [player, setPlayer] = useState<Player | null>(null);
  if (loading && !data) return <ActivityIndicator color={colors.gold} />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data) return null;
  const r = data.roster;
  const everyone = [...r.starters, ...r.bench, ...r.taxi, ...r.ir].filter((p) => p.player_id);
  return (
    <>
      <Pressable onPress={onBack} hitSlop={8}><Text style={styles.back}>‹ All teams</Text></Pressable>
      <Title>{data.team_name}</Title>
      {player ? <PlayerTools player={player} onDone={() => { setPlayer(null); refresh(); }} /> : null}
      <Card>
        {everyone.map((p, i) => (
          <Row key={p.player_id} first={i === 0} onPress={() => setPlayer(p)}>
            <View style={ui.flex}>
              <Text style={ui.value} numberOfLines={1}>{p.name}</Text>
              <Text style={ui.sub}>{p.pos} · {p.nfl_team}</Text>
            </View>
            <Text style={ui.value}>{money(p.salary)}</Text>
          </Row>
        ))}
      </Card>
    </>
  );
}

function PlayerTools({ player, onDone }: { player: Player; onDone: () => void }) {
  const [contract, setContract] = useState<ContractFix | null>(null);
  const [salary, setSalary] = useState('');
  const [end, setEnd] = useState('');
  const [busy, setBusy] = useState(false);

  const drop = async () => {
    setBusy(true);
    try {
      const p = await apiGet<DropPreview>(`/commissioner/drop/${player.player_id}`);
      Alert.alert(`Drop ${player.name}?`,
        `${p.team_name} takes ${money(p.dead_cap)} in dead cap (${p.dead_cap_pct}% of ${money(p.salary)}, ${p.years_left} year${p.years_left === 1 ? '' : 's'} left).`,
        [{ text: 'Cancel', style: 'cancel' }, {
          text: 'Drop', style: 'destructive',
          onPress: () => apiSend('POST', '/commissioner/drop', { player_id: player.player_id })
            .then(clearApiCache).then(onDone).catch((e) => Alert.alert("Didn't drop", e.message)),
        }]);
    } catch (e: any) {
      Alert.alert("Can't drop", e.message);
    } finally {
      setBusy(false);
    }
  };

  const openContract = async () => {
    setBusy(true);
    try {
      const c = await apiGet<ContractFix>(`/commissioner/contract/${player.player_id}`);
      setContract(c);
      setSalary(String(c.seasons[0]?.salary ?? ''));
      setEnd(String(c.end_season));
    } catch (e: any) {
      Alert.alert('No contract', e.message);
    } finally {
      setBusy(false);
    }
  };

  const saveContract = async () => {
    if (!contract) return;
    setBusy(true);
    try {
      await apiSend('POST', '/commissioner/contract', {
        contract_id: contract.contract_id,
        salary: salary === '' ? null : Number(salary),
        end_season: end === '' ? null : Number(end),
      });
      await clearApiCache();
      onDone();
    } catch (e: any) {
      Alert.alert("Didn't save", e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={player.name} gold>
      {contract ? (
        <>
          <Text style={ui.sub}>Now: {contract.seasons.map((s) => `${s.season} ${money(s.salary)}`).join(' · ')}</Text>
          <View style={styles.fields}>
            <View style={ui.flex}>
              <Text style={styles.label}>Salary from this season</Text>
              <TextInput style={styles.input} value={salary} onChangeText={setSalary} keyboardType="decimal-pad" />
            </View>
            <View style={ui.flex}>
              <Text style={styles.label}>Last season</Text>
              <TextInput style={styles.input} value={end} onChangeText={(v) => setEnd(v.replace(/\D/g, '').slice(0, 4))} keyboardType="number-pad" />
            </View>
          </View>
          <Note>Past seasons never change. Cap hits on IR or taxi keep their reduced rate.</Note>
          <Pressable style={[styles.button, busy && styles.off]} disabled={busy} onPress={saveContract}>
            <Text style={styles.buttonText}>Save contract</Text>
          </Pressable>
        </>
      ) : (
        <View style={styles.actions}>
          <Pressable style={[styles.button, ui.flex, busy && styles.off]} disabled={busy} onPress={openContract}>
            <Text style={styles.buttonText}>Fix contract</Text>
          </Pressable>
          <Pressable style={[styles.button, styles.dangerButton, ui.flex, busy && styles.off]} disabled={busy} onPress={drop}>
            <Text style={styles.buttonText}>Drop</Text>
          </Pressable>
        </View>
      )}
    </Card>
  );
}

const styles = StyleSheet.create({
  back: { color: colors.muted, fontSize: 14, marginBottom: 6 },
  fields: { flexDirection: 'row', gap: 10, marginTop: 10 },
  label: { color: colors.muted, fontSize: 12, fontWeight: '700', marginBottom: 6 },
  input: { backgroundColor: colors.card, color: colors.text, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 9, fontSize: 15 },
  actions: { flexDirection: 'row', gap: 10, marginTop: 6 },
  button: { backgroundColor: colors.gold, borderRadius: 10, paddingVertical: 12, alignItems: 'center', marginTop: 8 },
  dangerButton: { backgroundColor: colors.danger },
  buttonText: { color: colors.bg, fontWeight: '800' },
  off: { opacity: 0.4 },
});
