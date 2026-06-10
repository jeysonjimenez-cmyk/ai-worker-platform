#!/usr/bin/env bash
set -euo pipefail

echo "→ verifying Docker GPU access on ialab..."

OUTPUT=$(docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi 2>&1)
STATUS=$?

if [ $STATUS -ne 0 ]; then
    echo "✗ nvidia-smi failed inside Docker"
    echo "$OUTPUT"
    exit 1
fi

echo "$OUTPUT"

if echo "$OUTPUT" | grep -q "RTX 4070 Ti SUPER"; then
    echo "✓ RTX 4070 Ti SUPER detected"
else
    echo "⚠ GPU detected but not RTX 4070 Ti SUPER — check output above"
fi
