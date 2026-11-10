#!/bin/bash
# Lanzador del Bot Auditor y Postulador de Pestañas Abiertas en Brave
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PYTHON_BIN="python3"
if command -v python3.13 >/dev/null 2>&1; then
    PYTHON_BIN="python3.13"
elif [ -x "/home/dhamit/.local/bin/python3.13" ]; then
    PYTHON_BIN="/home/dhamit/.local/bin/python3.13"
fi

exec "$PYTHON_BIN" "$DIR/revisar_pestanas_abiertas.py" "$@"

