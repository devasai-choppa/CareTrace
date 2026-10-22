# Demo script (about 3 minutes)

Setup for the live Boston demo: `uv run caretrace load-live`, then `make api` and `make ui`. Open `http://localhost:8501`. The banner says demo mode. To regenerate the client video: `uv run python scripts/record_client_demo.py`.

For the offline sample path instead: `uv run caretrace load-samples` (prices then run $980–$1750).

1. **Estimate (45s).** Service `knee MRI`, payer `bcbsma`, ZIP `02115`, code `73721`. On live files the cited outpatient dollars are about Tufts $446, Boston Medical Center $574, Beth Israel $638, Brigham $967 (Mass General has no Blue Cross dollar for this code). Open View source on one card and read the hospital MRF URL and the row hash. Point at the trust badge for Priya Shah and the prior-auth panel, which cites page 2 of the sample imaging policy.

2. **The ghost (20s).** Say that NPI `1000000001` is the inactive listing. The directory score flags ghost risk because the NPI is not active. That is the "is this provider really in network?" question.

3. **Audit (60s).** Upload `data/samples/eobs/eob-00-r1.pdf`. The line allowed $2200. Against the lowest live published Boston dollar for CPT 73721 and `bcbsma` (Tufts $445.72), rule R1 reports about $1,754.28 and the finding cites that MRF row hash.

4. **Letter (30s).** Click Draft appeal. The PDF header says the letter is a draft and not legal advice. The dollar amounts in the letter match the finding. The model writes the paragraph; the template inserts the numbers.

5. **Ask (20s).** Question: `knee MRI with payer X near 02115`. The answer lists the live prices with `[[q_n]]` tokens and says prior authorization is required, with the policy page.

6. **Market view / Data & trust (20s).** Show transparency scores, then close on Data & trust: estimates are not quotes, CPT descriptors are not stored, and the patient bundle is synthetic.
