#!/usr/bin/env bash
set -euo pipefail
bws run -- pnpm dlx socket scan create . --org Ezequiel --json
