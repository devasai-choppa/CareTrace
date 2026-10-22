"""Cypher helpers. Every price row includes the negotiated-edge lineage."""

from __future__ import annotations

from typing import Any

from caretrace.config import Settings
from caretrace.graph.build_graph import connect_graph

CHEAPEST_CYPHER = """
MATCH (prov:Provider)-[:AFFILIATED]->(h:Hospital)<-[n:NEGOTIATED]-(plan:Plan),
      (prov)-[inn:IN_NETWORK]->(plan)
WHERE n.code = $code
  AND h.city = $city
  AND plan.payer_id = $payer
  AND inn.trust_score >= 0.5
  AND n.rate IS NOT NULL
RETURN h.id AS source_id, h.name AS hospital, plan.id AS plan_id, n.rate AS rate,
       n.source_row_hash AS source_row_hash, n.source_file_uri AS source_file_uri,
       n.ingested_at AS ingested_at, prov.npi AS npi, prov.name AS provider,
       inn.trust_score AS trust_score
ORDER BY n.rate
"""


def cheapest_in_network(
    code: str,
    payer_id: str,
    city: str,
    *,
    settings: Settings | None = None,
    connection: Any | None = None,
) -> dict[str, Any]:
    """Return the cheapest verified in-network option and the Cypher that produced it."""
    graph = connection or connect_graph(settings)
    frame = graph.execute(
        CHEAPEST_CYPHER,
        parameters={"code": code, "city": city, "payer": payer_id},
    ).get_as_df()
    rows = frame.to_dict(orient="records")
    evidence = []
    for row in rows:
        evidence.append(
            {
                "path": [
                    f"Plan {row['plan_id']}",
                    f"NEGOTIATED {row['rate']} hash {row['source_row_hash']}",
                    f"Hospital {row['hospital']}",
                    f"Provider {row['provider']} trust {row['trust_score']}",
                ],
                "source_id": row["source_id"],
                "source_file_uri": row["source_file_uri"],
                "source_row_hash": row["source_row_hash"],
                "ingested_at": str(row["ingested_at"]),
            }
        )
    return {"cypher": CHEAPEST_CYPHER, "rows": rows, "evidence": evidence}


def providers_for(
    code: str, payer_id: str, *, settings: Settings | None = None
) -> list[dict[str, Any]]:
    """Providers in network for a plan that offers the code at an affiliated hospital."""
    graph = connect_graph(settings)
    cypher = """
    MATCH (prov:Provider)-[:AFFILIATED]->(h:Hospital)-[:OFFERS]->(s:Service {code: $code}),
          (prov)-[inn:IN_NETWORK]->(plan:Plan)
    WHERE plan.payer_id = $payer
    RETURN prov.npi AS npi, prov.name AS provider, h.name AS hospital, inn.trust_score AS trust_score
    """
    frame = graph.execute(cypher, parameters={"code": code, "payer": payer_id}).get_as_df()
    return frame.to_dict(orient="records")


def policy_path(
    code: str, payer_id: str, *, settings: Settings | None = None
) -> list[dict[str, Any]]:
    """Policies that govern a service for a payer, when those edges have been loaded."""
    graph = connect_graph(settings)
    cypher = """
    MATCH (policy:Policy)-[g:GOVERNS]->(s:Service {code: $code})
    WHERE policy.payer_id = $payer
    RETURN policy.id AS policy_id, policy.title AS title, g.requires_pa AS requires_pa
    """
    try:
        frame = graph.execute(cypher, parameters={"code": code, "payer": payer_id}).get_as_df()
    except RuntimeError:
        return []
    return frame.to_dict(orient="records")


def price_path_explain(quote_hash: str, *, settings: Settings | None = None) -> dict[str, Any]:
    """Return the negotiated edge whose hash matches a quote."""
    graph = connect_graph(settings)
    cypher = """
    MATCH (plan:Plan)-[n:NEGOTIATED]->(h:Hospital)
    WHERE n.source_row_hash = $hash
    RETURN plan.id AS plan_id, h.name AS hospital, n.code AS code, n.rate AS rate,
           n.source_file_uri AS source_file_uri
    """
    frame = graph.execute(cypher, parameters={"hash": quote_hash}).get_as_df()
    return {"cypher": cypher, "rows": frame.to_dict(orient="records")}
