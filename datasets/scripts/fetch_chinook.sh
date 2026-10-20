#!/usr/bin/env bash
# Download the Chinook SQLite database (pinned release) and verify its checksum.
set -euo pipefail

VERSION="v1.4.5"
SHA256="bdf635be69850bd3be09c9a2dbeef7ddfb80036bd3ef3381383cd03b61e4a61a"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAW="$HERE/../raw"
mkdir -p "$RAW"

TARGET="$RAW/Chinook_Sqlite.sqlite"
URL="https://github.com/lerocha/chinook-database/releases/download/${VERSION}/Chinook_Sqlite.sqlite"
LICENSE_URL="https://raw.githubusercontent.com/lerocha/chinook-database/${VERSION}/LICENSE.md"

if [ ! -f "$TARGET" ]; then
  echo "Downloading Chinook ${VERSION}"
  curl --fail --silent --show-error --location --output "$TARGET" "$URL"
fi
curl --fail --silent --show-error --location --output "$RAW/chinook_LICENSE.md" "$LICENSE_URL"

if command -v sha256sum >/dev/null 2>&1; then ACTUAL="$(sha256sum "$TARGET" | cut -d' ' -f1)"; else ACTUAL="$(shasum -a 256 "$TARGET" | cut -d' ' -f1)"; fi
if [ "$ACTUAL" != "$SHA256" ]; then
  echo "Checksum mismatch for Chinook: expected $SHA256, got $ACTUAL" >&2
  rm -f "$TARGET"
  exit 1
fi
echo "Chinook ${VERSION} verified: $ACTUAL"
