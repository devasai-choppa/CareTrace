# Data sources

Every file in the demo is synthetic or a small public-code seed. Do not point the loader at a real patient's bill.

## Hospital price files

`config/sources.yaml` lists five Boston hospitals: Massachusetts General Hospital, Brigham and Women's Hospital, Beth Israel Deaconess Medical Center, Tufts Medical Center, and Boston Medical Center. Each `website_root` is the host of a `cms-hpt.txt` that was fetched and confirmed to be a CMS key/value index on 2026-10-20:

| source_id | Index |
| --- | --- |
| mgh, bwh | `https://www.massgeneralbrigham.org/cms-hpt.txt` |
| bidmc | `https://www.bidmc.org/cms-hpt.txt` |
| tufts | `https://www.tuftsmedicine.org/cms-hpt.txt` |
| bmc | `https://www.bmc.org/cms-hpt.txt` |

Those indexes also name sibling hospitals. Discovery keeps the location whose name matches the configured hospital, so a Mass General Brigham fetch for `mgh` does not attach Faulkner, Newton-Wellesley, or the other system files. `https://www.bmc.org/cms-hpt.txt` redirects to `https://www.bmchealthsystem.org/cms-hpt.txt`; the Boston Medical Center block is still the one kept, not Brighton or South. `mrf_url_override` stays empty while the index exists.

A live `caretrace discover` on 2026-10-20 stored one file per hospital:

| source_id | Machine-readable file |
| --- | --- |
| mgh | `https://www.massgeneralbrigham.org/price-transparency/042697983_Massachusetts-General-Hospital_StandardCharges.zip` |
| bwh | `https://www.massgeneralbrigham.org/price-transparency/042312909_Brigham-and-Womens-Hospital_StandardCharges.zip` |
| bidmc | `https://bidmc.org/042103881_beth-israel-deaconess-medical-center_standardcharges.json` |
| tufts | `https://www.tuftsmedicine.org/sites/default/files/2026-03/043400617_tuftsmedicalcenter_standardcharges.csv.zip` |
| bmc | `https://www.bmchealthsystem.org/wp-content/uploads/043314093_BostonMedicalCenter_standardcharges.csv.zip` |

These files are large and are not committed. `caretrace download --max-gb` is the cap if you fetch one.

The committed samples in `data/samples/` follow the CMS template v2 shape: one JSON file, one tall CSV, and one wide CSV, plus two more JSON files so all five hospitals have a knee MRI row. They are not the hospitals' real MRFs.

## Payers

Three placeholders: Blue Cross Blue Shield of Massachusetts (`bcbsma`), Harvard Pilgrim Health Care, and Tufts Health Plan. Policy URLs are `example.invalid` until a public policy PDF is added. The imaging policy used by tests is `data/samples/policies/bcbsma_imaging.txt`.

## Codes

CPT codes are stored without AMA descriptor text. Plain labels in `dbt/seeds/shoppable_services.csv` were written for this project. HCPCS Level II and MS-DRG descriptions in the seeds are short excerpts from CMS public files. `scripts/fetch_code_refs.py` can refresh them when the CMS download is reachable.

## People

`data/samples/fhir/patient_bundle.json` is a synthetic FHIR R4 bundle (person `syn-001`). `scripts/gen_synthea.sh` documents how to generate a larger Synthea set. `scripts/make_synthetic_eobs.py` writes 30 fake EOBs with an answer key.

## What is not here

No CMS Blue Button live pull, no NPPES bulk download, and no payer Transparency-in-Coverage file. The scale module streams a synthetic TiC-shaped JSON so the reader can be timed offline.
