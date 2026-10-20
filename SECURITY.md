# Security

CareTrace is a demo. It is not a medical device, a payer system, or a place to put real patient records.

## No real PHI

Do not upload a real explanation of benefits, a real bill, or any file that contains a Social Security number, a real medical record number, or a real patient's name. Demo mode rejects text that matches an SSN pattern (`###-##-####` or a bare 9-digit run) on JSON requests. PDF uploads are checked after text extraction so binary bytes are not scanned as identifiers.

Synthetic patients come from a hand-written FHIR bundle in the shape Synthea emits, and from generated explanation-of-benefit PDFs. They are not real people.

## Secrets

API keys live in the environment or `.env`, which is gitignored. Do not commit `.env`, warehouse passwords, or NPPES bulk files that you are not allowed to redistribute.

## Reporting

Open a private security advisory on the GitHub repository if you find a way the demo accepts real PHI or leaks a secret. Do not file a public issue with sample records.
