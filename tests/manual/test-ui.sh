#!/usr/bin/env bash
# Comprueba: 1) envio -> gato pensando, 2) respuesta -> gato normal y ancha,
# 3) clic fuera -> la burbuja se oculta.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PAT="^python3 .*${ROOT}/pet.py"
OUT="${TMPDIR:-/tmp}/opencode-pet-tests"
mkdir -p "$OUT"
pkill -f "$PAT" 2>/dev/null; sleep 2; rm -f ~/.config/opencode-pet/pet.pid
cd "$ROOT"
./start.sh > /tmp/ui.log 2>&1 &
sleep 18
PID=$(pgrep -f "$PAT" | head -1)
[ -z "$PID" ] && { echo "NO ARANCO"; tail -8 /tmp/ui.log; exit 1; }
CAT=$(for w in $(xdotool search --pid "$PID" 2>/dev/null); do
  s=$(xwininfo -id $w 2>/dev/null | awk '/Width:/{a=$2}/Height:/{b=$2}END{print a"x"b}')
  [ "$s" = "84x84" ] && echo $w; done | head -1)
eval $(xdotool getwindowgeometry --shell "$CAT")
CX=$((X+WIDTH/2)); CY=$((Y+HEIGHT/2))
echo "gato $CX,$CY"

echo "== 1) abro y mando algo =="
xdotool mousemove $CX $CY click 1
sleep 2
xdotool type --delay 45 "dime tres Datos rapidos sobre gatos"
sleep 1
xdotool key Return
sleep 4
B=$(xdotool getwindowfocus 2>/dev/null || true)
import -window "$B" "$OUT/1-pensando.png" 2>/dev/null && echo "  captura PENSANDO ok" || echo "  sin captura"

echo "== 2) espero la respuesta =="
for i in $(seq 1 45); do
  sleep 2
  B=$(xdotool getwindowfocus 2>/dev/null || true)
  [ -n "$B" ] && [ "$B" != "$CAT" ] && xwininfo -id "$B" 2>/dev/null | grep -q IsViewable && break
done
sleep 2
B=$(xdotool getwindowfocus 2>/dev/null || true)
import -window "$B" "$OUT/2-respuesta.png" 2>/dev/null && echo "  captura RESPUESTA ok"
eval $(xdotool getwindowgeometry --shell "$B" 2>/dev/null || echo "WIDTH=0;HEIGHT=0")
echo "  tamano burbuja: ${WIDTH}x${HEIGHT}"

echo "== 3) clic FUERA =="
xdotool mousemove 350 100 click 1
sleep 3
VIS=$(xwininfo -id "$B" 2>/dev/null | grep -c IsViewable)
echo "  visible tras clic fuera: $VIS  (0 = correcto)"
[ "$VIS" = "0" ] && import -window root "$OUT/3-oculta.png" 2>/dev/null

echo "== 4) el raton sigue respondiendo? =="
xdotool getmouselocation | sed 's/^/  /'
pkill -f "$PAT" 2>/dev/null
echo "capturas en: $OUT"
