import { fetch as streamingFetch } from 'expo/fetch';
import { useFocusEffect } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { AppState } from 'react-native';

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

// Posts as the signed-in owner and reads a newline-delimited JSON stream,
// calling onEvent for each line as it arrives.
export async function apiStream<E>(path: string, body: unknown, onEvent: (event: E) => void): Promise<void> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new Error('Sign in required.');

  const response = await streamingFetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', Accept: 'application/x-ndjson' },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.detail ?? `Request failed (${response.status}).`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (value) buffer += decoder.decode(value, { stream: true });
    let newline;
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onEvent(JSON.parse(line) as E);
    }
    if (done) break;
  }
}

// Pages open instantly with what was last loaded (kept in memory and on the
// phone), then refresh quietly in the background. Cleared on sign out.
// Bump CACHE_VERSION when a response changes shape, so an app update never
// draws an old saved copy with the new screen.
const CACHE_VERSION = 'v7';
const CACHE_PREFIX = 'api:';
const memory = new Map<string, unknown>();

function remember(path: string, value: unknown) {
  memory.set(path, value);
  AsyncStorage.setItem(CACHE_PREFIX + CACHE_VERSION + path, JSON.stringify(value)).catch(() => undefined);
}

async function recall<T>(path: string): Promise<T | null> {
  if (memory.has(path)) return memory.get(path) as T;
  try {
    const saved = await AsyncStorage.getItem(CACHE_PREFIX + CACHE_VERSION + path);
    if (saved) {
      const value = JSON.parse(saved) as T;
      memory.set(path, value);
      return value;
    }
  } catch {
    // A bad saved copy just means a normal load.
  }
  return null;
}

export async function clearApiCache() {
  memory.clear();
  const keys = await AsyncStorage.getAllKeys().catch(() => [] as readonly string[]);
  await AsyncStorage.multiRemove(keys.filter((k) => k.startsWith(CACHE_PREFIX))).catch(() => undefined);
}

// Whoever signs in next on this phone never sees the last owner's pages.
supabase.auth.onAuthStateChange((event) => {
  if (event === 'SIGNED_OUT') clearApiCache();
});

// Loads pages in the background so the first tap on them is instant.
export function prefetch(paths: string[]) {
  for (const path of paths) {
    apiGet(path).then((value) => remember(path, value)).catch(() => undefined);
  }
}

// refreshEvery (ms) quietly re-fetches while the screen is open and the app
// is in front, e.g. live scores. Pass 0 to stop.
export function useApi<T>(path: string, refreshEvery = 0) {
  const [data, setData] = useState<T | null>(() => (memory.get(path) as T | undefined) ?? null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!memory.has(path));

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const value = await apiGet<T>(path);
      remember(path, value);
      setData(value);
      setError(null);
    } catch (e: any) {
      if (!quiet) setError(e?.message ?? 'Something went wrong.');
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    let live = true;
    recall<T>(path).then((saved) => {
      if (!live) return;
      if (saved) {
        setData(saved);
        setLoading(false);
      } else {
        setData(null);
      }
      load(!!saved);
    });
    return () => { live = false; };
  }, [path, load]);

  useFocusEffect(
    useCallback(() => {
      if (!refreshEvery) return undefined;
      const timer = setInterval(() => {
        if (AppState.currentState === 'active') load(true);
      }, refreshEvery);
      return () => clearInterval(timer);
    }, [load, refreshEvery]),
  );

  // Stable across renders, so screens can list it in effect dependencies.
  // reload shows the spinner (pull to refresh); refresh is silent.
  const reload = useCallback(() => load(), [load]);
  const refresh = useCallback(() => load(true), [load]);

  return { data, error, loading, reload, refresh };
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

export type MatchupTeam = {
  roster_id: number; name: string; score: number; rank: number; top5: boolean; projected?: number | null;
  avatar?: string | null; record?: string; playing?: number; to_play?: number;
};
export type Game = { matchup_id: number; teams: [MatchupTeam, MatchupTeam]; win_chance?: [number, number] | null; next_kickoff?: string | null };
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
  no_contract?: boolean;
  designation: 'taxi' | 'ir' | null;
  is_rookie: boolean;
  ppg: number | null;
  slot?: string;
  through?: number;
};
// A contract on the value board: PPG per cap dollar, graded against the whole league.
export type ValuedContract = Player & { through: number; per_dollar: number | null; grade: 'bargain' | 'fair' | 'overpaid' | 'stash' | null };
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
  contracts: { biggest: Player[]; expiring: Player[]; all?: ValuedContract[]; dead_cap: { player: string; amount: number; years_left?: number; through?: number; more?: boolean }[] };
  picks: { owned: Pick[]; traded_away: Pick[] };
  activity: { kind: 'SIGN' | 'CUT'; player: string; at: string | null }[];
  moves?: { moves: number; trades: number };
  faab?: { left: number; budget: number } | null;
  power?: { rank: number; of: number | null; move: number | null } | null;
  head_to_head: { wins: number; losses: number; ties: number; last: { week: number; me: number; them: number } } | null;
};

export type BoxPlayer = {
  player_id: string;
  name: string;
  slot: string;
  points: number;
  pos?: string;
  team?: string | null;
  status?: 'bye' | 'waiting' | 'playing' | 'final';
  proj?: number | null;
  live_proj?: number | null;
  rank?: string | null;
  breakdown?: { label: string; value: number; points: number }[];
  game?: NflGame | null;
  line?: { text: string; tone: '' | 'good' | 'bad' }[];
  kickoff?: string | null;
};
// A player's NFL game from his team's side (ESPN scoreboard): pre, in or post.
export type NflGame = {
  state: 'pre' | 'in' | 'post';
  quarter: number;
  clock: string;
  detail: string;
  opp: string;
  home: boolean;
  score: number;
  opp_score: number;
  possession: boolean;
  red_zone: boolean;
  down: string;
};
export type BoxScore = {
  week: number;
  is_final: boolean;
  matchup_id: number;
  my_roster_id: number | null;
  teams: { roster_id: number; team_id: string | null; name: string; score: number; projected?: number; players: BoxPlayer[]; bench?: BoxPlayer[] }[];
  win_chance?: [number, number] | null;
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
  transactions_enabled?: boolean;
  sleeper_league_id?: string;
  rostered_by?: { roster_id: number; owner_name: string; team_id: string | null } | null;
  bye_week: number | null;
  nfl_season: {
    games: number;
    tiles: { value: string; label: string }[];
    snap_pct: number | null;
    snap_trend: 'up' | 'down' | null;
  } | null;
  league_season: {
    ppg: number | null;
    ppg_started: number | null;
    rank: { rank: number; of: number; pos: string; total: number } | null;
    started: number;
    played: number;
    per_dollar: number | null;
    best?: { week: number; points: number; opp: string } | null;
  };
  game_log: GameLogRow[];
  next_game: {
    week: number;
    bye: boolean;
    opp?: string;
    home?: boolean;
    kickoff?: string | null;
    team_total?: number | null;
    yards_line?: { label: string; line: number } | null;
    projected?: number | null;
    weather?: string | null;
    vs_pos?: { label: 'tough' | 'soft' | 'neutral' | null; text: string; allowed: number | null } | null;
  } | null;
  health: {
    status: string;
    healthy: boolean;
    detail: string | null;
    update: string | null;
    return: string | null;
    this_week: { week: number; practice: string | null; game_status: string | null; injury: string | null } | null;
    missed: { injury: string; season: number; games: number; back: boolean }[];
    estimate?: { injury: string; median_games: number; range: string; absences: number; scope: string; text: string } | null;
  };
  history: {
    kind: string;
    title: string;
    owner: string;
    team_id: string;
    date: string;
    salary: number | null;
    years: number | null;
    tag: string | null;
    live: boolean;
  }[];
};

export type GameLogRow = {
  week: number;
  opp: string;
  status: 'started' | 'bench' | 'out' | 'bye' | 'free_agent';
  points: number | null;
  rank?: string | null;
  line: {
    carries: number; rush_yds: number; rush_td: number;
    targets: number; rec: number; rec_yds: number; rec_td: number;
    snap_pct: number | null;
    comp?: number; att?: number; pass_yds?: number; pass_td?: number; ints?: number;
  } | null;
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
  points: number | null;
};
export type TradePlayer = {
  player_id: string; name: string; pos: string; nfl_team: string; salary: number | null; years: number | null; points_since?: number;
};
export type TradeSide = {
  owner: string; team_id: string | null; players: TradePlayer[]; picks: { label: string; original_owner: string | null }[];
  faab: number; cap_change: number; points_since?: number; market_now?: number; market_share?: number | null;
};
export type Trade = {
  kind: 'trade'; id: string; season: number; week: number; date: string; at: string; owners: string[]; sides: TradeSide[];
  weeks_since?: number; winning: { owner: string; by: number } | null;
};
export type WaiverBid = { owner: string; amount: number; won: boolean };
export type MovePlayer = {
  player_id: string; name: string; pos: string; nfl_team: string; salary: number | null; years: number | null; faab?: number | null;
  bids?: WaiverBid[]; dead_cap?: number; dead_through?: number;
};
export type Move = {
  kind: 'move'; type: 'waiver' | 'free_agent' | 'commissioner' | 'drop'; id: string; season: number; week: number; date: string; at: string;
  owner: string; owners: string[]; team_id: string | null; adds: MovePlayer[]; drops: MovePlayer[];
};
export type Draft = {
  kind: 'draft'; id: string; season: number; date: string; at: string; owners: string[];
  picks: (MovePlayer & { owner: string; team_id: string | null; round: number | null; slot: number | null; amount: number | null })[];
};
export type ActivityItem = Trade | Move | Draft;
export type ActivityFeed = { items: ActivityItem[]; seasons: number[]; total: number; me: string | null };
export type FreeAgents = { season: number; last_season: number; season_started: boolean; players: FreeAgent[] };
// Every rostered player plus the best free agents (/market/players), for the Players tab and pull-down search.
export type LeaguePlayer = {
  player_id: string; name: string; pos: string; nfl_team: string; age: number | null; injury: string | null; rookie: boolean;
  rank_ppg: number | null; last_ppg: number | null; points: number | null;
  team_id: string | null; owner_name: string | null; is_mine: boolean;
  salary: number | null; years_left: number | null; rookie_deal: boolean; designation: 'taxi' | 'ir' | null;
};
export type LeagueOwner = { team_id: string; owner_name: string; team_name: string; is_mine: boolean };
export type LeaguePlayers = {
  season: number; last_season: number; season_started: boolean; players: LeaguePlayer[]; owners: LeagueOwner[];
  plan_question?: string; // the ★ Saved chip's GM question (from build 38)
};
export type ExpiringPlayer = {
  player_id: string; name: string; pos: string; nfl_team: string; owner_name: string; team_id: string; salary: number;
  is_mine: boolean; rank_ppg: number | null; points: number | null;
};
export type Target = {
  player_id: string; name: string; pos: string; nfl_team: string; owner_name: string | null; team_id: string | null;
  salary: number | null; years_left: number | null; is_mine: boolean; rank_ppg: number | null; points: number | null;
};
export type Expiring = {
  season: number;
  years: number[];
  players: ExpiringPlayer[];
  sleeper_league_id: string;
  offseason: boolean;
  // Only between the final game and the June 30 rollover.
  calls: { window_open: boolean; deadline: string; players: (RookieDeal & { nfl_team: string })[] } | null;
  targets: Target[];
  targets_ready: boolean;
  plan_question: string;
};

export type FoTile = { value: string; label: string; tone: 'good' | 'warn' | null };
export type ReportCard = {
  record: Record<string, string>;
  open_calls: number;
  recent: { when: string; call: string; right: boolean; points: string; why: string | null }[];
  lessons: { lesson: string; seen: number }[];
  accuracy?: Accuracy | null;
};
// How the start/sit engine did on every lineup in the league (weekly, free), and this owner's own line.
export type AccuracyPair = { engine: number; owners: number; calls: number };
export type Accuracy = {
  season: number;
  overall: AccuracyPair;
  all_seasons: AccuracyPair | null;
  by_position: (AccuracyPair & { pos: string })[];
  by_week: (AccuracyPair & { week: number })[];
  you?: { engine: number; you: number; calls: number; points_per_call: number };
};
export type FoMove = { action: 'start' | 'sit'; player: string; slot: string; note: string };
export type FoHeader = {
  weakest: { slot: string; note: string } | null;
  odds: { pct: number; change: number | null; weeks_left: number; spots: number } | null;
  power: { rank: number; move: number | null } | null;
};
export type FrontOffice = {
  greeting: string;
  ai_ready: boolean;
  week: number;
  season: number;
  tiles: FoTile[];
  header?: FoHeader;
  moves?: FoMove[];
  win?: { pct: number; opponent: string } | null;
  brief: {
    injuries: { items: { player: string; status: string }[]; call?: string };
    matchup: { headline: string; lines: string[]; stakes?: string };
  };
  prompts: string[];
  report_card: ReportCard;
  sleeper_league_id: string | null;
};
export type ChatMessage = { role: 'user' | 'assistant'; content: string };
export type ChatReply = { ok: boolean; text: string; conversation_id: string | null };
export type ChatEvent =
  | { e: 'status'; text: string }
  | { e: 'text'; d: string }
  | { e: 'reset' }
  | { e: 'round' }
  | ({ e: 'done' } & ChatReply);
export type Conversations = { conversations: { id: string; title: string; updated_at: string | null }[] };
export type Conversation = { id: string; title: string; messages: ChatMessage[] };

export type HomeSide = {
  roster_id: number; name: string; score: number; rank?: number; top5?: boolean;
  avatar?: string | null; team_id?: string | null; season_rank?: number | null; wins?: number; losses?: number; ties?: number; ppg?: number | null;
};
export type LivePlayer = {
  player_id: string | null; name: string; pos: string; team: string; points: number;
  status: 'waiting' | 'playing' | 'final' | 'bye' | 'empty'; kickoff: string | null; injury: string | null; issue?: string;
};
export type TableRow = {
  rank: number; roster_id: number; name: string; wins: number; losses: number; ties: number; standing_points: number;
  is_me: boolean; in: boolean; team_id: string | null; avatar: string | null;
};
export type RaceRow = { rank: number; roster_id: number; name: string; score: number; top5: boolean; is_me: boolean; avatar: string | null };
export type HomeGame = { matchup_id?: number; me?: HomeSide; them?: HomeSide | null };
export type BracketSide = { roster_id: number; name: string; score: number | null; avatar?: string | null; seed?: number | null } | null;
export type RookieDeal = {
  decision_id: string | null; player_id: string; name: string; pos: string; salary: number; ppg: number | null;
  choice: 'keep' | 'walk' | 'review' | null; open: boolean; status: string;
};
export type Riser = {
  player_id: string; name: string; pos: string; team: string; avg: number; proj: number; rise: number; last: number | null;
  roster_id: number | null; owner: string | null; is_mine: boolean; injury: string | null;
};
export type BoardSide = { roster_id: number; name: string; score: number; top5: boolean; left: number; is_me: boolean; avatar: string | null };
export type ShotgunRow = {
  roster_id: number; name: string; avatar: string | null; is_me: boolean; score: number; projected: number;
  left: number; playing: number; frozen: boolean; gap: number;
};
export type HomeBottom =
  | { mode: 'risers'; risers: Riser[] }
  | { mode: 'scoreboard'; scoreboard: { matchup_id: number; teams: BoardSide[]; mine: boolean }[]; race: RaceRow[] }
  | { mode: 'shotgun'; shotgun: {
      window: number; locked: boolean; last: ShotgunRow; danger: ShotgunRow[]; safe: ShotgunRow[];
      needs: { points: number; to_pass: string } | null;
    } | null };
export type HomeData = {
  phase: 'game_day' | 'recap' | 'playoffs' | 'decisions' | 'draft_prep';
  season: number;
  week: number;
  today: string;
  owner_name: string;
  salary_cap: number;
  me: { rank: number | null; wins: number; losses: number; ties: number; standing_points: number; cap_space: number; cap_by_year: CapYear[]; team_id: string };
  teams_count: number;
  playoff_teams: number;
  front_office: { line: string; prompts: string[] };
  game?: HomeGame & { week: number; is_final: boolean };
  recap?: { week: number; game: HomeGame | null; summary: { high: HomeSide; cut: HomeSide } | null; top_scorers: LivePlayer[]; standing_points: number };
  weekday?: number;
  standings?: TableRow[];
  players?: { me: LivePlayer[]; them: LivePlayer[] };
  lineup_issues?: LivePlayer[];
  race?: RaceRow[];
  bottom?: HomeBottom;
  series?: { wins: number; losses: number; ties: number } | null;
  weeks_left?: number;
  bracket?: { round: number; week: number; label: string; games: { a: BracketSide; b: BracketSide; winner: number | null }[] }[];
  decisions?: { window_open: boolean; deadline: string; players: RookieDeal[] };
  picks?: { year: number | null; picks: Pick[] };
  who_has_money?: { name: string; space: number; is_me: boolean; team_id: string | null; open_spots: number | null }[];
  roster_count?: number;
  roster_max?: number;
  coming_off?: { total: number; players: { name: string; player_id: string; pos: string; salary: number }[] };
};

// ---- trades between owners (off until the league leaves Sleeper) ----
export type TradeAsset = { player_id: string; contract_id: string; name: string; pos: string; nfl_team: string; salary: number; years: number; ppg: number | null; value: number };
export type TradePick = { pick_id: string; label: string; year: number; round: number; value: number };
export type TradeSideData = { team_id: string; owner_name: string; cap_space: number; players: TradeAsset[]; picks: TradePick[] };
export type TradeBuilder = { enabled: boolean; sleeper_league_id: string; salary_cap: number; me: TradeSideData; them: TradeSideData; add: string | null };
export type OfferPiece = { kind: 'player' | 'pick'; player_id: string | null; contract_id: string | null; pick_id: string | null; name: string; pos: string; nfl_team: string };
export type Offer = {
  id: string; partner: string; partner_team_id: string; you_get: OfferPiece[]; you_give: OfferPiece[]; note: string | null;
  created_at: string; status: string; decided_at: string | null; is_counter: boolean; line?: string; sent_by_you?: boolean;
};
export type Transactions = { enabled: boolean; needs_answer: Offer[]; sent: Offer[]; bids: unknown[]; results: Offer[]; pending: number };
