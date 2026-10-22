"""Build an idempotent Kuzu graph from gold rates and directory fixtures."""

from __future__ import annotations

import shutil
from typing import Any

import kuzu

from caretrace.config import Settings, get_settings
from caretrace.ingest.manifest import connect as duck_connect
from caretrace.ingest.registry import SourceRegistry, load_registry

SCHEMA = [
    "CREATE NODE TABLE Hospital(id STRING, name STRING, city STRING, PRIMARY KEY (id))",
    "CREATE NODE TABLE Provider(npi STRING, name STRING, PRIMARY KEY (npi))",
    "CREATE NODE TABLE Payer(id STRING, name STRING, PRIMARY KEY (id))",
    "CREATE NODE TABLE Plan(id STRING, payer_id STRING, name STRING, PRIMARY KEY (id))",
    "CREATE NODE TABLE Service(code STRING, label STRING, PRIMARY KEY (code))",
    "CREATE NODE TABLE Policy(id STRING, payer_id STRING, title STRING, PRIMARY KEY (id))",
    "CREATE NODE TABLE PolicySection(id STRING, policy_id STRING, page INT64, text STRING, PRIMARY KEY (id))",
    "CREATE NODE TABLE Patient(id STRING, PRIMARY KEY (id))",
    "CREATE NODE TABLE Claim(id STRING, PRIMARY KEY (id))",
    "CREATE REL TABLE OFFERS(FROM Hospital TO Service, setting STRING)",
    """CREATE REL TABLE NEGOTIATED(
        FROM Plan TO Hospital,
        code STRING, setting STRING, rate DOUBLE,
        source_id STRING, source_file_uri STRING, source_row_hash STRING, ingested_at STRING
    )""",
    "CREATE REL TABLE AFFILIATED(FROM Provider TO Hospital)",
    "CREATE REL TABLE IN_NETWORK(FROM Provider TO Plan, trust_score DOUBLE)",
    "CREATE REL TABLE GOVERNS(FROM Policy TO Service, requires_pa STRING)",
    "CREATE REL TABLE BILLED(FROM Claim TO Service, amount DOUBLE)",
    "CREATE REL TABLE CITES(FROM PolicySection TO Service)",
]

DEMO_PROVIDERS = [
    ("1234567893", "Priya Shah", "mgh", "bcbsma", "HMO Blue", 0.86),
    ("1987654321", "Luis Ortega", "bwh", "bcbsma", "HMO Blue", 0.81),
    ("1765432109", "Noah Kim", "bidmc", "bcbsma", "HMO Blue", 0.78),
    ("1543210987", "Amina Yusuf", "tufts", "bcbsma", "HMO Blue", 0.74),
    ("1321098765", "Elena Rossi", "bmc", "bcbsma", "HMO Blue", 0.9),
]


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def build_graph(
    settings: Settings | None = None,
    registry: SourceRegistry | None = None,
) -> dict[str, int]:
    """Rebuild the graph from gold.mart_negotiated_rates. Safe to run twice."""
    cfg = settings or get_settings()
    reg = registry or load_registry()
    if cfg.graph_path.exists():
        shutil.rmtree(cfg.graph_path)
    cfg.graph_path.parent.mkdir(parents=True, exist_ok=True)
    database = kuzu.Database(str(cfg.graph_path))
    graph = kuzu.Connection(database)
    for statement in SCHEMA:
        graph.execute(statement)
    hospitals = {item.source_id: item for item in reg.hospitals}
    for hospital in reg.hospitals:
        graph.execute(
            f"CREATE (:Hospital {{id: '{_q(hospital.source_id)}', name: '{_q(hospital.name)}', city: '{_q(hospital.city)}'}})"
        )
    for payer in reg.payers:
        graph.execute(
            f"CREATE (:Payer {{id: '{_q(payer.payer_id)}', name: '{_q(payer.canonical_name)}'}})"
        )
    conn = duck_connect(cfg)
    try:
        frame = conn.execute(
            """
            SELECT source_id, hospital_name, code, setting, payer_id, plan_name, rate_dollar,
                   source_row_hash, ingested_at, file_id
            FROM gold.mart_negotiated_rates
            WHERE rate_dollar IS NOT NULL
            """
        ).fetchdf()
    finally:
        conn.close()
    services: set[str] = set()
    plans: set[str] = set()
    offers: set[tuple[str, str, str]] = set()
    for record in frame.to_dict(orient="records"):
        code = str(record["code"])
        payer_id = str(record["payer_id"])
        plan_name = str(record.get("plan_name") or "unspecified")
        plan_id = f"{payer_id}|{plan_name}"
        source_id = str(record["source_id"])
        if source_id not in hospitals:
            continue
        if code not in services:
            graph.execute(f"CREATE (:Service {{code: '{_q(code)}', label: '{_q(code)}'}})")
            services.add(code)
        if plan_id not in plans:
            graph.execute(
                f"CREATE (:Plan {{id: '{_q(plan_id)}', payer_id: '{_q(payer_id)}', name: '{_q(plan_name)}'}})"
            )
            plans.add(plan_id)
        offer = (source_id, code, str(record["setting"]))
        if offer not in offers:
            graph.execute(
                f"""
                MATCH (h:Hospital {{id: '{_q(source_id)}'}}), (s:Service {{code: '{_q(code)}'}})
                CREATE (h)-[:OFFERS {{setting: '{_q(str(record["setting"]))}'}}]->(s)
                """
            )
            offers.add(offer)
        rate = float(record["rate_dollar"])
        row_hash = _q(str(record["source_row_hash"]))
        ingested = _q(str(record["ingested_at"]))
        uri = _q(f"mrf://{source_id}/{record['file_id']}")
        graph.execute(
            f"""
            MATCH (p:Plan {{id: '{_q(plan_id)}'}}), (h:Hospital {{id: '{_q(source_id)}'}})
            CREATE (p)-[:NEGOTIATED {{
                code: '{_q(code)}', setting: '{_q(str(record["setting"]))}', rate: {rate},
                source_id: '{_q(source_id)}', source_file_uri: '{uri}',
                source_row_hash: '{row_hash}', ingested_at: '{ingested}'
            }}]->(h)
            """
        )
    for npi, name, hospital, payer_id, plan_name, trust in DEMO_PROVIDERS:
        plan_id = f"{payer_id}|{plan_name}"
        if plan_id not in plans:
            graph.execute(
                f"CREATE (:Plan {{id: '{_q(plan_id)}', payer_id: '{_q(payer_id)}', name: '{_q(plan_name)}'}})"
            )
            plans.add(plan_id)
        graph.execute(f"CREATE (:Provider {{npi: '{npi}', name: '{_q(name)}'}})")
        graph.execute(
            f"""
            MATCH (p:Provider {{npi: '{npi}'}}), (h:Hospital {{id: '{hospital}'}})
            CREATE (p)-[:AFFILIATED]->(h)
            """
        )
        graph.execute(
            f"""
            MATCH (p:Provider {{npi: '{npi}'}}), (plan:Plan {{id: '{_q(plan_id)}'}})
            CREATE (p)-[:IN_NETWORK {{trust_score: {trust}}}]->(plan)
            """
        )
    graph.close()
    return {"services": len(services), "plans": len(plans), "rates": int(len(frame))}


def connect_graph(settings: Settings | None = None) -> Any:
    """Open the Kuzu database, building it when the directory is missing."""
    cfg = settings or get_settings()
    if not cfg.graph_path.exists():
        build_graph(cfg)
    database = kuzu.Database(str(cfg.graph_path))
    return kuzu.Connection(database)
