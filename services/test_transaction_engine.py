from transaction_engine import TransactionEngine, Contract, TeamCapState

engine = TransactionEngine()

team = TeamCapState(
    team_id="tommy",
    faab_remaining=100,
    active_cap=120,
    dead_cap=0,
    roster_count=21,
)

dk = Contract(
    player_id="dk",
    player_name="DK Metcalf",
    team_id=None,
    salary=18,
    years_left=2,
)

gibbs = Contract(
    player_id="gibbs",
    player_name="Jahmyr Gibbs",
    team_id="tommy",
    salary=30,
    years_left=3,
)

result = engine.process_add_drop(
    added_contract=dk,
    dropped_contract=gibbs,
    team=team,
    waiver_bid=2,
)

print(result)
