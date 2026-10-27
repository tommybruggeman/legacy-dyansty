import { useEffect, useState } from 'react';

import { supabase } from './supabase';

export type MyTeam = {
  leagueId: string;
  teamId: string | null;
  teamName: string | null;
  ownerName: string | null;
  role: string | null;
};

// Mirrors resolve_my_team in services/app_context.py: membership first, then
// the team row by id, falling back to the owner name.
export async function loadMyTeam(userId: string): Promise<MyTeam | null> {
  const { data: memberships, error } = await supabase
    .from('league_memberships')
    .select('*')
    .eq('user_id', userId)
    .limit(1);
  if (error) throw error;
  const membership = memberships?.[0];
  if (!membership) return null;

  const leagueId: string = membership.league_id;
  const teamId = membership.league_team_id ?? membership.team_id;
  const ownerName = membership.owner_name ?? membership.sleeper_username;

  let query = supabase.from('league_teams').select('*').eq('league_id', leagueId).limit(1);
  if (teamId) query = query.eq('id', teamId);
  else if (ownerName) query = query.eq('owner_name', ownerName);
  else return { leagueId, teamId: null, teamName: membership.team_name ?? null, ownerName: null, role: membership.role ?? null };

  const { data: teams, error: teamError } = await query;
  if (teamError) throw teamError;
  const team = teams?.[0];

  return {
    leagueId,
    teamId: team?.id ?? null,
    teamName: team?.team_name ?? membership.team_name ?? team?.owner_name ?? null,
    ownerName: team?.owner_name ?? ownerName ?? null,
    role: membership.role ?? null,
  };
}

export function useMyTeam(userId: string | undefined) {
  const [team, setTeam] = useState<MyTeam | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!userId) return;
    setLoading(true);
    loadMyTeam(userId)
      .then((t) => { setTeam(t); setError(null); })
      .catch((e) => setError(e?.message ?? 'Could not load your team.'))
      .finally(() => setLoading(false));
  }, [userId]);

  return { team, error, loading };
}
