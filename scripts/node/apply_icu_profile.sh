#!/usr/bin/env bash
# Install the exact selected-locales-full-break-v1 filter consumed by Node's
# icutrim.py when --with-intl=small-icu is used.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_ROOT="${1:-$(pwd)}"
PROFILE="$SCRIPT_DIR/icu-selected-locales.json"
TARGET="$NODE_ROOT/tools/icu/icu_small.json"

if [ ! -f "$PROFILE" ]; then
  echo "Missing canonical ICU profile: $PROFILE" >&2
  exit 1
fi
if [ ! -d "$NODE_ROOT/tools/icu" ]; then
  echo "Node ICU tools directory not found: $NODE_ROOT/tools/icu" >&2
  exit 1
fi
cp "$PROFILE" "$TARGET"
if ! cmp -s "$PROFILE" "$TARGET"; then
  echo "Failed to install canonical ICU profile at $TARGET" >&2
  exit 1
fi

# Node's stock icutrim action uses --delete-tmp, which removes the only
# inspectable small-ICU .dat archive after generating the embedded C/object
# data. Keep that archive until verify_icu_data.py runs. This is deliberately
# an exact, fail-closed patch: the current v22.x-v24.x GYP layout has two
# platform-specific icutrim actions, and a changed upstream layout must not
# silently disable data validation.
GYP="$NODE_ROOT/tools/icu/icu-generic.gyp"
if [ ! -f "$GYP" ]; then
  echo "Missing ICU gyp definition: $GYP" >&2
  exit 1
fi
delete_tmp_count="$(grep -Fc "'--delete-tmp'," "$GYP" || true)"
if [ "$delete_tmp_count" -ne 2 ]; then
  echo "Expected exactly two ICU --delete-tmp actions, found $delete_tmp_count" >&2
  exit 1
fi
sed -i.bak "s/'--delete-tmp',/ /g" "$GYP"
rm -f "$GYP.bak"
if grep -q -- "--delete-tmp" "$GYP"; then
  echo "Failed to disable ICU temporary-data deletion in $GYP" >&2
  exit 1
fi
printf 'Applied ICU profile and retained trim data: %s\n' "$TARGET"
