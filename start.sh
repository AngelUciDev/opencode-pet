#!/usr/bin/env bash
# Arranca (o reutiliza) un opencode serve propio y abre la mascota.
#
#   ./start.sh              -> usa $PWD como directorio de sesion
#   DIR=/ruta ./start.sh    -> fuerza el directorio
set -euo pipefail

PORT="${PORT:-4599}"
DIR="${DIR:-$PWD}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG_DIR="$HOME/.config/opencode-pet"
CFG="$CFG_DIR/config.json"
LOG2="$CFG_DIR/serve.log"
MODEL_ID="${MODEL_ID:-deepseek-v4.1-flash-free}"
PROVIDER_ID="${PROVIDER_ID:-opencode}"

mkdir -p "$CFG_DIR"

# --- binario de opencode --------------------------------------------------
OC=""
for cand in "$HOME"/.config/ai.opencode.desktop/cli/*/opencode-cli \
            "$HOME/.opencode/bin/opencode" \
            "$(command -v opencode 2>/dev/null || true)"; do
  [ -n "${cand:-}" ] && [ -x "$cand" ] && { OC="$cand"; break; }
done
[ -z "$OC" ] && { echo "✗ no encuentro el binario de opencode"; exit 1; }

# --- ¿responde el server? ------------------------------------------------
alive() { curl -sf -o /dev/null --max-time 3 "http://127.0.0.1:$PORT/" && return 0 || return 1; }

# ¿la contraseña guardada sirve?
# OJO: hace falta -f. Con 'curl -s -o /dev/null' un 401 tambien devuelve
# codigo 0 y la comprobacion miente (que es lo que pasaba).
check_pw() {
  [ -n "${1:-}" ] || return 1
  curl -sf -o /dev/null --max-time 6 -u "opencode:$1" \
    "http://127.0.0.1:$PORT/api/session?limit=1"
}

SAVED_PW=""
[ -f "$CFG" ] && SAVED_PW="$(python3 -c "import json;print(json.load(open('$CFG')).get('password',''))" 2>/dev/null || true)"

# 'opencode serve' genera una contraseña nueva en CADA arranque, asi que la
# guardada puede quedarse obsoleta (la vieja da 401). Si no vale y no hay
# forma de recuperarla, me quedo con el puerto: reinicio el server yo.
start_server() {
  # OJO: todo lo informativo va a stderr. Esta funcion se invoca dentro de
  # $( ) y si imprimiera aqui, el mensaje se pegaria a la contraseña.
  echo "· arranco opencode serve en :$PORT  (log: $LOG2)" >&2
  : > "$LOG2"
  # setsid, no nohup: lo independiza del grupo de sesiones. Con nohup, si el
  # proceso padre muere (timeout, cierre de terminal) el server se va con el,
  # y en el autoinicio eso lo dejaba sin Mascota al iniciar sesion.
  if command -v setsid >/dev/null 2>&1; then
    setsid "$OC" serve --hostname 127.0.0.1 --port "$PORT" >> "$LOG2" 2>&1 &
  else
    nohup "$OC" serve --hostname 127.0.0.1 --port "$PORT" >> "$LOG2" 2>&1 &
  fi
  for _ in $(seq 1 40); do alive && break; sleep 0.5; done
  alive || { echo "✗ el server no arrancó" >&2; tail -5 "$LOG2" >&2; exit 1; }
  grep -hoP 'server password \K\S+' "$LOG2" | tail -1
}

if alive; then
  echo "· server ya escuchando en :$PORT"
  # Un server vivo puede ser de una version ANTIGUA: si opencode se actualizo,
  # los binarios viejos se borran pero el proceso sigue corriendo con el inode
  # en memoria (y con su red/provider ya rota: "getaddrinfo ETIMEOUT opencode.ai",
  # sesiones colgadas en "trabajando" para siempre). Si el binario del server
  # vivo ya no existe en disco, lo reinicio con la version actual.
  SRV_PID="$(pgrep -f "opencode.*serve.*--port $PORT" | head -1 || true)"
  if [ -n "$SRV_PID" ]; then
    SRV_BIN="$(tr '\0' '\n' < "/proc/$SRV_PID/cmdline" | head -1 || true)"
    if [ -n "$SRV_BIN" ] && [ ! -x "$SRV_BIN" ]; then
      echo "· el server corre con binarios ya borrados (opencode se actualizo): lo reinicio"
      pkill -f "opencode.*serve.*--port $PORT" 2>/dev/null || true
      sleep 2
      PW="$(start_server)"
    fi
  fi
  if [ -n "${PW:-}" ]; then
    :   # el server se reinició arriba (versión vieja): PW ya es el nuevo
  elif check_pw "$SAVED_PW"; then
    PW="$SAVED_PW"
    echo "· reutilizo la contraseña guardada"
  else
    PW="$(grep -hoP 'server password \K\S+' "$LOG2" 2>/dev/null | tail -1 || true)"
    if [ -n "$PW" ] && check_pw "$PW"; then
      echo "· contraseña recuperada del log"
    else
      echo "· la contraseña guardada ya no vale; reinicio el server"
      pkill -f "opencode.*serve.*--port $PORT" 2>/dev/null || true
      sleep 2
      PW="$(start_server)"
    fi
  fi
else
  PW="$(start_server)"
fi

[ -n "$PW" ] || { echo "✗ no pude obtener la contraseña del server"; exit 1; }

# --- config ---------------------------------------------------------------
if [ -f "$CFG" ]; then
  python3 - "$CFG" "$PORT" "$PW" <<'PY'
import json, sys
cfg_p, port, pw = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.load(open(cfg_p))
cfg["server"] = f"http://127.0.0.1:{port}"
cfg["password"] = pw
json.dump(cfg, open(cfg_p, "w"), indent=2)
PY
else
  cat > "$CFG" <<EOF
{
  "server": "http://127.0.0.1:$PORT",
  "username": "opencode",
  "password": "$PW",
  "directory": "$DIR",
  "model": { "providerID": "$PROVIDER_ID", "id": "$MODEL_ID" }
}
EOF
fi
chmod 600 "$CFG"

echo "· sesión en: $(python3 -c "import json;print(json.load(open('$CFG'))['directory'])")"
echo "· clic en la mascota = hablar · clic derecho = menú (Configuración / Cerrar)"

# --- solo una mascota a la vez ------------------------------------------
PIDFILE="$CFG_DIR/pet.pid"
if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
    echo "· ya hay una mascota corriendo (pid $OLD)"
    command -v notify-send >/dev/null && \
      notify-send "opencode-pet" "Ya hay una mascota corriendo" 2>/dev/null || true
    exit 0
  fi
  rm -f "$PIDFILE"      # estaba obsoleto
fi

# aviso: NO uso exec, para poder quitar el pidfile al terminar.
python3 "$HERE/pet.py" &
PET_PID=$!
echo "$PET_PID" > "$PIDFILE"
wait "$PET_PID" || true
rm -f "$PIDFILE"
