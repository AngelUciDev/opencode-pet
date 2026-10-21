#!/usr/bin/env bash
# Mini: abrir burbuja, comprobar rects, mover puntero fuera.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PAT="^python3 .*${ROOT}/pet.py"
pkill -f "$PAT" 2>/dev/null; sleep 2; rm -f ~/.config/opencode-pet/pet.pid
cd "$ROOT"
./start.sh > /tmp/mini.log 2>&1 &
sleep 16
PID=$(pgrep -f "$PAT" | head -1)
CAT=$(for w in $(xdotool search --pid "$PID" 2>/dev/null); do
  s=$(xwininfo -id $w 2>/dev/null | awk '/Width:/{a=$2}/Height:/{b=$2}END{print a"x"b}')
  [ "$s" = "84x84" ] && echo $w; done | head -1)
eval $(xdotool getwindowgeometry --shell "$CAT")
CX=$((X+WIDTH/2)); CY=$((Y+HEIGHT/2))
echo "gato $CX,$CY"
xdotool mousemove $CX $CY click 1
sleep 4
B=$(xdotool getwindowfocus 2>/dev/null || true)
echo "burbuja enfocada: $B"
eval $(xdotool getwindowgeometry --shell "$B" 2>/dev/null || echo "X=0;Y=0;WIDTH=0")
echo "burbuja geo: $X,$Y ${WIDTH}x${HEIGHT}"
echo "--- muevo el puntero FUERA ---"
xdotool mousemove 200 90
sleep 3
VIS=$(xwininfo -id "$B" 2>/dev/null | grep -c IsViewable)
echo "visible: $VIS (0=ok)"
echo "--- trazas ---"
grep "dentro" /tmp/mini.log | tail -6 | sed 's/  */ /g'
pkill -f "$PAT" 2>/dev/null
