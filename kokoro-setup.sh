#!/usr/bin/env bash
# Instala el motor de voz Kokoro del pet (sin root, como voice-setup.sh).
#   ./kokoro-setup.sh
#
# Qué deja en voice/kokoro/:
#   model_quantized.onnx  modelo 82M cuantizado int8 (~92 MB)
#   voices-v1.0.bin       pack de 54 voces (ef_dora = mujer en español)
#   tts.py                sintetizador de una pasada (sin daemon)
#   daemon.py             sintetizador "caliente": carga el modelo una vez y
#                         speak frase a frase; se autoapaga tras idle_exit s
set -u

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "✗ falta '$1'. Instálalo y vuelve a ejecutar."; exit 1; }
}
need python3
need pip3
need curl
need stat

DIR="$(cd "$(dirname "$0")" && pwd)/voice/kokoro"
HF=https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main
GH=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
mkdir -p "$DIR"
cd "$DIR" || exit 1

echo "== 1/4 dependencias python (onnxruntime + tokenizer) =="
pip3 install --user --break-system-packages kokoro-onnx 2>/dev/null || \
  pip3 install --user kokoro-onnx || {
  echo "✗ pip falló"; exit 1; }

echo "== 2/4 modelo (int8, ~92 MB; la red va y viene, si se corta reejecuta) =="
if [ ! -s model_quantized.onnx ] || [ "$(stat -c%s model_quantized.onnx)" -lt 90000000 ]; then
  curl -L -C - --retry 5 --retry-delay 3 -o model_quantized.onnx \
    "$HF/onnx/model_quantized.onnx" || { echo "✗ descarga fallida"; exit 1; }
fi

echo "== 3/4 pack de voces (~28 MB) =="
[ "$(stat -c%s voices-v1.0.bin 2>/dev/null || echo 0)" -ge 28000000 ] || \
  curl -L -C - --retry 5 --retry-delay 3 \
  -o voices-v1.0.bin "$GH/voices-v1.0.bin" || { echo "✗ descarga fallida"; exit 1; }

echo "== 4/4 prueba de síntesis =="
echo "Hola, soy tu gato." | python3 tts.py --voice ef_dora --lang es \
  --output_file test.wav || { echo "✗ síntesis falló"; exit 1; }
[ -s test.wav ] && echo "✓ listo: $(stat -c%s test.wav) bytes generados" \
  || { echo "✗ no se generó audio"; exit 1; }
rm -f test.wav

cat <<'MSG'

Kokoro instalado. En el config del gato (~/.config/opencode-pet/config.json):
    "voice": { "engine": "kokoro", "kokoro_voice": "ef_dora", ... }
Voces: ef_dora (mujer), em_alex / em_santa (hombres). Reinicia el gato.
MSG