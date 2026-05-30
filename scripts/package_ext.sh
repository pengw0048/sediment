#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p dist
zip -r dist/pke-ext.zip pke-ext
