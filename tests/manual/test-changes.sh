#!/usr/bin/env bash
# Verifica los cambios:
#   1) un solo fondo semitransparente con borde redondeado
#   2) la burbuja se muestra a un lado del gato (no arriba/abajo) y más ancha
#   3) la respuesta se oculta sola a los ~15 s
#   4) Ctrl+F abre la burbuja con el input
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PAT="^python3 .*${ROOT}/pet.py"
pkill -f "$PAT" 2>/dev/null; sleep 2; rm -f ~/.config/opencode-pet/pet.pid
cd "$ROOT"
setsid ./start.sh > /tmp/hot.log 2>&1 < /dev/null &
sleep 16
PID=$(pgrep -f "$PAT" | head -1)
echo "pet pid: $PID"

bubble_win() {
  for w in $(xdotool search --pid "$PID" 2>/dev/null); do
    geo=$(xwininfo -id "$w" 2>/dev/null | awk '/Width:/{a=$2}/Height:/{b=$2}END{print a"x"b}')
    aw=$(echo "$geo" | cut -dx -f1)
    [ "$aw" -gt 200 ] 2>/dev/null && { echo "$w"; return; }
  done
}
cat_win() {
  for w in $(xdotool search --pid "$PID" 2>/dev/null); do
    geo=$(xwininfo -id "$w" 2>/dev/null | awk '/Width:/{a=$2}/Height:/{b=$2}END{print a"x"b}')
    [ "$geo" = "84x84" ] && { echo "$w"; return; }
  done
}

CAT=$(cat_win)
eval $(xdotool getwindowgeometry --shell "$CAT")
CX=$((X+WIDTH/2)); CY=$((Y+HEIGHT/2))
echo "gato en $X,$Y"
echo "== abro la burbuja con un clic =="
xdotool mousemove $CX $CY click 1
sleep 2
BUB=$(bubble_win)
eval $(xdotool getwindowgeometry --shell "$BUB")
echo "burbuja: $X,$Y ${WIDTH}x${HEIGHT}"

echo "== 1) y 2) posicion lateral y fondo =="
python3 - "$BUB" "$X" "$Y" "$WIDTH" "$HEIGHT" "$CX" "$CY" <<'PY'
import subprocess, sys
bid, X, Y, W, H, CX, CY = (int(a) for a in sys.argv[1:8])
# ¿a un lado? la burbuja debe estar a la izquierda o derecha del gato
a_izq = (X + W) <= CX + 4
a_der = X >= (CX - 4)
# ¿centrada verticalmente respecto al gato?
gc = CY
bc = Y + H//2
vert_ok = abs(gc - bc) <= max(40, H//3)
print(f"  a la izquierda del gato : {'SÍ' if a_izq else 'no'}")
print(f"  a la derecha del gato  : {'SÍ' if a_der else 'no'}")
print(f"  centrada verticalmente  : {'SÍ' if vert_ok else 'no'} (gato y={gc}, burbuja y={bc})")
print(f"  ancho >= 520           : {'SÍ' if W >= 520 else 'no'} ({W})")
# fondo: compara la esquina de la burbuja con el escritorio adyacente
subprocess.run(["import","-window","root","-crop",f"20x20+{X}+{Y}","/tmp/c_bub.png"],check=True)
fy = Y if Y > 40 else Y + H + 20
subprocess.run(["import","-window","root","-crop",f"20x20+{X}+{fy}","/tmp/c_des.png"],check=True)
from PIL import Image
def lum(p):
    im=Image.open(p).convert("RGB"); d=list(im.getdata())
    return sum(0.299*r+0.587*g+0.114*b for r,g,b in d)/len(d)
lb, ld = lum("/tmp/c_bub.png"), lum("/tmp/c_des.png")
print(f"  luminancia esquina burbuja={lb:.1f}  escritorio={ld:.1f}  diff={ld-lb:+.1f}")
print("  (diff > 0 => la esquina esta oscurecida por el fondo semitransparente)")
PY

echo "== 3) auto-ocultado =="
xdotool type --delay 25 "cuanto es 2 mas 2"
xdotool key Return
xdotool mousemove 150 40
echo "  enviado; espero el auto-ocultado (hasta 60 s)..."
OK=0
for i in $(seq 1 30); do
  sleep 2
  VIS=$(xwininfo -id "$BUB" 2>/dev/null | grep -c IsViewable)
  if [ "$VIS" = "0" ]; then OK=1; echo "  se oculto sola tras ~$((i*2)) s"; break; fi
done
[ "$OK" = "0" ] && echo "  NO se oculto sola"

echo "== 4) Ctrl+F =="
BUB2=$(bubble_win)
if [ -z "$BUB2" ]; then echo "  (burbuja oculta, bien)"; else echo "  sigue visible: $BUB2"; fi
xdotool key --clearmodifiers ctrl+f
sleep 2
BUB3=$(bubble_win)
if [ -n "$BUB3" ]; then
  eval $(xdotool getwindowgeometry --shell "$BUB3")
  echo "  visible tras Ctrl+F: $BUB3 (${WIDTH}x${HEIGHT})"
else
  echo "  NO aparecio con Ctrl+F"
fi

echo "== fin =="
pkill -f "$PAT" 2>/dev/null
