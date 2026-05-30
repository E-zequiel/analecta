#!/usr/bin/env bash
# Set SOCKET_ORG in your shell profile to skip the interactive org picker.
set -euo pipefail
bws run -- pnpm exec socket scan create . ${SOCKET_ORG:+--org "$SOCKET_ORG"} --json
