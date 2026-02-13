#!/usr/bin/env bash
set -euo pipefail

echo ">>> Local rebuild (current arch) for app services..."
./build-local-mscl.sh
./build-local-redlab.sh
echo ">>> Done (mscl-app + redlab-app)."
