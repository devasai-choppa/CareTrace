"""CareTrace demo. The pages call the FastAPI backend."""

from __future__ import annotations

import os
from typing import Any

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

API = os.environ.get("CARETRACE_API_URL", "http://localhost:8000")
ACCENT = "#0F766E"
DISCLAIMER = "Estimates are not quotes. Appeal letters are drafts. Not legal or medical advice."

st.set_page_config(page_title="CareTrace", layout="wide")
st.markdown(
    f"""
    <style>
    .stApp {{ background: #F7F5F2; }}
    .ct-banner {{ background: {ACCENT}; color: white; padding: 0.6rem 1rem; border-radius: 8px; }}
    .ct-card {{ background: white; border-radius: 12px; padding: 1rem 1.1rem; border: 1px solid #E7E2DA; }}
    </style>
    """,
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="ct-banner">Demo mode. Synthetic records only. No real patient data.</div>',
    unsafe_allow_html=True,
)
st.title("CareTrace")
st.caption(DISCLAIMER)

page = st.sidebar.radio(
    "Go to",
    ["Estimate", "Audit my bill", "Ask CareTrace", "Market view", "Data & trust"],
)


def api_get(path: str, **params: Any) -> dict[str, Any]:
    """GET a JSON endpoint."""
    response = httpx.get(f"{API}{path}", params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {"data": payload}


def api_post(path: str, payload: dict[str, Any] | None = None, files: Any = None) -> dict[str, Any]:
    """POST JSON or a file."""
    if files:
        response = httpx.post(f"{API}{path}", files=files, timeout=120)
    else:
        response = httpx.post(f"{API}{path}", json=payload or {}, timeout=120)
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {"data": body}


if page == "Estimate":
    st.subheader("What will it cost, and is it covered?")
    left, right = st.columns([1, 2])
    with left:
        service = st.text_input("Service", "knee MRI")
        payer = st.selectbox("Payer", ["bcbsma", "harvard-pilgrim", "tufts-health"])
        plan = st.text_input("Plan", "HMO Blue")
        zip_code = st.text_input("ZIP", "02115")
        deductible = st.number_input("Deductible remaining", value=200.0)
        coinsurance = st.number_input("Coinsurance (fraction)", value=0.2)
        copay = st.number_input("Copay", value=50.0)
        code = st.text_input("Code", "73721")
    with right:
        try:
            prices = api_get("/v1/prices", code=code, payer_id=payer, city="Boston")
            quotes = prices.get("quotes", [])
            cards = st.columns(min(3, max(1, len(quotes))) or 1)
            rows = []
            for index, item in enumerate(quotes):
                value = item["value"]
                rows.append(
                    {
                        "hospital": value["hospital_name"],
                        "negotiated": value["rate_dollar"],
                        "cash": value["discounted_cash"],
                    }
                )
                with cards[index % len(cards)]:
                    st.markdown('<div class="ct-card">', unsafe_allow_html=True)
                    st.markdown(f"**{value['hospital_name']}**")
                    st.metric(
                        "Negotiated",
                        f"${value['rate_dollar']:.0f}" if value["rate_dollar"] else "—",
                    )
                    st.caption(
                        f"Cash ${value['discounted_cash']:.0f}"
                        if value["discounted_cash"]
                        else "Cash n/a"
                    )
                    with st.expander("View source"):
                        lineage = item["lineage"][0]
                        st.write(lineage["source_file_uri"])
                        st.write(lineage["source_row_hash"])
                        st.write(lineage["ingested_at"])
                    st.markdown("</div>", unsafe_allow_html=True)
            if rows:
                frame = pd.DataFrame(rows)
                st.plotly_chart(
                    px.bar(
                        frame,
                        x="hospital",
                        y="negotiated",
                        color_discrete_sequence=[ACCENT],
                        title="Negotiated rate spread",
                    ),
                    use_container_width=True,
                )
            trust = api_get("/v1/providers/1234567893/trust", payer_id=payer)
            st.markdown(f"**Provider trust** {trust['provider_name']}: {trust['score']:.2f}")
            if trust["ghost_risk"]:
                st.warning("Ghost risk")
            else:
                st.success("In-network listing looks corroborated")
            prior = api_post(
                "/v1/prior-auth/ask",
                {"payer_id": payer, "question": f"does {code} {service} need prior authorization?"},
            )
            st.markdown("**Prior authorization**")
            st.write(prior.get("prior_auth", prior))
            st.caption(
                f"Plan {plan}, ZIP {zip_code}, deductible {deductible}, coinsurance {coinsurance}, copay {copay}."
            )
        except httpx.HTTPError as exc:
            st.error(f"Start the API with `make api` ({exc}).")

elif page == "Audit my bill":
    st.subheader("Was I billed correctly?")
    upload = st.file_uploader("Synthetic EOB", type=["txt", "pdf", "csv", "json"])
    if upload is not None:
        try:
            report = api_post("/v1/audit", files={"file": (upload.name, upload.getvalue())})
            lines = pd.DataFrame(report["lines"])
            st.dataframe(lines, use_container_width=True)
            findings = report["findings"]
            st.metric("Potential savings", f"${report['potential_savings']:.2f}")
            for finding in findings:
                st.markdown(
                    f"**{finding['rule_id']}** ${finding['impact']:.2f} — {finding['explanation']}"
                )
            if st.button("Draft appeal"):
                letter = api_post(
                    "/v1/appeal",
                    {
                        "findings": findings,
                        "claim_id": "SYNTHETIC",
                        "finding_ids": [item["rule_id"] for item in findings],
                    },
                )
                st.markdown(letter["markdown"])
                pdf = httpx.get(f"{API}{letter['pdf_url']}", timeout=60)
                pdf.raise_for_status()
                st.download_button(
                    "Download PDF",
                    data=pdf.content,
                    file_name="appeal.pdf",
                    mime="application/pdf",
                )
        except httpx.HTTPError as exc:
            st.error(f"Audit failed: {exc}")

elif page == "Ask CareTrace":
    st.subheader("Ask a question")
    prompt = st.text_input("Question", "knee MRI with payer X near 02115")
    if st.button("Ask") or prompt:
        try:
            answer = api_post("/v1/chat", {"session_id": "demo", "message": prompt})
            st.write(answer["answer"])
            for index, citation in enumerate(answer.get("citations", []), start=1):
                st.caption(
                    f"[{index}] {citation.get('source_file_uri')} {citation.get('source_row_hash')}"
                )
            st.caption(f"Confidence {answer['confidence']:.2f}")
        except httpx.HTTPError as exc:
            st.error(f"Start the API with `make api` ({exc}).")

elif page == "Market view":
    st.subheader("Price variance and transparency")
    try:
        sources = api_get("/v1/sources")
        frame = pd.DataFrame(sources.get("hospitals", []))
        if not frame.empty:
            st.plotly_chart(
                px.bar(
                    frame,
                    x="hospital_name",
                    y="transparency_score",
                    color_discrete_sequence=[ACCENT],
                ),
                use_container_width=True,
            )
            st.dataframe(frame, use_container_width=True)
        prices = api_get("/v1/prices", code="73721", payer_id="bcbsma", city="Boston")
        spread = pd.DataFrame([item["value"] for item in prices.get("quotes", [])])
        if not spread.empty:
            st.plotly_chart(px.box(spread, y="rate_dollar", points="all"), use_container_width=True)
    except httpx.HTTPError as exc:
        st.error(f"Start the API with `make api` ({exc}).")

else:
    st.subheader("Data and trust")
    st.markdown(
        """
        Every rate on the Estimate page carries a source file, an ingest time, and a row hash.
        Confidence is the quality score of that published row. Percent-only and algorithm-only
        rows are not shown as a hospital's dollar price.

        CPT codes are stored without AMA descriptor text. The plain labels in the shoppable
        list were written for this demo. Sample patients come from a synthetic FHIR bundle,
        not from a real record.
        """
    )
    try:
        st.json(api_get("/v1/health"))
        st.dataframe(
            pd.DataFrame(api_get("/v1/sources").get("hospitals", [])), use_container_width=True
        )
    except httpx.HTTPError as exc:
        st.info(f"API not reachable at {API}: {exc}")
