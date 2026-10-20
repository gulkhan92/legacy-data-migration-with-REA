#!/usr/bin/env bash
# Build ClinicDesk.dll, then run it on the prepared CSV files to produce clinic.cdb and its layout.
# Uses a local .NET SDK when present, otherwise the official SDK image (pinned tag).
set -euo pipefail

SDK_IMAGE="mcr.microsoft.com/dotnet/sdk:8.0"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATASETS="$(cd "$HERE/../.." && pwd)"
IMPORT="$DATASETS/raw/clinic_import"
FIXTURE="$DATASETS/fixtures/clinic"

[ -f "$IMPORT/patients.csv" ] || { echo "Missing $IMPORT. Run prepare_clinic_inputs.py first." >&2; exit 1; }
mkdir -p "$FIXTURE"

native() {
  local build
  build="$(mktemp -d)"
  cp -r "$HERE/." "$build/src"
  rm -rf "$build/src/bin" "$build/src/obj"
  dotnet build "$build/src/ClinicDesk.csproj" -c Release -o "$build/out" --nologo -v quiet
  cp "$build/out/ClinicDesk.dll" "$FIXTURE/ClinicDesk.dll"
  cp "$build/out/ClinicDesk.runtimeconfig.json" "$FIXTURE/ClinicDesk.runtimeconfig.json"
  dotnet "$FIXTURE/ClinicDesk.dll" import "$IMPORT" "$FIXTURE/clinic.cdb" "$FIXTURE/layout.json"
  rm -rf "$build"
}

containerised() {
  local win_datasets
  win_datasets="$(cd "$DATASETS" && (pwd -W 2>/dev/null || pwd))"
  # The source is copied inside the container so no bin or obj folders appear in the repository.
  MSYS_NO_PATHCONV=1 docker run --rm -v "${win_datasets}:/datasets" "$SDK_IMAGE" bash -c '
    set -euo pipefail
    export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
    cp -r /datasets/scripts/build_legacy_clinic /tmp/src
    rm -rf /tmp/src/bin /tmp/src/obj
    dotnet build /tmp/src/ClinicDesk.csproj -c Release -o /tmp/out --nologo -v quiet
    cp /tmp/out/ClinicDesk.dll /datasets/fixtures/clinic/ClinicDesk.dll
    cp /tmp/out/ClinicDesk.runtimeconfig.json /datasets/fixtures/clinic/ClinicDesk.runtimeconfig.json
    dotnet /datasets/fixtures/clinic/ClinicDesk.dll import /datasets/raw/clinic_import /datasets/fixtures/clinic/clinic.cdb /datasets/fixtures/clinic/layout.json
  '
}

if command -v dotnet >/dev/null 2>&1; then native; else containerised; fi
echo "Built $FIXTURE/ClinicDesk.dll and $FIXTURE/clinic.cdb"
