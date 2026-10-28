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

// Sends a change (POST or DELETE) as the signed-in owner.
export async function apiSend<T>(method: 'POST' | 'DELETE', path: string, body?: unknown): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new Error('Sign in required.');

  const response = await fetch(`${API_URL}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok) throw new Error(result?.detail ?? `Request failed (${response.status}).`);
  return result as T;
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
  team_id: string | null;
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

export type CapYear = { season: number; used: number; space: number; dead_cap: number };
export type Lean = 'contending' | 'middle' | 'rebuilding' | null;

export type TeamSummary = {
  team_id: string;
  owner_name: string;
  team_name: string;
  roster_id: number | null;
  rank: number | null;
  wins: number;
  losses: number;
  ties: number;
  standing_points: number;
  is_me: boolean;
  lean: Lean;
  cap_space: number;
  cap_by_year: CapYear[];
};
export type TeamList = { season: number; salary_cap: number; my_team_id: string | null; teams: TeamSummary[] };

export type Player = {
  player_id: string;
  name: string;
  pos: string;
  nfl_team: string;
  injury: string | null;
  salary: number;
  contract_salary: number;
  years_left: number;
  designation: 'taxi' | 'ir' | null;
  is_rookie: boolean;
  ppg: number | null;
  slot?: string;
  through?: number;
};
export type Pick = { year: number; round: number; original_owner: string; current_owner: string; own: boolean };

export type TeamPage = {
  season: number;
  salary_cap: number;
  sleeper_league_id: string;
  team_id: string;
  owner_name: string;
  team_name: string;
  roster_id: number | null;
  is_me: boolean;
  lean: Lean;
  record: { wins: number; losses: number; ties: number; rank: number | null; standing_points: number; pf_per_game: number | null };
  cap: { space: number; by_year: CapYear[] };
  roster: { starters: Player[]; bench: Player[]; taxi: Player[]; ir: Player[]; count: number };
  contracts: { biggest: Player[]; expiring: Player[]; dead_cap: { player: string; amount: number }[] };
  picks: { owned: Pick[]; traded_away: Pick[] };
  activity: { kind: 'SIGN' | 'CUT'; player: string; at: string | null }[];
  head_to_head: { wins: number; losses: number; ties: number; last: { week: number; me: number; them: number } } | null;
};

export type BoxPlayer = { player_id: string; name: string; slot: string; points: number };
export type BoxScore = {
  week: number;
  matchup_id: number;
  my_roster_id: number | null;
  teams: { roster_id: number; team_id: string | null; name: string; score: number; players: BoxPlayer[] }[];
};

export type PlayerPage = {
  season: number;
  player_id: string;
  name: string;
  pos: string;
  nfl_team: string;
  age: number | null;
  injury: string | null;
  injury_detail: string | null;
  ppg: number | null;
  weeks: { week: number; points: number | null; started: boolean }[];
  contract: {
    team_id: string;
    owner_name: string;
    salary: number;
    cap_hit: number;
    years_left: number;
    through: number;
    designation: 'taxi' | 'ir' | null;
    is_rookie: boolean;
  } | null;
  is_mine: boolean;
};

export type FreeAgent = {
  player_id: string;
  name: string;
  pos: string;
  nfl_team: string;
  injury: string | null;
  rookie: boolean;
  ppg: number | null;
  last_ppg: number | null;
  rank_ppg: number | null;
};
export type FreeAgents = { season: number; last_season: number; season_started: boolean; players: FreeAgent[] };
export type Expiring = {
  season: number;
  players: { player_id: string; name: string; pos: string; owner_name: string; team_id: string; salary: number; is_mine: boolean }[];
};
