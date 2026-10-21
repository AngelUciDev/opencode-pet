#!/usr/bin/env bash
# Instala la cadena de voz del pet SIN root:
#   · whisper.cpp (compilado con cmake de pip) + modelo base (multilingüe)
#   · piper (binario standalone, sin Python) + una voz en español
# Todo queda en <repo>/voice/ (ignorado por git).
#
#   ./voice-setup.sh
#   PIPER_VOICE_NAME=es_MX-ald-medium ./voice-setup.sh   # otra voz
#
# La voz por defecto es la misma que usa pet.py: es_MX-claude-high.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V="$HERE/voice"
VOICE_NAME="${PIPER_VOICE_NAME:-es_MX-claude-high}"
LOG="${XDG_STATE_HOME:-$HOME/.local/state}/opencode-pet/voice-setup.log"
mkdir -p "$(dirname "$LOG")" "$V/models" "$V/piper" "$V/voices" "$V/tmp"
# Se ve el progreso y queda copia en el log.
exec > >(tee -a "$LOG") 2>&1
echo "===== $(date) voice-setup ====="
echo "· repo: $HERE"
echo "· voz:  $VOICE_NAME"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "✗ falta '$1'. Instálalo y vuelve a ejecutar."; exit 1; }
}
need python3
need pip3
need git
need curl
need tar
need aplay   # solo para la prueba final; en Debian: alsa-utils
command -v c++ >/dev/null 2>&1 || command -v g++ >/dev/null 2>&1 || \
  command -v clang++ >/dev/null 2>&1 || {
  echo "✗ falta un compilador de C++ (g++ o clang++)."; exit 1; }

echo "== 1) cmake (pip --user) =="
export PATH="$HOME/.local/bin:$PATH"
if ! command -v cmake >/dev/null; then
  pip3 install --user -q --break-system-packages cmake 2>/dev/null || \
  pip3 install --user -q cmake || { echo "✗ pip cmake falló"; exit 1; }
fi
cmake --version | head -1

echo "== 2) whisper.cpp (clone ligero) =="
cd "$V" || exit 1
if [ ! -d whisper.cpp/.git ]; then
  git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git || exit 1
fi
cd whisper.cpp || exit 1
cmake -B build -DCMAKE_BUILD_TYPE=Release \
      -DWHISPER_BUILD_TESTS=OFF -DWHISPER_SDL2=OFF || exit 1
cmake --build build -j"$(nproc 2>/dev/null || echo 2)" --target whisper-cli || exit 1
ls -la build/bin/whisper-cli

echo "== 3) modelo ggml-base.bin (142 MB) =="
cd "$V" || exit 1
[ "$(stat -c%s models/ggml-base.bin 2>/dev/null || echo 0)" -ge 140000000 ] || \
  curl -L -C - --retry 3 -o models/ggml-base.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin || exit 1

echo "== 4) piper standalone (26 MB) =="
PIPER_TGZ="$V/tmp/piper.tar.gz"
if [ -s "$PIPER_TGZ" ] && ! tar -tzf "$PIPER_TGZ" >/dev/null 2>&1; then
  echo "· el tar de piper estaba incompleto, lo descargo otra vez"
  rm -f "$PIPER_TGZ"
fi
[ -s "$PIPER_TGZ" ] || \
  curl -L -C - --retry 3 -o "$PIPER_TGZ" \
  https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz || exit 1
tar -xzf "$PIPER_TGZ" -C "$V/piper" || { rm -f "$PIPER_TGZ"; exit 1; }
rm -f "$PIPER_TGZ"
find "$V/piper" -maxdepth 2 | head -20

echo "== 5) voz $VOICE_NAME (~60 MB) =="
# es_MX-claude-high -> idioma es, locale es_MX, hablante claude, calidad high
LANG_CODE="${VOICE_NAME%%_*}"
LOCALE="${VOICE_NAME%%-*}"
REST="${VOICE_NAME#*-}"
SPEAKER="${REST%-*}"
QUALITY="${REST##*-}"
BASE="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/$LANG_CODE/$LOCALE/$SPEAKER/$QUALITY"
[ "$(stat -c%s "voices/$VOICE_NAME.onnx" 2>/dev/null || echo 0)" -ge 60000000 ] || \
  curl -L -C - --retry 3 -o "voices/$VOICE_NAME.onnx" \
  "$BASE/$VOICE_NAME.onnx" || exit 1
[ -s "voices/$VOICE_NAME.onnx.json" ] || \
  curl -L -C - --retry 3 -o "voices/$VOICE_NAME.onnx.json" \
  "$BASE/$VOICE_NAME.onnx.json" || exit 1

echo "== 6) prueba rápida TTS -> STT =="
echo "Hola mascota, esta es una prueba de voz." | "$V/piper/piper/piper" \
  --model "voices/$VOICE_NAME.onnx" --output_file "$V/tmp/prueba.wav" || exit 1
ls -la "$V/tmp/prueba.wav"
"$V/whisper.cpp/build/bin/whisper-cli" -m models/ggml-base.bin \
  -f "$V/tmp/prueba.wav" -l es -t 2 -nt -np || exit 1

echo
echo "✓ Voz instalada."
echo "  Si eliges una voz distinta a la de por defecto, pon su ruta en"
echo "  ~/.config/opencode-pet/config.json → \"voice\": {\"tts_voice\": \"$V/voices/$VOICE_NAME.onnx\"}"
echo "===== SETUP OK ====="
