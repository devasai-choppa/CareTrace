"""CareTrace command line."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from caretrace.agent.graph import run_agent
from caretrace.audit.appeal import draft_letter, write_pdf
from caretrace.audit.engine import run_audit
from caretrace.audit.ingest_eob import ingest_path
from caretrace.audit.rules.builtin import AuditContext
from caretrace.directory.payer_directory import MockDirectory
from caretrace.directory.trust import explain, score_entry
from caretrace.ingest.discover import discover
from caretrace.ingest.download import HostRateLimiter, download
from caretrace.models.domain import Finding, RateQuote
from caretrace.parse.payer_resolve import append_confirmation, resolve_all, review_band
from caretrace.parse.runner import parse_file
from caretrace.pipeline import load_samples
from caretrace.policy.fetch import fetch_policies
from caretrace.warehouse import get_rates

app = typer.Typer(help="CareTrace: cited price estimates and bill audits.", no_args_is_help=True)
directory_app = typer.Typer(help="Provider directory trust.")
audit_app = typer.Typer(help="EOB ingest and audit.")
payers_app = typer.Typer(help="Payer resolution review.")
policy_app = typer.Typer(help="Prior-authorization questions.")
app.add_typer(directory_app, name="directory")
app.add_typer(audit_app, name="audit")
app.add_typer(payers_app, name="payers")
app.add_typer(policy_app, name="policy")
console = Console()


@app.command("discover")
def discover_command(source_id: str | None = None) -> None:
    """Fetch cms-hpt.txt for each hospital and store mrf_index."""
    rows = discover(source_id=source_id)
    console.print(f"discovered {len(rows)} machine-readable files")


@app.command("download")
def download_command(source_id: str | None = None, max_gb: float | None = None) -> None:
    """Stream MRF downloads. Skips a file when the sha256 is unchanged."""
    rows = download(source_id=source_id, max_gb=max_gb, limiter=HostRateLimiter(1.0))
    console.print(f"download attempts: {len(rows)}")


@app.command("parse")
def parse_command(file_id: str | None = None, samples: bool = False) -> None:
    """Parse downloaded MRFs, or the committed samples when --samples is set."""
    if samples or file_id is None:
        result = load_samples()
        console.print(result["marts"])
        return
    from caretrace.ingest.manifest import get_file

    row = get_file(file_id)
    if row is None:
        raise typer.BadParameter(f"unknown file_id {file_id}")
    stats = parse_file(Path(str(row["uri"])), source_id=str(row["source_id"]), file_id=file_id)
    console.print(stats)


@app.command("dbt")
def dbt_command() -> None:
    """Run dbt deps, build, and docs generate."""
    import os
    import subprocess

    env = os.environ.copy()
    env["DBT_PROFILES_DIR"] = str(Path("dbt").resolve())
    profiles = Path("dbt/profiles.yml")
    if not profiles.exists():
        profiles.write_text(
            Path("dbt/profiles.yml.example").read_text(encoding="utf-8"), encoding="utf-8"
        )
    subprocess.check_call(["uv", "run", "dbt", "deps"], cwd="dbt", env=env)
    subprocess.check_call(["uv", "run", "dbt", "build"], cwd="dbt", env=env)
    subprocess.check_call(["uv", "run", "dbt", "docs", "generate"], cwd="dbt", env=env)


@app.command("load-samples")
def load_samples_command() -> None:
    """Parse sample MRFs and build the local gold tables."""
    console.print(load_samples()["marts"])


@app.command("serve")
def serve_command(port: int = 8000) -> None:
    """Run the FastAPI app."""
    import uvicorn

    uvicorn.run("caretrace.api.main:app", host="0.0.0.0", port=port)


@app.command("chat")
def chat_command(message: str) -> None:
    """Answer one question with citations."""
    result = run_agent(message)
    console.print(result.answer, markup=False)
    for citation in result.citations:
        console.print(
            f"[{citation.claim_id}] {citation.source_file_uri} {citation.source_row_hash}",
            markup=False,
        )


@directory_app.command("score")
def directory_score(payer: str) -> None:
    """Score the demo directory for one payer."""
    for entry in MockDirectory().search(payer):
        scored = score_entry(entry)
        console.print(f"{scored.provider_name} {scored.score:.2f} ghost={scored.ghost_risk}")
        for reason in explain(scored):
            console.print(f"  - {reason}")


@payers_app.command("review")
def payers_review() -> None:
    """Print payer names in the 75-90 review band."""
    rows = review_band(resolve_all())
    if not rows:
        console.print("No payer names in the review band.")
        return
    for row in rows:
        console.print(
            f"{row['payer_name_raw']} -> {row.get('suggested_payer_id')} ({row['score']:.0f})"
        )


@payers_app.command("confirm")
def payers_confirm(payer_name_raw: str, payer_id: str) -> None:
    """Append a human confirmation to the seed."""
    append_confirmation(Path("dbt/seeds/payer_confirmations.csv"), payer_name_raw, payer_id)
    console.print("confirmation appended")


@app.command("policy-index")
def policy_index_command() -> None:
    """Download configured policies and index the local sample."""
    paths = fetch_policies()
    console.print(f"indexed source files: {len(paths)}")


@policy_app.command("ask")
def policy_ask(question: str, payer: str = "bcbsma") -> None:
    """Ask a prior-authorization question."""
    from caretrace.agent.tools import tool_prior_auth

    result = tool_prior_auth(payer, question)
    console.print(result["prior_auth"])


@audit_app.command("ingest")
def audit_ingest(path: Path) -> None:
    """Print extracted lines and their field provenance."""
    for line in ingest_path(path):
        console.print(
            f"{line.line_no} {line.code} allowed={line.allowed} provenance={line.provenance}"
        )


@audit_app.command("run")
def audit_run(path: Path, payer_id: str = "bcbsma") -> None:
    """Audit an EOB against published rates."""
    lines = ingest_path(path)
    rates: dict[str, RateQuote] = {}
    for line in lines:
        if not line.code or line.code in rates:
            continue
        quotes = get_rates(line.code, payer_id)
        if quotes:
            rates[line.code] = quotes[0].value
    report = run_audit(lines, AuditContext(rates_by_code=rates, directory_confidence=0.86))
    for finding in report["findings"]:
        console.print(f"{finding.rule_id} ${finding.impact:.2f} {finding.explanation}")
    console.print(f"potential savings ${report['potential_savings']}")


@audit_app.command("appeal")
def audit_appeal(path: Path, payer_id: str = "bcbsma") -> None:
    """Draft a letter PDF for an audited EOB."""
    lines = ingest_path(path)
    rates: dict[str, RateQuote] = {}
    for line in lines:
        if line.code:
            quotes = get_rates(line.code, payer_id)
            if quotes:
                rates[line.code] = quotes[0].value
    report = run_audit(lines, AuditContext(rates_by_code=rates))
    findings: list[Finding] = report["findings"]  # type: ignore[assignment]
    letter = draft_letter(findings, payer_id=payer_id)
    pdf = write_pdf(letter, Path("exports") / "appeal.pdf")
    console.print(letter)
    console.print(f"wrote {pdf}")


def main() -> None:
    """Entrypoint for the caretrace console script."""
    app()
