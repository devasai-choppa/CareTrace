# Demo script (about 3 minutes)

Setup, once: `uv run caretrace load-samples`, then `make api` and `make ui`. Open `http://localhost:8501`. The banner says demo mode.

1. **Estimate (45s).** Service `knee MRI`, payer `bcbsma`, ZIP `02115`, code `73721`. Five Boston hospitals show negotiated dollars from 980 to 1750. Open View source on one card and read the file path and the row hash. Point at the trust badge for Priya Shah and the prior-auth panel, which cites page 2 of the sample imaging policy.

2. **The ghost (20s).** Say that NPI `1000000001` is the inactive listing. The directory score flags ghost risk because the NPI is not active. That is the "is this provider really in network?" question.

3. **Audit (60s).** Upload `data/samples/eobs/eob-00-r1.pdf`. The line allowed $2200. The API compares that with the lowest published Boston dollar for CPT 73721 and `bcbsma`, which is $980 at Boston Medical Center. Rule R1 reports $1220 and the finding cites that MRF row hash.

4. **Letter (30s).** Click Draft appeal. The PDF header says the letter is a draft and not legal advice. The dollar amounts in the letter match the finding. The model writes the paragraph; the template inserts the numbers.

5. **Ask (20s).** Question: `knee MRI with payer X near 02115`. The answer lists the five prices with `[[q_n]]` tokens and says prior authorization is required, with the policy page.

Close on the Data & trust page: estimates are not quotes, CPT descriptors are not stored, and the patient bundle is synthetic.
