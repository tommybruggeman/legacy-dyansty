import { useCallback, useEffect, useState } from 'react';

import { supabase } from './supabase';

const API_URL = (process.env.EXPO_PUBLIC_API_URL ?? '').replace(/\/$/, '');

// Calls the Legacy API as the signed-in owner. The server checks the token
// and reads as that owner, so the database rules still apply.
export async function apiGet<T>(path: string): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new Error('Sign in required.');

  const response = await fetch(`${API_URL}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.detail ?? `Request failed (${response.status}).`);
  return body as T;
}

export function useApi<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await apiGet<T>(path));
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? 'Something went wrong.');
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => { load(); }, [load]);

  return { data, error, loading, reload: load };
}

export type StandingsRow = {
  rank: number;
  prev_rank: number | null;
  roster_id: number;
  name: string;
  is_me: boolean;
  standing_points: number;
  wins: number;
  losses: number;
  ties: number;
  top5: number;
  pf: number;
  pa: number;
  pf_per_game: number;
  pa_per_game: number;
  games: number;
};

export type Standings = { weeks_played: number; playoff_teams: number; rows: StandingsRow[] };

export type MatchupTeam = { roster_id: number; name: string; score: number; rank: number; top5: boolean };
export type Game = { matchup_id: number; teams: [MatchupTeam, MatchupTeam] };
export type Matchups = {
  week: number;
  current_week: number;
  is_final: boolean;
  my_roster_id: number | null;
  games: Game[];
  summary: { high: MatchupTeam; cut: MatchupTeam; closest: Game; closest_margin: number } | null;
};
