from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from gm_assistant.identity.resolver import IdentityCandidate, normalize_name, resolve_cluster


@dataclass
class IdentityGraphResult:
    clusters: list
    node_count: int
    edge_count: int
    component_count: int
    warnings: list[str] = field(default_factory=list)


def _candidate_nodes(c: IdentityCandidate) -> list[str]:
    nodes = []

    if c.sleeper_id:
        # Treat gsis-looking values in sleeper fields as GSIS, not real Sleeper IDs.
        if str(c.sleeper_id).startswith("00-"):
            nodes.append(f"gsis:{c.sleeper_id}")
        else:
            nodes.append(f"sleeper:{c.sleeper_id}")

    if c.gsis_id:
        nodes.append(f"gsis:{c.gsis_id}")

    if c.player_name:
        nodes.append(f"name:{normalize_name(c.player_name)}")

    return sorted(set(nodes))


def build_identity_graph(candidates: list[IdentityCandidate]) -> IdentityGraphResult:
    graph: dict[str, set[str]] = defaultdict(set)
    node_candidates: dict[str, list[IdentityCandidate]] = defaultdict(list)
    edges = 0

    for c in candidates:
        nodes = _candidate_nodes(c)

        for n in nodes:
            node_candidates[n].append(c)
            graph.setdefault(n, set())

        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                graph[a].add(b)
                graph[b].add(a)
                edges += 1

    seen = set()
    clusters = []

    for node in graph:
        if node in seen:
            continue

        q = deque([node])
        seen.add(node)
        component_nodes = []

        while q:
            cur = q.popleft()
            component_nodes.append(cur)

            for nxt in graph[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)

        component_candidates = []
        candidate_seen = set()

        for n in component_nodes:
            for c in node_candidates.get(n, []):
                ident = (
                    c.table,
                    c.player_name,
                    c.pos,
                    c.sleeper_id,
                    c.gsis_id,
                    c.owner,
                    c.canonical_player_id,
                )
                if ident in candidate_seen:
                    continue
                candidate_seen.add(ident)
                component_candidates.append(c)

        if component_candidates:
            clusters.append(resolve_cluster(component_candidates))

    return IdentityGraphResult(
        clusters=clusters,
        node_count=len(graph),
        edge_count=edges,
        component_count=len(clusters),
    )
