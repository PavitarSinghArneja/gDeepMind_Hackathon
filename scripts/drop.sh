#!/usr/bin/env bash
# Drop a prepared file into a watched folder during the demo: scripts/drop.sh jio|airtel|lab
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-}" in
  jio)    src=$(ls mock/_demo_drops/Screenshot*.png | head -1); dest=mock/Screenshots ;;
  airtel) src=$(ls mock/_demo_drops/Airtel_Bill_*.pdf | head -1); dest=mock/Downloads ;;
  lab)    src=mock/_demo_drops/Apollo_followup.pdf; dest=mock/Downloads ;;
  *) echo "usage: scripts/drop.sh jio|airtel|lab"; exit 1 ;;
esac
cp "$src" "$dest/"
echo "Dropped $(basename "$src") into $dest"
