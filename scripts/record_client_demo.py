"""Record a paced CareTrace client demo (app viewport only, no desktop dock)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

UI = "http://localhost:8501"
EOB = Path("data/samples/eobs/eob-00-r1.pdf")
OUT_DIR = Path("/tmp/caretrace_client_demo")
FINAL_MP4 = Path("/opt/cursor/artifacts/caretrace_full_client_demo_walkthrough.mp4")


def banner(page: Page, text: str, seconds: float = 3.0) -> None:
    """Show a temporary on-screen title for the client narration."""
    page.evaluate(
        """([text, ms]) => {
          const existing = document.getElementById('ct-demo-banner');
          if (existing) existing.remove();
          const el = document.createElement('div');
          el.id = 'ct-demo-banner';
          el.textContent = text;
          el.style.cssText = [
            'position:fixed', 'top:16px', 'left:50%', 'transform:translateX(-50%)',
            'z-index:2147483647', 'background:#0F766E', 'color:#fff',
            'padding:14px 28px', 'border-radius:10px',
            'font:600 22px/1.3 system-ui,sans-serif',
            'box-shadow:0 8px 24px rgba(0,0,0,.25)', 'max-width:90%',
            'text-align:center', 'pointer-events:none'
          ].join(';');
          document.body.appendChild(el);
          setTimeout(() => el.remove(), ms);
        }""",
        [text, int(seconds * 1000)],
    )
    page.wait_for_timeout(int(seconds * 1000))


def go(page: Page, label: str) -> None:
    page.locator('[data-testid="stSidebar"]').get_by_text(label, exact=True).click(force=True)
    page.wait_for_timeout(2000)


def main() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    FINAL_MP4.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=str(OUT_DIR),
            record_video_size={"width": 1440, "height": 900},
        )
        page = context.new_page()
        page.goto(UI, wait_until="networkidle", timeout=90_000)
        page.wait_for_timeout(2500)

        banner(page, "CareTrace client demo — estimate to appeal on live Boston rates", 4.0)
        banner(
            page,
            "Demo mode: synthetic patients and EOBs. Estimates are not quotes.",
            3.5,
        )

        # --- Estimate ---
        banner(page, "1. Estimate — what did hospitals publish for a knee MRI?", 3.5)
        go(page, "Estimate")
        page.wait_for_selector("text=Negotiated", timeout=60_000)
        page.wait_for_timeout(2000)
        banner(
            page,
            "CPT 73721 · payer bcbsma · ZIP 02115 · live published dollars",
            3.5,
        )
        page.wait_for_timeout(2500)
        banner(
            page,
            "Live rates: Tufts $446 · BMC $574 · Beth Israel $638 · Brigham $967",
            4.0,
        )
        page.wait_for_timeout(2000)

        # Open first View source expander
        expanders = page.locator('[data-testid="stExpander"]')
        if expanders.count() > 0:
            expanders.first.click()
            page.wait_for_timeout(1500)
            banner(page, "View source — every dollar has a file URL and row hash", 3.5)
            page.wait_for_timeout(2500)

        page.mouse.wheel(0, 600)
        page.wait_for_timeout(1500)
        banner(page, "Provider trust score and prior-auth citation from the policy", 3.5)
        page.wait_for_timeout(3000)

        # --- Audit ---
        banner(page, "2. Audit — does the bill match the published rate?", 3.5)
        go(page, "Audit my bill")
        page.wait_for_timeout(1500)
        banner(page, "Upload a synthetic EOB (never a real patient bill in demo)", 3.0)
        page.locator('input[type="file"]').set_input_files(str(EOB.resolve()))
        page.wait_for_selector("text=Potential savings", timeout=90_000)
        page.wait_for_timeout(2000)
        banner(
            page,
            "Rule R1: allowed $2200 vs lowest live published rate → savings shown",
            4.0,
        )
        page.wait_for_timeout(3000)

        # --- Appeal ---
        banner(page, "3. Draft appeal — numbers come from the finding, not the model", 3.5)
        page.get_by_role("button", name="Draft appeal").click()
        page.wait_for_timeout(4000)
        # Wait for letter content or download button
        try:
            page.wait_for_selector("text=Download PDF", timeout=60_000)
        except Exception:
            page.wait_for_timeout(3000)
        banner(page, "Letter is a draft, not legal advice — dollars match the audit", 4.0)
        page.wait_for_timeout(3500)

        # --- Ask ---
        banner(page, "4. Ask CareTrace — one question, cited answer", 3.5)
        go(page, "Ask CareTrace")
        page.wait_for_timeout(1500)
        page.locator('[data-testid="stTextInputField"]').fill("knee MRI with payer X near 02115")
        page.get_by_role("button", name="Ask").click()
        page.wait_for_selector("text=445.72", timeout=90_000)
        page.wait_for_timeout(2000)
        banner(
            page,
            "Answer lists live prices with citations and prior-auth page 2",
            4.0,
        )
        page.mouse.wheel(0, 500)
        page.wait_for_timeout(3500)

        # --- Market ---
        banner(page, "5. Market view — transparency scores and rate spread", 3.5)
        go(page, "Market view")
        page.wait_for_timeout(3500)
        page.mouse.wheel(0, 400)
        page.wait_for_timeout(2500)
        banner(page, "Hospital transparency score and negotiated-rate distribution", 3.5)
        page.wait_for_timeout(3000)

        # --- Data & trust ---
        banner(page, "6. Data & trust — lineage, no real PHI, CPT codes only", 3.5)
        go(page, "Data & trust")
        page.wait_for_timeout(2500)
        page.mouse.wheel(0, 400)
        page.wait_for_timeout(2500)
        banner(
            page,
            "Every rate carries source file, ingest time, and row hash",
            3.5,
        )
        page.wait_for_timeout(3000)

        banner(
            page,
            "CareTrace closes the loop: estimate → bill audit → draft appeal",
            4.0,
        )
        page.wait_for_timeout(2000)

        context.close()
        browser.close()

    videos = list(OUT_DIR.glob("*.webm"))
    if not videos:
        raise SystemExit("No webm video was recorded")
    webm = videos[0]
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(webm),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(FINAL_MP4),
        ]
    )
    print(f"wrote {FINAL_MP4} ({FINAL_MP4.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
