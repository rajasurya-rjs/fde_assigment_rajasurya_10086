#!/usr/bin/env bash
# Fetch the FlashEats classroom pack (instructor repo) next to these notebooks.
# The pack is course material and is not committed here; the commit is pinned so results reproduce.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d flasheats-classroom-pack/.git ]; then
  git clone --quiet https://github.com/manangupta12/flasheats-classroom-pack.git flasheats-classroom-pack
fi
git -C flasheats-classroom-pack checkout --quiet 0105c7a
echo "Pack ready at $(pwd)/flasheats-classroom-pack (commit $(git -C flasheats-classroom-pack rev-parse --short HEAD))"
