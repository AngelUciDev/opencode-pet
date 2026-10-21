#!/usr/bin/env bash
# Pruebas de la ventana de configuración y del menú del clic derecho.
# Necesita un X activo: DISPLAY=:0.0 ./test-menu-settings.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
export DISPLAY="${DISPLAY:-:0.0}"
echo "== menú del clic derecho =="
python3 -u "$HERE/test-menu.py"
echo
echo "== ventana de configuración =="
python3 -u "$HERE/test-settings.py"
