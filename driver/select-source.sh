#!/bin/sh
# Copy the patched dell-smm-hwmon source for this kernel series (e.g. 7.0) into place.
set -e
ser=$(echo "$1" | cut -d. -f1-2)
d=$(dirname "$0")
src="$d/src/dell-smm-hwmon-$ser.c"
[ -f "$src" ] || { echo "dell-smm-hwmon-qbm1250: no source for kernel series $ser" >&2; exit 1; }
cp "$src" "$d/dell-smm-hwmon.c"
echo "dell-smm-hwmon-qbm1250: using source for $ser"
