import csv, io, requests
from api import props
rows=list(csv.DictReader(io.StringIO(requests.get(props.CROSSWALK,timeout=30).text)))
for name,pos in [("Pat Freiermuth","TE"),("Josh Allen","QB"),("Derrick Henry","RB"),("Ja'Marr Chase","WR"),("Jayden Daniels","QB"),("Puka Nacua","WR"),("Christian McCaffrey","RB")]:
    r=[x for x in rows if x["name"]==name and x["position"]==pos]
    if not r: print(name,"not in crosswalk"); continue
    r=r[0]; print("RESULT",name, r["sleeper_id"], r["espn_id"], r["team"], props.yards_line(r["sleeper_id"].split(".")[0], pos, {"SFO":"SF"}.get(r["team"],r["team"]), 2026, 8))
d=props.fetch(props.PROP_BETS.format(event="401873023",provider="100"),{"limit":1000,"page":2})
print("PAGE2", d.get("pageIndex"), d.get("pageCount"), len(d.get("items",[])))
print("EVENTS", [e["name"] for v in [x for k,(t,x) in props.cache._cache.items() if k[0]=="espn_board"] for e in v["events"]])
