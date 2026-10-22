#!/usr/bin/env bash
# Generate about 1,000 synthetic Massachusetts patients with Synthea.
# CareTrace never commits the output. Point SYNTHEA_OUT at a local directory.
set -euo pipefail
: "${SYNTHEA_JAR:?Set SYNTHEA_JAR to the Synthea jar path}"
OUT="${SYNTHEA_OUT:-data/raw/synthea}"
mkdir -p "$OUT"
java -jar "$SYNTHEA_JAR" -p 1000 Massachusetts \
  --exporter.fhir.export true \
  --exporter.baseDirectory "$OUT"
echo "FHIR bundles are in $OUT. Convert with: uv run python -c 'from caretrace.clinical.fhir_to_omop import convert_bundles; from pathlib import Path; print(convert_bundles(list(Path(\"$OUT\").rglob(\"*.json\"))))'"
