#!/usr/bin/env python3
"""Sintetizador kokoro "caliente" para el gato.

Carga el modelo UNA vez y se queda escuchando peticiones por stdin (JSON, una
por línea) en vez de arrancar un proceso por frase: así la segunda respuesta
suena al instante en lugar de esperar la carga (~2.5 s).

Petición:  {"text": "...", "voice": "ef_dora", "lang": "es", "speed": 1.0,
            "out": "/ruta/base.wav", "first_max": 36, "max_chunks": 2,
            "threads": 0, "idle_exit": 180}
           {"cmd": "warmup", ...mismos campos...}   # carga + calienta kernels
           {"cmd": "ping"} / {"cmd": "quit"}
Respuesta: una línea JSON por cada trozo de audio ya listo, para que el gato
reproduzca mientras el resto se sintetiza:
            {"chunk": "/ruta/base-0.wav", "duration": 3.2, "synth": 2.4}
            {"done": true, "chunks": 2, "seconds": 6.8}
Si algo falla: {"error": "..."}

Sobre la latencia (medido en un i5-3210M de 2 núcleos):
- onnxruntime con 4 hilos va MÁS LENTO que con 2 (2 núcleos + hyperthreading:
  los hilos se pisan). Por eso el default es la mitad de los lógicos.
- El primer trozo se corta corto a propósito (`first_max`): el tiempo hasta oír
  la primera palabra depende del largo de ESE trozo, no del total. Un texto de
  110 caracteres en un solo trozo tardaba ~15 s en empezar; partido, ~3-4 s.
- La carga del modelo y el primer `run` (creación de kernels/arenas) cuestan
  ~4-5 s: el gato pide "warmup" mientras opencode está pensando y así no se
  notan.

Por seguridad de recursos, si pasan `idle_exit` segundos (180 por defecto) sin
nada que decir, el proceso se cierra solo y la RAM vuelve a su nivel normal.
"""
import json
import os
import re
import sys
import time
import wave

import numpy as np
import onnxruntime as rt
from kokoro_onnx import Kokoro

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_RATE = 24000
MAX_PHONEME_LENGTH = 510

_kokoro = None          # se carga perezoso, en el primer texto
_loaded = None          # (modelo, voces) con los que se cargó _kokoro
_last_use = time.time()
_warmed = False


def _threads(pedidos=0):
    """Hilos de onnxruntime: por defecto la mitad de los lógicos (ver arriba)."""
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        n = os.cpu_count() or 1
    try:
        pedidos = int(pedidos or 0)
    except (TypeError, ValueError):
        pedidos = 0
    if pedidos > 0:
        return max(1, min(pedidos, n))
    return max(1, n // 2) if n >= 4 else n


def _load(model=None, voices=None, threads=0):
    global _kokoro, _loaded
    model = model or os.path.join(HERE, "model_quantized.onnx")
    voices = voices or os.path.join(HERE, "voices-v1.0.bin")
    if _kokoro is not None and _loaded == (model, voices, _threads(threads)):
        return _kokoro
    # el modelo o las voces cambiaron (config): recargar
    so = rt.SessionOptions()
    so.intra_op_num_threads = _threads(threads)
    so.inter_op_num_threads = 1
    sess = rt.InferenceSession(model, sess_options=so,
                               providers=["CPUExecutionProvider"])
    if hasattr(Kokoro, "from_session"):
        # evita cargar el .onnx dos veces (la librería crea su propia sesión)
        _kokoro = Kokoro.from_session(sess, voices)
    else:                                   # kokoro-onnx viejo
        _kokoro = Kokoro(model, voices)
        _kokoro.sess = sess
    _loaded = (model, voices, _threads(threads))
    return _kokoro


def _cut_head(parts, limit):
    """Separa un primer trozo corto del resto.

    El resto se agrupa como siempre (prosodia natural); solo el arranque se
    acorta para que el audio empiece cuanto antes.
    """
    head = parts[0]
    if len(head) <= limit:
        return head, parts[1:]
    # 1) cortar en una coma/punto y coma: pausa natural
    m = re.search(r"[,;:]\s+", head[:limit + 20])
    if m and m.start() >= 12:
        return (head[:m.start()].strip() + ".",
                [head[m.end():].strip()] + parts[1:])
    # 2) sin puntuación: cortar por palabra
    cut = head.rfind(" ", 0, limit + 15)
    if cut < 12:
        cut = limit
    return head[:cut].strip() + ".", [head[cut:].strip()] + parts[1:]


def _split_long(text, limit):
    """Trocea un texto largo por comas o palabras sin pasar de `limit`."""
    if len(text) <= limit:
        return [text]
    out, rest = [], text
    while len(rest) > limit:
        cut = -1
        for m in re.finditer(r"[,;:]\s+", rest[:limit + 15]):
            cut = m.end()
        if cut < limit * 0.4:
            cut = rest.rfind(" ", 0, limit + 10)
        if cut < limit * 0.4:
            cut = limit
        out.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        out.append(rest)
    return out


def _sentences(text, first_max=0, chunk_max=0):
    """Parte el texto en frases, agrupando las cortas para no cortar el ritmo.

    Con `first_max` > 0 la primera frase se acorta (si hace falta) para que el
    primer trozo de audio llegue antes. Con `chunk_max` > 0 ningún trozo pasa de
    ese tamaño: un trozo enorme tarda tanto en sintetizarse que el anterior ya
    terminó de sonar y se oye un silencio largo entre ambos.
    """
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", text) if p.strip()]
    if not parts:
        return []
    head = ""
    if first_max > 0:
        head, parts = _cut_head(parts, int(first_max))
    out, buf = [], ""
    for p in parts:
        buf = f"{buf} {p}".strip() if buf else p
        if len(buf) >= 60 or p.endswith(("!", "?")):
            out.append(buf)
            buf = ""
    if buf:
        if out and len(buf) < 40:        # pega la coletilla a la frase anterior
            out[-1] = f"{out[-1]} {buf}"
        else:
            out.append(buf)
    if chunk_max > 0:
        out = [p for c in out for p in _split_long(c, int(chunk_max))]
    return ([head] if head else []) + out


def _write_wav(path, audio):
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if peak > 1.0:
        audio = audio / peak
    tmp = f"{path}.tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())
    os.replace(tmp, path)          # atómico: el gato nunca lee un wav a medias


def _synth(k, text, voice, lang, speed):
    phonemes = k.tokenizer.phonemize(text, lang)
    if len(phonemes) > MAX_PHONEME_LENGTH:
        phonemes = phonemes[:MAX_PHONEME_LENGTH]
    tokens = [[0, *k.tokenizer.tokenize(phonemes), 0]]
    style = k.get_voice_style(voice)[len(tokens[0])]
    audio = k.sess.run(None, {
        "input_ids": np.array(tokens, dtype=np.int64),
        "style": np.array(style, dtype=np.float32),
        "speed": np.array([speed], dtype=np.float32),
    })[0]
    return np.asarray(audio, dtype=np.float32).ravel()


def _int_req(req, key, default=0):
    try:
        return int(req.get(key) or default)
    except (TypeError, ValueError):
        return default


def _opts(req):
    return {"voice": req.get("voice") or "ef_dora",
            "lang": req.get("lang") or "es",
            "speed": req.get("speed") or 1.0,
            "model": req.get("model"),
            "voices": req.get("voices"),
            "threads": req.get("threads") or 0}


def warm(req):
    """Carga el modelo y ejecuta una síntesis mínima (calienta kernels/arenas)."""
    global _last_use, _warmed
    o = _opts(req)
    t0 = time.time()
    k = _load(o["model"], o["voices"], o["threads"])
    if not _warmed:
        try:
            speed = float(o["speed"])
        except (TypeError, ValueError):
            speed = 1.0
        _synth(k, "Hola.", o["voice"], o["lang"], speed)
        _warmed = True
    _last_use = time.time()
    print(json.dumps({"warm": True, "seconds": round(time.time() - t0, 2)}),
          flush=True)


def handle(req):
    """Sintetiza por frases y avisa cada trozo en cuanto está listo."""
    global _last_use
    text = (req.get("text") or "").strip()
    if not text:
        return
    out = req.get("out") or os.path.join(HERE, "chunk.wav")
    base = out[:-4] if out.endswith(".wav") else out
    o = _opts(req)
    try:
        speed = float(o["speed"])
    except (TypeError, ValueError):
        speed = 1.0
    k = _load(o["model"], o["voices"], o["threads"])

    t0 = time.time()
    first_max = _int_req(req, "first_max")
    chunks = _sentences(text, first_max,
                        _int_req(req, "chunk_max")) or [text]
    max_chunks = _int_req(req, "max_chunks")
    if max_chunks > 0:
        # en un CPU lento conviene leer solo el arranque de la respuesta:
        # el texto completo ya está en la burbuja
        chunks = chunks[:max_chunks]
    made = []
    if req.get("single"):
        # Todo en un solo wav: sin pausas entre frases. En este CPU kokoro
        # sintetiza más lento que tiempo real, así que trocear en streaming
        # deja silencios; de una vez tarda más en EMPEZAR pero suena seguido.
        audio = None
        for phrase in chunks:
            a = _synth(k, phrase, o["voice"], o["lang"], speed)
            if audio is None:
                audio = a
            else:
                audio = np.concatenate(
                    [audio, np.zeros(int(SAMPLE_RATE * 0.08),
                                     dtype=np.float32), a])
        path = f"{base}-0.wav"
        _write_wav(path, audio)
        made.append(path)
        print(json.dumps({"chunk": path,
                          "duration": round(os.path.getsize(path)
                                            / 2 / SAMPLE_RATE, 2),
                          "synth": round(time.time() - t0, 2)}), flush=True)
    else:
        for i, phrase in enumerate(chunks):
            path = f"{base}-{i}.wav"
            t1 = time.time()
            _write_wav(path, _synth(k, phrase, o["voice"], o["lang"], speed))
            made.append(path)
            print(json.dumps({"chunk": path,
                              "duration": round(os.path.getsize(path)
                                                / 2 / SAMPLE_RATE, 2),
                              "synth": round(time.time() - t1, 2)}),
                  flush=True)
    _last_use = time.time()
    print(json.dumps({"done": True, "chunks": len(made),
                      "seconds": round(time.time() - t0, 1)}), flush=True)


def main():
    import select

    idle_exit = 180.0
    stdin = sys.stdin
    while True:
        # espera una línea sin bloquear para poder autoapagarse
        r, _, _ = select.select([stdin], [], [], 1.0)
        if r:
            line = stdin.readline()
            if not line:             # el gato cerró el pipe: nos vamos
                break
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                if req.get("cmd") == "ping":
                    print(json.dumps({"pong": True, "pid": os.getpid(),
                                      "warm": _warmed, "threads": _threads(
                                          req.get("threads") or 0)}),
                          flush=True)
                    continue
                if req.get("cmd") == "quit":
                    break
                idle_exit = float(req.get("idle_exit") or idle_exit)
                if req.get("cmd") == "warmup":
                    warm(req)
                else:
                    handle(req)
            except Exception as exc:                # noqa: BLE001
                print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}),
                      flush=True)
        elif time.time() - _last_use > idle_exit:
            break                     # nadie habla hace rato: libera la RAM


if __name__ == "__main__":
    main()
