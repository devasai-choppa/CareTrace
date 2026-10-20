# Data sources

Every file in the demo is synthetic or a small public-code seed. Do not point the loader at a real patient's bill.

## Hospital price files

`config/sources.yaml` lists five Boston hospitals: Massachusetts General Hospital, Brigham and Women's Hospital, Beth Israel Deaconess Medical Center, Tufts Medical Center, and Boston Medical Center. `website_root` is empty on purpose. Fill it only after you open `https://<site>/cms-hpt.txt` and confirm the CMS index file.

The committed samples in `data/samples/` follow the CMS template v2 shape: one JSON file, one tall CSV, and one wide CSV, plus two more JSON files so all five hospitals have a knee MRI row. They are not the hospitals' real MRFs.

## Payers

Three placeholders: Blue Cross Blue Shield of Massachusetts (`bcbsma`), Harvard Pilgrim Health Care, and Tufts Health Plan. Policy URLs are `example.invalid` until a public policy PDF is added. The imaging policy used by tests is `data/samples/policies/bcbsma_imaging.txt`.

## Codes

CPT codes are stored without AMA descriptor text. Plain labels in `dbt/seeds/shoppable_services.csv` were written for this project. HCPCS Level II and MS-DRG descriptions in the seeds are short excerpts from CMS public files. `scripts/fetch_code_refs.py` can refresh them when the CMS download is reachable.

## People

`data/samples/fhir/patient_bundle.json` is a synthetic FHIR R4 bundle (person `syn-001`). `scripts/gen_synthea.sh` documents how to generate a larger Synthea set. `scripts/make_synthetic_eobs.py` writes 30 fake EOBs with an answer key.

## What is not here

No CMS Blue Button live pull, no NPPES bulk download, and no payer Transparency-in-Coverage file. The scale module streams a synthetic TiC-shaped JSON so the reader can be timed offline.
