#!/usr/bin/env bash
# Instalador sin root de opencode-pet.
#
#   ./install.sh              # crea el lanzador del menú y el agente del pet
#   ./install.sh --autostart  # además lo arranca al iniciar sesión
#
# No descarga modelos de voz: eso es opcional y se hace con
# ./voice-setup.sh (piper) o ./kokoro-setup.sh.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG_DIR="$HOME/.config/opencode-pet"
APP_DIR="$HOME/.local/share/applications"
AUTOSTART_DIR="$HOME/.config/autostart"
AGENT_DIR="$HOME/.config/opencode/agents"
AGENT_SRC="$HERE/agents/pet-mascota.md"
AGENT_DST="$AGENT_DIR/pet-mascota.md"
AUTOSTART=0
for arg in "$@"; do
  case "$arg" in
    --autostart) AUTOSTART=1 ;;
    -h|--help)
      sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Opción desconocida: $arg"; exit 2 ;;
  esac
done

echo "== opencode-pet: instalación =="
echo "· repo: $HERE"

# --- dependencias ---------------------------------------------------------
fail=0
check() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "  ✓ $1"
  else
    echo "  ✗ falta $1${2:+ ($2)}"
    fail=1
  fi
}

check_opt() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "  ✓ $1"
  else
    echo "  · $1 no está${2:+: $2}"
  fi
}

check python3
check curl
check_opt aplay "alsa-utils; necesario para la voz"
check_opt arecord "alsa-utils; necesario para el micrófono"
check_opt ffmpeg "volumen/graves/nitidez del audio"
check_opt git "solo para voice-setup.sh"

# opencode puede estar en el menú de escritorio, en ~/.opencode o en el PATH
OC=""
for cand in "$HOME"/.config/ai.opencode.desktop/cli/*/opencode-cli \
            "$HOME/.opencode/bin/opencode" \
            "$(command -v opencode 2>/dev/null || true)"; do
  if [ -n "${cand:-}" ] && [ -x "$cand" ]; then OC="$cand"; break; fi
done
if [ -n "$OC" ]; then
  echo "  ✓ opencode ($OC)"
else
  echo "  ✗ no encuentro el binario de opencode"
  echo "    instálalo desde https://opencode.ai y vuelve a ejecutar"
  fail=1
fi

if python3 - <<'PY' >/dev/null 2>&1
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import cairo
PY
then
  echo "  ✓ Python: gi + GTK3 + cairo"
else
  echo "  ✗ faltan PyGObject/GTK3 o pycairo"
  echo "    Debian/Ubuntu: sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-cairo"
  echo "    Arch:          sudo pacman -S python-gobject python-cairo gtk3"
  echo "    Fedora:        sudo dnf install python3-gobject python3-cairo gtk3"
  fail=1
fi

if python3 -c "import Xlib" >/dev/null 2>&1; then
  echo "  ✓ python-xlib (atajo global Ctrl+F)"
else
  echo "  · python-xlib no está: Ctrl+F no funcionará (el resto sí)."
  echo "    pip3 install --user python-xlib"
fi

[ "$fail" = 0 ] || { echo; echo "Corrige lo marcado con ✗ y vuelve a intentarlo."; exit 1; }

# --- permisos de ejecución ------------------------------------------------
chmod +x "$HERE"/*.sh "$HERE"/*.py 2>/dev/null || true

# --- agente del pet -------------------------------------------------------
mkdir -p "$CFG_DIR" "$APP_DIR" "$AGENT_DIR"
if [ -f "$AGENT_DST" ]; then
  echo "· ya existe $AGENT_DST (no lo sobrescribo)"
else
  cp "$AGENT_SRC" "$AGENT_DST"
  echo "· agente instalado: $AGENT_DST"
fi

# --- lanzador del menú ----------------------------------------------------
DESK="$APP_DIR/opencode-pet.desktop"
cat > "$DESK" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=opencode-pet
GenericName=Mascota para opencode
Comment=Mascota de escritorio: clic para hablar con opencode
Exec="$HERE/start.sh"
Icon=$HERE/opencode-pet.png
Path=$HOME
Terminal=false
StartupNotify=false
Categories=Utility;
Keywords=opencode;agente;mascota;pet;chat;IA;
EOF
chmod +x "$DESK"
echo "· lanzador: $DESK"

if [ "$AUTOSTART" = 1 ]; then
  mkdir -p "$AUTOSTART_DIR"
  cp "$DESK" "$AUTOSTART_DIR/opencode-pet.desktop"
  echo "· autoinicio: $AUTOSTART_DIR/opencode-pet.desktop"
fi

command -v update-desktop-database >/dev/null 2>&1 && \
  update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
command -v desktop-file-validate >/dev/null 2>&1 && \
  desktop-file-validate "$DESK" || true

cat <<EOF

✓ Listo. Siguiente:

   ./start.sh

  La primera vez crea ~/.config/opencode-pet/config.json y levanta
  un opencode serve propio en 127.0.0.1:4599.

  Voz (opcional, ~450 MB):
     ./voice-setup.sh          # piper: rápida y sin pausas
     ./kokoro-setup.sh         # kokoro: más natural, más lento

  Configuración: clic derecho sobre la mascota → Configuración…
EOF
