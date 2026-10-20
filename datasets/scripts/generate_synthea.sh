#!/usr/bin/env bash
# Generate synthetic patients with Synthea (pinned release) as CSV.
# Uses a local Java runtime when present, otherwise a Docker image with Java.
#
# Environment: PATIENTS (default 500), SEED (default 42), REFERENCE_DATE (default 20260101)
set -euo pipefail

VERSION="v4.0.0"
JAR_SHA256="ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1"
PATIENTS="${PATIENTS:-500}"
SEED="${SEED:-42}"
REFERENCE_DATE="${REFERENCE_DATE:-20260101}"
JAVA_IMAGE="eclipse-temurin:21-jre"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAW="$(cd "$HERE/.." && pwd)/raw"
JAR="$RAW/synthea-with-dependencies.jar"
OUT="$RAW/synthea"
mkdir -p "$RAW"

sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

if [ ! -f "$JAR" ]; then
  echo "Downloading Synthea ${VERSION}"
  curl --fail --silent --show-error --location --output "$JAR" \
    "https://github.com/synthetichealth/synthea/releases/download/${VERSION}/synthea-with-dependencies.jar"
fi
if [ "$(sha "$JAR")" != "$JAR_SHA256" ]; then
  echo "Checksum mismatch for the Synthea jar" >&2
  rm -f "$JAR"
  exit 1
fi

rm -rf "$OUT"
mkdir -p "$OUT"

ARGS=(-p "$PATIENTS" -s "$SEED" -cs "$SEED" -r "$REFERENCE_DATE" Massachusetts
  --exporter.csv.export=true
  --exporter.fhir.export=false
  --exporter.hospital.fhir.export=false
  --exporter.practitioner.fhir.export=false
  --exporter.years_of_history=3
  --generate.thread_pool_size=1)

if command -v java >/dev/null 2>&1; then
  (cd "$OUT" && java -jar "$JAR" "${ARGS[@]}")
else
  # MSYS_NO_PATHCONV keeps Git Bash on Windows from rewriting the container paths.
  WIN_RAW="$(cd "$RAW" && (pwd -W 2>/dev/null || pwd))"
  MSYS_NO_PATHCONV=1 docker run --rm -v "${WIN_RAW}:/data" -w /data/synthea "$JAVA_IMAGE" \
    java -jar /data/synthea-with-dependencies.jar "${ARGS[@]}"
fi
echo "Synthea output: $OUT/output/csv"
