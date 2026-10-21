#!/usr/bin/env python3
"""Sintetizador Kokoro para el gato (ONNX, solo CPU).

Se usa como plan B del daemon: lee el texto por stdin y escribe un WAV, igual
que el binario de piper. Existe porque la librería kokoro-onnx manda "speed"
como int32 a los modelos con export nuevo (input_ids) y esos lo esperan como
float.

    echo "hola" | ./tts.py --voice ef_dora --output_file out.wav
"""
import argparse
import os
import sys
import wave

import numpy as np
import onnxruntime as rt

MAX_PHONEME_LENGTH = 510   # mismo límite que usa la librería
SAMPLE_RATE = 24000        # kokoro trabaja a 24 kHz


def _threads(pedidos=0):
    """Hilos de onnxruntime: la mitad de los lógicos si hay hyperthreading.

    En el i5-3210M (4 hilos lógicos / 2 núcleos) usar los 4 es más lento que 2.
    """
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        n = os.cpu_count() or 1
    if pedidos and pedidos > 0:
        return max(1, min(pedidos, n))
    return max(1, n // 2) if n >= 4 else n


def _load(model, voices, threads):
    from kokoro_onnx import Kokoro
    so = rt.SessionOptions()
    so.intra_op_num_threads = _threads(threads)
    so.inter_op_num_threads = 1
    sess = rt.InferenceSession(model, sess_options=so,
                               providers=["CPUExecutionProvider"])
    if hasattr(Kokoro, "from_session"):
        return Kokoro.from_session(sess, voices)     # sin cargar el .onnx 2 veces
    k = Kokoro(model, voices)
    k.sess = sess
    return k


def synth(k, text, voice, speed, lang):
    phonemes = k.tokenizer.phonemize(text, lang)
    # la librería trocea los fonemas largos en tandas; aquí es un solo lote
    if len(phonemes) > MAX_PHONEME_LENGTH:
        phonemes = phonemes[:MAX_PHONEME_LENGTH]
    tokens = [[0, *k.tokenizer.tokenize(phonemes), 0]]
    style = k.get_voice_style(voice)
    style = style[len(tokens[0])]
    audio = k.sess.run(None, {
        "input_ids": np.array(tokens, dtype=np.int64),
        "style": np.array(style, dtype=np.float32),
        "speed": np.array([speed], dtype=np.float32),
    })[0]
    return np.asarray(audio, dtype=np.float32)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.path.join(here, "model_quantized.onnx"))
    ap.add_argument("--voices", default=os.path.join(here, "voices-v1.0.bin"))
    ap.add_argument("--voice", default="ef_dora")
    ap.add_argument("--lang", default="es-us")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--output_file", required=True)
    a = ap.parse_args()

    text = sys.stdin.read().strip()
    if not text:
        sys.exit(0)

    k = _load(a.model, a.voices, a.threads)
    audio = synth(k, text, a.voice, a.speed, a.lang)
    if audio.size:
        peak = float(np.abs(audio).max())
        if peak > 1.0:                     # el modelo puede pasarse de ±1
            audio = audio / peak
    with wave.open(a.output_file, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())


if __name__ == "__main__":
    main()
