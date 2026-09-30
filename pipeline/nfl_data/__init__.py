"""Nightly NFL data pipeline feeding the nfl_* tables League AI reads.

Sources (all free):
- Sleeper players API           -> nfl_players (identity, team, age, depth chart, injury)
- DynastyProcess db_playerids   -> ID crosswalk (sleeper <-> gsis <-> fantasypros <-> pfr <-> cfbref)
- nflverse weekly player stats  -> nfl_player_stats (production, usage, splits)
- nflverse snap counts          -> offense snaps / snap share on nfl_player_stats
- DynastyProcess values         -> nfl_market_values, nfl_pick_values

Every loader is a pure transform over fetched data plus a thin writer, so the
transforms are unit-tested against saved samples without network access.
"""
