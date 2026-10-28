import json, requests
H={"User-Agent":"Mozilla/5.0"}
def g(u):
    r=requests.get(u,headers=H,timeout=30); print("GET",u,r.status_code); return r.json() if r.ok else {}
sb=g("https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard")
ev=[e for e in sb.get("events",[]) if e["status"]["type"]["state"]=="pre"][:2]
for e in ev:
    eid=e["id"]; print("EVENT",eid,e["name"])
    base=f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{eid}/competitions/{eid}"
    odds=g(base+"/odds")
    for it in odds.get("items",[]):
        p=it.get("provider",{}); pid=p.get("id"); print("PROVIDER",pid,p.get("name"),list(it.keys())[:20])
        pb=g(base+f"/odds/{pid}/propBets?limit=1000")
        items=pb.get("items",[]); print("PROPS",pid,pb.get("count"),len(items))
        types={}
        for x in items: types.setdefault(json.dumps(x.get("type")),0); types[json.dumps(x.get("type"))]+=1
        for t,c in list(types.items())[:40]: print("  TYPE",c,t)
        for x in items[:3]: print("  SAMPLE",json.dumps(x)[:900])
    s=g(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={eid}")
    print("SUMMARY keys",list(s.keys()))
    for k in ("pickcenter","odds","againstTheSpread"):
        if k in s: print(k, json.dumps(s[k])[:1200])
