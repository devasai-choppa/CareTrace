"""HTTP API. Every price response keeps the lineage fields on the quote."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from caretrace.agent.graph import run_agent
from caretrace.audit.appeal import draft_letter, write_pdf
from caretrace.audit.engine import run_audit
from caretrace.audit.ingest_eob import ingest_path
from caretrace.audit.rules.builtin import AuditContext
from caretrace.config import get_settings
from caretrace.directory.payer_directory import MockDirectory
from caretrace.directory.trust import score_entry
from caretrace.models.domain import Finding
from caretrace.phi import PhiRejectedError, assert_no_phi
from caretrace.warehouse import compliance_table, data_mode, get_rates

DISCLAIMER = (
    "Estimates are not quotes. Appeal letters are drafts. This is not legal or medical advice."
)
PHI_NOTICE = "Demo mode rejects real patient data. Upload only synthetic EOBs from data/samples/."


class ChatRequest(BaseModel):
    session_id: str = "demo"
    message: str


class ChatResponse(BaseModel):
    answer: str
    citations: list[dict[str, Any]]
    confidence: float
    trace_id: str
    disclaimer: str = DISCLAIMER


class PriorAuthRequest(BaseModel):
    payer_id: str
    question: str


class AppealRequest(BaseModel):
    finding_ids: list[str] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    claim_id: str = "CLAIM-PLACEHOLDER"


def create_app() -> FastAPI:
    """Build the API. Tests can replace app.state after creation."""
    settings = get_settings()
    app = FastAPI(title="CareTrace", version="0.1.0", description=DISCLAIMER)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next: Any) -> Response:
        request.state.request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        content_type = request.headers.get("content-type", "")
        if (
            settings.demo_mode
            and request.method in {"POST", "PUT"}
            and "multipart/" not in content_type
        ):
            body = await request.body()
            try:
                assert_no_phi(body.decode("utf-8", errors="ignore"), demo_mode=True)
            except PhiRejectedError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=400)
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

    @app.get("/v1/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "warehouse": settings.warehouse,
            "data_mode": data_mode(settings),
            "demo_mode": "true" if settings.demo_mode else "false",
        }

    @app.get("/v1/sources", tags=["meta"])
    def sources() -> dict[str, Any]:
        try:
            rows = compliance_table(settings)
        except Exception:
            rows = []
        return {
            "hospitals": rows,
            "disclaimer": DISCLAIMER,
            "data_mode": data_mode(settings),
            "phi_notice": PHI_NOTICE,
        }

    @app.post("/v1/chat", tags=["agent"], response_model=ChatResponse)
    def chat(payload: ChatRequest) -> ChatResponse:
        try:
            assert_no_phi(payload.message, demo_mode=settings.demo_mode)
        except PhiRejectedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = run_agent(payload.message)
        return ChatResponse(
            answer=result.answer,
            citations=[item.model_dump() for item in result.citations],
            confidence=result.confidence,
            trace_id=result.trace_id,
        )

    @app.get("/v1/prices", tags=["prices"])
    def prices(
        code: str,
        payer_id: str,
        city: str | None = None,
        plan: str | None = None,
        commercial_only: bool = True,
    ) -> dict[str, Any]:
        quotes = get_rates(
            code,
            payer_id,
            city=city,
            plan=plan,
            commercial_only=commercial_only,
            published_only=True,
            settings=settings,
        )
        return {
            "quotes": [
                {
                    "value": item.value.model_dump(),
                    "confidence": item.confidence,
                    "confidence_reasons": item.confidence_reasons,
                    "lineage": [line.model_dump(mode="json") for line in item.lineage],
                }
                for item in quotes
            ],
            "disclaimer": DISCLAIMER,
            "filters": {
                "plan": plan,
                "commercial_only": commercial_only,
                "published_only": True,
            },
            "data_mode": data_mode(settings),
        }

    @app.get("/v1/providers/{npi}/trust", tags=["directory"])
    def trust(npi: str, payer_id: str) -> dict[str, Any]:
        entries = MockDirectory().search(payer_id, npi=npi)
        if not entries:
            raise HTTPException(status_code=404, detail="provider not in the demo directory")
        scored = score_entry(entries[0])
        return scored.model_dump()

    @app.post("/v1/prior-auth/ask", tags=["policy"])
    def prior_auth(payload: PriorAuthRequest) -> dict[str, Any]:
        from caretrace.agent.tools import tool_prior_auth

        return tool_prior_auth(payload.payer_id, payload.question)

    @app.post("/v1/audit", tags=["audit"])
    async def audit(file: UploadFile = File(...)) -> dict[str, Any]:
        content = await file.read()
        limit = settings.max_upload_mb * 1024 * 1024
        if len(content) > limit:
            raise HTTPException(status_code=413, detail="upload exceeds the demo size limit")
        import tempfile
        from pathlib import Path

        suffix = Path(file.filename or "upload.txt").suffix or ".txt"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(content)
            path = Path(handle.name)
        lines = ingest_path(path, demo_mode=settings.demo_mode)
        report = run_audit(lines, _audit_context(lines))
        return {
            "lines": [line.model_dump() for line in lines],
            "findings": [finding.model_dump() for finding in report["findings"]],
            "potential_savings": report["potential_savings"],
            "disclaimer": DISCLAIMER,
            "phi_notice": PHI_NOTICE,
            "comparison_note": (
                "Savings compare the allowed amount to the lowest commercial published "
                "dollar for the code and payer. Medicare-looking plans are excluded."
            ),
        }

    @app.post("/v1/appeal", tags=["audit"])
    def appeal(payload: AppealRequest) -> dict[str, Any]:
        findings = [Finding.model_validate(item) for item in payload.findings]
        if payload.finding_ids:
            findings = [item for item in findings if item.rule_id in payload.finding_ids]
        claim_id = _safe_claim_id(payload.claim_id)
        letter = draft_letter(findings, claim_id=claim_id)
        pdf_path = settings.data_dir / "exports" / f"{claim_id}.pdf"
        write_pdf(letter, pdf_path)
        return {
            "markdown": letter,
            "pdf_url": f"/v1/appeal/{claim_id}.pdf",
            "disclaimer": DISCLAIMER,
            "phi_notice": PHI_NOTICE,
        }

    @app.get("/v1/appeal/{claim_id}.pdf", tags=["audit"])
    def appeal_file(claim_id: str) -> FileResponse:
        safe = _safe_claim_id(claim_id)
        path = settings.data_dir / "exports" / f"{safe}.pdf"
        if not path.exists():
            raise HTTPException(status_code=404, detail="letter not found")
        return FileResponse(path, media_type="application/pdf", filename=f"{safe}.pdf")

    return app


def _audit_context(lines: list[Any], payer_id: str = "bcbsma") -> AuditContext:
    """Attach published commercial rates and a directory score for the rules."""
    from caretrace.models.domain import EobLine
    from caretrace.warehouse import get_rates

    parsed = [line if isinstance(line, EobLine) else EobLine.model_validate(line) for line in lines]
    import duckdb

    rates = {}
    for line in parsed:
        if not line.code or line.code in rates:
            continue
        try:
            quotes = get_rates(
                line.code,
                payer_id,
                commercial_only=True,
                published_only=True,
            )
        except duckdb.CatalogException:
            break
        if quotes:
            rates[line.code] = quotes[0].value
    directory = None
    for line in parsed:
        if not line.provider_npi:
            continue
        entries = MockDirectory().search(payer_id, npi=line.provider_npi)
        if entries:
            directory = score_entry(entries[0]).score
            break
    return AuditContext(rates_by_code=rates, directory_confidence=directory)


def _safe_claim_id(claim_id: str) -> str:
    """Keep claim ids inside the exports directory."""
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", claim_id):
        raise HTTPException(status_code=400, detail="claim_id must be letters, digits, _ or -")
    return claim_id


app = create_app()
