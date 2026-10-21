#!/usr/bin/env python3
"""
opencode-pet — mascota de escritorio mínima para opencode.

  · clic en el gato  -> entrada de texto -> se envía a opencode
  · opencode pide permiso -> burbuja con "Sí" / "No"
  · opencode termina    -> burbuja con un trozo de la respuesta

Sin dependencias fuera de lo estándar en tu sistema: GTK3 + Cairo (gi) y urllib.
El dibujo es con primitivas de Cairo: ni una imagen, ni WebKit, ni animaciones
caras. En reposo no hay temporizadores de red ni de redibujado.

Verificado contra opencode 2.0.21 (server en 127.0.0.1:4599).
"""

import base64
import cairo
import json
import math
import os
import queue
import re
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

CFG_PATH = os.path.expanduser("~/.config/opencode-pet/config.json")
LOG_PATH = os.path.join(os.path.dirname(CFG_PATH), "pet.log")
LOG_MAX = 200 * 1024        # con un .old basta para ver qué pasó ayer
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# En un portátil de 2 núcleos, sintetizar no debe quitarle el escritorio.
NICE = ["nice", "-n", "10"] if shutil.which("nice") else []


class _Tee:
    """print() a la terminal (si la hay) y a pet.log, con hora por línea."""

    def __init__(self, *streams):
        self.streams = streams
        self.ini = True

    def write(self, s):
        if not s:
            return
        for st in self.streams:
            try:
                if self.ini and s.strip():
                    st.write(time.strftime("%Y-%m-%d %H:%M:%S "))
                st.write(s)
                st.flush()
            except Exception:
                pass
        self.ini = s.endswith("\n")

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


def setup_log():
    """Desde el menú no hay terminal: deja rastro en ~/.config/opencode-pet."""
    try:
        if os.path.getsize(LOG_PATH) > LOG_MAX:
            os.replace(LOG_PATH, LOG_PATH + ".old")
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        f = open(LOG_PATH, "a", encoding="utf-8", errors="replace")
    except OSError:
        return
    sys.stdout = _Tee(sys.stdout, f)
    sys.stderr = _Tee(sys.stderr, f)
    print(f"--- opencode-pet arranca (pid {os.getpid()}) ---")
DEFAULT_CFG = {
    "server": "http://127.0.0.1:4599",
    "username": "opencode",
    "password": "",
    "directory": os.getcwd(),
    # true = al arrancar retoma la última sesión del pet (si sigue viva)
    "reuse_session": False,
    "model": {"providerID": "opencode", "id": "deepseek-v4.1-flash-free"},
    # agente oculto hecho a medida para la burbuja (respuestas cortas)
    "agent": "pet-mascota",
    # dibujo de la mascota: "terminal" (mini terminal) o "gato"
    "mascot": "terminal",
    # segundos que una respuesta se queda visible antes de ocultarse (0 = nunca)
    "reply_timeout": 15,
    # ocultarse si hay algo a pantalla completa (vídeo, presentación)
    "hide_fullscreen": True,
    # atajo global para abrir el input ("" lo desactiva)
    "hotkey": "ctrl+f",
    # voz (por invocación): micrófono -> whisper.cpp -> opencode -> piper -> altavoz
    "voice": {
        "enabled": True,       # botón 🎤 y atajo Ctrl+Shift+F
        "reply": "voz",        # "voz" (solo si hablaste) | "siempre" | "nunca"
        "max_seconds": 20,     # tope de grabación
        "language": "es",      # idioma para whisper
        # --- motor de voz ---
        # "piper"  = empieza en ~2 s y lee TODO seguido (sin pausas)
        # "kokoro" = más natural, pero en este CPU tarda y hace pausas
        "engine": "piper",
        "kokoro_voice": "ef_dora",   # ef_dora = mujer | em_alex/em_santa = hombre
        "kokoro_lang": "es",        # es | es-419 (latinoamericano)
        "kokoro_speed": 1.0,
        "kokoro_max_sentences": 2,   # frases que lee en voz alta (0 = todas)
        # La 1ª frase se acorta a ~esto caracteres para que el audio empiece
        # antes (el retraso depende del largo del primer trozo, no del total).
        "kokoro_first_chars": 36,
        # Tope de los trozos siguientes (~50 = una frase corta). Evita que el
        # segundo trozo tarde tanto que deje un silencio largo tras el primero.
        "kokoro_chunk_chars": 50,
        # Hilos de onnxruntime: 0 = auto. En CPUs con hyperthreading, usar
        # todos los hilos lógicos es MÁS lento que la mitad (se pisan).
        "kokoro_threads": 0,
        "kokoro_idle_exit": 180,     # s en silencio antes de soltar la RAM
        # true = sintetiza toda la respuesta de una vez y la reproduce seguida
        # (sin pausas entre frases). false = empieza antes, pero con pausas.
        "kokoro_batch": True,
        # --- audio de salida (solo la mascota, no el volumen del sistema) ---
        "audio_gain": 0,        # dB: + sube el volumen, - lo baja
        "audio_bass": 0,        # dB en graves (~120 Hz)
        "audio_treble": 0,      # dB en agudos (~3,5 kHz): + = más nítido
        # Si la respuesta se lee en voz alta, no mostrarla también en la
        # burbuja (solo audio). Los avisos y errores siempre se ven.
        "hide_text": False,
        # --- piper (si engine = "piper") ---
        "tts_voice": "",       # ruta .onnx de piper ("" = es_MX-claude-high)
        # Prosodia (piper/VITS). Más altos = más expresivo pero menos estable.
        "length_scale": 1.0,        # >1 habla más lento y suave
        "noise_scale": 0.8,         # variabilidad tonal (fabrica 0.667)
        "noise_w": 0.95,            # variabilidad de duración (fabrica 0.8)
        "sentence_silence": 0.25,   # pausa entre frases (fabrica 0.2)
    },
}


def load_cfg():
    """Defaults + config del usuario, mezclando también el bloque `voice`.

    La mezcla superficial anterior hacía que un `voice` parcial (por ejemplo
    solo `{"engine": "piper"}`) borrara el resto de defaults y dejara el audio
    sin ajustes. Ahora los defaults rellenan lo que falte.
    """
    cfg = dict(DEFAULT_CFG)
    cfg["voice"] = dict(DEFAULT_CFG["voice"])
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH) as f:
                data = json.load(f)
            voice = data.pop("voice", None)
            cfg.update(data)
            if isinstance(voice, dict):
                cfg["voice"].update(voice)
        except Exception as e:
            print("config ilegible, uso defaults:", e)
    return cfg


CFG = load_cfg()


# --- voz: binarios y modelos instalados por voice-setup.sh -------------------
VOICE_DIR = os.path.join(BASE_DIR, "voice")
WHISPER_BIN = os.path.join(VOICE_DIR, "whisper.cpp", "build", "bin", "whisper-cli")
WHISPER_MODEL = os.path.join(VOICE_DIR, "models", "ggml-base.bin")
PIPER_BIN = os.path.join(VOICE_DIR, "piper", "piper", "piper")
PIPER_VOICE = os.path.join(VOICE_DIR, "voices", "es_MX-claude-high.onnx")
# Para volumen/graves/agudos del audio de la mascota (no toca el sistema).
FFMPEG = shutil.which("ffmpeg")
KOKORO_DIR = os.path.join(VOICE_DIR, "kokoro")
KOKORO_TTS = os.path.join(KOKORO_DIR, "tts.py")
KOKORO_DAEMON = os.path.join(KOKORO_DIR, "daemon.py")
KOKORO_MODEL = os.path.join(KOKORO_DIR, "model_quantized.onnx")
KOKORO_VOICES = os.path.join(KOKORO_DIR, "voices-v1.0.bin")


def voice_cfg():
    v = CFG.get("voice")
    return v if isinstance(v, dict) else {}


def tts_engine():
    """Motor de voz: "kokoro" (natural, más lento) o "piper" (rápido, robótico)."""
    e = str(voice_cfg().get("engine") or "piper").strip().lower()
    return e if e in ("kokoro", "piper") else "piper"


def kokoro_voice():
    """Voz de kokoro: "ef_dora" (mujer), "em_alex"/"em_santa" (hombres)."""
    return str(voice_cfg().get("kokoro_voice") or "ef_dora").strip()


def kokoro_model():
    p = str(voice_cfg().get("kokoro_model") or "").strip()
    return os.path.expanduser(p) if p else KOKORO_MODEL


def kokoro_num(key, default):
    """Número del config de voz, tolerante a strings y a 0 explícito.

    Ojo con `v.get(key) or default`: un 0 deliberado (p. ej. "todas las
    frases") se convertía en el default sin querer.
    """
    v = voice_cfg().get(key, default)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def tts_voice_path():
    """Voz de piper: `voice.tts_voice` del config si existe, si no la default."""
    p = str(voice_cfg().get("tts_voice") or "").strip()
    if p:
        return os.path.expanduser(p)
    return PIPER_VOICE


def _strip_symbols(text):
    """Quita emojis y símbolos: piper/espeak los convierte en sonidos raros
    (un 👋 suelto sonaba como un glitch de ~1 s en medio de la frase)."""
    drop = {"So", "Sk", "Cs"}          # emoji, símbolos, supplementary
    out = []
    for ch in text:
        if unicodedata.category(ch) in drop or ch in "\u200d\ufe0f\ufe0e":
            continue
        out.append(ch)
    return "".join(out)


def _speakable(text):
    """Limpia el texto para el motor de voz: sin emojis ni marcas de énfasis."""
    t = "".join(ch for ch in (text or "") if ch.isprintable())
    t = _strip_symbols(t)
    t = re.sub(r"[*_`#>|~\[\](){}<>]", " ", t)
    t = re.sub(r"\s*[—–]\s*", ". ", t)      # rayas = pausa, no "?!" raro
    t = re.sub(r"\s+", " ", t).strip()
    return t[:600]


def _piper_args():
    """Flags de piper: prosodia ajustable desde el config (más expresivo por
    defecto: los valores de fábrica priorizan estabilidad y suenan monótonos)."""
    v = voice_cfg()
    def num(key, default):
        try:
            return float(v.get(key, default))
        except (TypeError, ValueError):
            return default
    return ["--length_scale", str(num("length_scale", 1.0)),
            "--noise_scale", str(num("noise_scale", 0.8)),
            "--noise_w", str(num("noise_w", 0.95)),
            "--sentence_silence", str(num("sentence_silence", 0.25))]


def audio_fx(cfg=None):
    """Filtros de ffmpeg para volumen, graves y agudos ("" = sin tocar nada).

    Solo afecta al audio de la mascota: no cambia el volumen del sistema ni el
    de otras aplicaciones. El limitador evita que la ganancia sature.
    """
    v = cfg if isinstance(cfg, dict) else voice_cfg()

    def num(key):
        try:
            return float(v.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    gan = max(-20.0, min(20.0, num("audio_gain")))
    baj = max(-15.0, min(15.0, num("audio_bass")))
    agu = max(-15.0, min(15.0, num("audio_treble")))
    if not (gan or baj or agu) or not FFMPEG:
        return ""
    filtros = []
    if gan:
        filtros.append(f"volume={gan:g}dB")
    if baj:
        filtros.append(f"bass=g={baj:g}:f=120:w=0.7")
    if agu:
        filtros.append(f"treble=g={agu:g}:f=3500:w=0.7")
    filtros.append("alimiter=limit=0.95")
    return ",".join(filtros)


def reply_hide_ms():
    """Cuánto se queda una respuesta en pantalla antes de ocultarse sola.

    Configurable con "reply_timeout" (segundos) en config.json. Un valor <= 0
    desactiva el temporizador y la respuesta se queda hasta que la cierres.
    """
    try:
        secs = int(CFG.get("reply_timeout", 15))
    except (TypeError, ValueError):
        secs = 15
    return max(0, secs) * 1000


# ------------------------------------------------- formato de los mensajes

def _truncate(text, limit=900):
    """Recorta sin partir palabras y avisando."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in (". ", "\n", " "):
        p = cut.rfind(sep)
        if p > limit * 0.6:
            return cut[:p] + "…"
    return cut + "…"


def _inline(s):
    """Negritas, cursivas y codigo en linea sobre texto YA escapado."""
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<tt>\1</tt>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", s)
    return s


def render_message(text, limit=900):
    """Markdown -> markup de Pango (seguro: se escapa TODO primero).

    opencode responde con markdown (negritas, listas, codigo) y mostrarlo como
    texto plano deja la burbuja como un bloque gris ilegible. Escapo primero y
    solo despues aplico formato, de modo que ninguna respuesta puede inyectar
    markup ni romper la ventana.
    """
    text = _truncate(text or "", limit)
    esc = GLib.markup_escape_text(text)
    out = []
    in_code = False

    for line in esc.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            out.append("<tt>%s</tt>" % (line or " "))
            continue

        # titulos
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            size = {1: "<big>", 2: "<big>", 3: "", 4: ""}[len(m.group(1))]
            end = "</big>" if size else ""
            out.append("<b>%s%s%s</b>" % (size, m.group(2), end))
            continue

        # listas con guion o asterisco
        m = re.match(r"^(\s*)[-*•]\s+(.*)$", line)
        if m:
            pad = "&#160;" * min(len(m.group(1)), 6)
            out.append("%s•  %s" % (pad, _inline(m.group(2))))
            continue

        # numeradas
        m = re.match(r"^(\s*)(\d+)[.)]\s+(.*)$", line)
        if m:
            pad = "&#160;" * min(len(m.group(1)), 6)
            out.append("%s%s.  %s" % (pad, m.group(2), _inline(m.group(3))))
            continue

        # citas
        if line.lstrip().startswith("&gt;"):
            out.append("<i>%s</i>" % line.lstrip()[4:].strip())
            continue

        out.append(_inline(line) or "&#160;")

    return "\n".join(out)


# ----------------------------------------------------- servidor para el gato
# Endpoints minimos para que el plugin de opencode (o un agente) le hable.
# Escucha SOLO en loopback y exige un token, porque cualquier proceso local
# podria mandar cosas a la burbuja.

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):        # silencio: no queremos ruido
        pass

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        token = _Handler.token
        got = self.headers.get("X-Pet-Token", "")
        return bool(token) and got == token

    def do_GET(self):
        if self.path.rstrip("/") == "/ping":
            return self._reply(200, {"ok": True})
        self._reply(404, {"ok": False})

    def do_POST(self):
        if self.path.rstrip("/") == "/say":
            if not self._authed():
                return self._reply(401, {"ok": False, "error": "token"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._reply(400, {"ok": False, "error": "json"})
            text = str(data.get("text") or "").strip()
            if not text:
                return self._reply(400, {"ok": False, "error": "vacio"})
            self.server.pet.q.put(("say", {
                "text": text[:600],
                "mood": data.get("mood") or "idle",
                "title": data.get("title"),
                "auto_hide": int(data.get("auto_hide") or 0),
            }))
            return self._reply(200, {"ok": True})
        self._reply(404, {"ok": False})


def start_pet_server(pet, port):
    token = CFG.get("token")
    if not token:
        token = base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip("=")
        CFG["token"] = token
        pet._save_cfg()
    _Handler.token = token
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    except OSError:
        return None                      # puerto ocupado: no es grave
    srv.pet = pet

    def run():
        try:
            srv.serve_forever(poll_interval=0.5)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()
    return srv


# ------------------------------------------------------------------- estilo

CSS = b"""
/* la burbuja: el fondo lo pinta el codigo con Cairo (ver _draw_bg), porque el
   CSS no renderiza bien el background-color de la ventana en GTK3 */
window.pet-bubble {
}

window.pet-bubble label.pet-msg {
  color: #ececf1;
  font-size: 13px;
  font-weight: 500;
}

window.pet-bubble entry.pet-input {
  background-color: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 11px;
  color: #f2f2f5;
  font-size: 13px;
  padding: 9px 12px;
  caret-color: #ffb45c;
  transition: border-color 120ms ease;
}

window.pet-bubble entry.pet-input:focus,
window.pet-bubble entry.pet-input:hover {
  background-color: rgba(255, 255, 255, 0.11);
  border-color: #ffb45c;
}

window.pet-bubble button {
  background-color: rgba(255, 255, 255, 0.09);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 11px;
  color: #ececf1;
  font-size: 13px;
  font-weight: 600;
  padding: 9px 14px;
}

window.pet-bubble button:hover {
  background-color: rgba(255, 255, 255, 0.17);
}

window.pet-bubble button:active {
  background-color: rgba(255, 255, 255, 0.07);
}

/* acentos por boton */
window.pet-bubble button.pet-send {
  background-color: #ffb45c;
  border-color: #ffb45c;
  color: #2a1c08;
}
window.pet-bubble button.pet-send:hover {
  background-color: #ffc37c;
  border-color: #ffc37c;
}

window.pet-bubble button.pet-yes {
  background-color: rgba(88, 199, 122, 0.22);
  border-color: rgba(88, 199, 122, 0.55);
  color: #a9efc0;
}
window.pet-bubble button.pet-yes:hover {
  background-color: rgba(88, 199, 122, 0.38);
}

window.pet-bubble button.pet-no {
  background-color: rgba(233, 92, 88, 0.20);
  border-color: rgba(233, 92, 88, 0.52);
  color: #ffb3b1;
}
window.pet-bubble button.pet-no:hover {
  background-color: rgba(233, 92, 88, 0.36);
}

window.pet-bubble button.pet-ok {
  background-color: rgba(255, 255, 255, 0.14);
}

window.pet-bubble button.pet-mic {
  background-color: rgba(137, 180, 250, 0.18);
  border-color: rgba(137, 180, 250, 0.50);
  color: #b8d2ff;
}
window.pet-bubble button.pet-mic:hover {
  background-color: rgba(137, 180, 250, 0.32);
}
"""


def install_css():
    """Aplica el CSS a toda la pantalla (ventana del gato incluida)."""
    prov = Gtk.CssProvider()
    prov.load_from_data(CSS)
    try:
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    except Exception:
        pass


# ------------------------------------------------------------------- cliente

class Api:
    """REST contra el server de opencode. Todo viene envuelto en {"data": ...}."""

    def __init__(self, base, user, password):
        self.base = base.rstrip("/") + "/"
        self.auth = "Basic " + base64.b64encode(
            f"{user}:{password}".encode()).decode()
        self.session_id = None

    def _req(self, method, path, body=None, timeout=20):
        url = urljoin(self.base, path.lstrip("/"))
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", self.auth)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if not raw:
                    return None
                try:
                    return json.loads(raw).get("data")
                except json.JSONDecodeError:
                    return None      # rutas que devuelven HTML, no API
        except urllib.error.HTTPError as e:
            detail = e.read()[:250].decode("utf-8", "replace")
            raise RuntimeError(f"HTTP {e.code} en {path}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"no pude hablar con {url}: {e.reason}") from e

    # -- endpoints que usa la mascota ----------------------------------
    def health(self):
        """POST /api/session devuelve 200 si el server responde."""
        try:
            self._req("POST", "/api/session", {}, timeout=8)
            return True
        except RuntimeError:
            return False

    def list_sessions(self, limit=40):
        return self._req("GET", f"/api/session?limit={limit}", timeout=15) or []

    def create_session(self):
        return self._req("POST", "/api/session", {}, timeout=20)

    def list_agents(self):
        """Agentes que ofrece el server (GET /api/agent). Devuelve una lista."""
        return self._req("GET", "/api/agent", timeout=15) or []

    def list_models(self):
        """Modelos disponibles (GET /api/model). Devuelve una lista."""
        return self._req("GET", "/api/model", timeout=15) or []

    def set_model(self):
        return self._req("POST", f"/api/session/{self.session_id}/model",
                         {"model": CFG["model"]}, timeout=15)

    def set_agent(self):
        """Fija el agente de la sesion (p.ej. la mascota de respuestas cortas).

        Si no hay agente configurado no hago nada: la sesion usa el de por
        defecto. Mejor esfuerzo: si el nombre no existe, opencode acepta igual
        (204) y fallara al prompt, pero eso lo dejo en manos de la config.
        """
        ag = (CFG.get("agent") or "").strip()
        if not ag:
            return None
        return self._req("POST", f"/api/session/{self.session_id}/agent",
                         {"agent": ag}, timeout=15)

    def get_session_by_id(self, sid):
        return self._req("GET", f"/api/session/{sid}", timeout=12)

    def count_messages(self):
        return len(self.messages())

    def prompt(self, text, delivery=None):
        body = {"text": text}
        if delivery:
            body["delivery"] = delivery
        return self._req("POST", f"/api/session/{self.session_id}/prompt",
                         body, timeout=30)

    def get_session(self):
        return self._req("GET", f"/api/session/{self.session_id}", timeout=15)

    def messages(self):
        return self._req("GET", f"/api/session/{self.session_id}/message",
                         timeout=20) or []

    def pending_permissions(self):
        return self._req("GET", f"/api/session/{self.session_id}/permission",
                         timeout=15) or []

    def reply_permission(self, rid, decision):
        return self._req(
            "POST",
            f"/api/session/{self.session_id}/permission/{rid}/reply",
            {"decision": decision}, timeout=20)

    def last_assistant_text(self):
        for m in reversed(self.messages()):
            if isinstance(m, dict) and m.get("error"):
                err = m["error"]
                return "⚠ " + str(err.get("message", err))[:170]
            txt = _text_of(m)
            if txt:
                return txt
        return "terminado."


def _parts(m):
    if not isinstance(m, dict):
        return []
    for key in ("parts", "content"):
        v = m.get(key)
        if isinstance(v, list):
            return v
    return []


def _text_of(m):
    return " ".join(
        (p.get("text") or "").strip()
        for p in _parts(m)
        if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
    ).strip()


def text_since(msgs, seen_ids):
    """Ultimo texto de la respuesta, ignorando lo ya visto.

    IMPORTANTE: /api/session/{id}/message devuelve los mensajes DEL MAS NUEVO
    AL MAS VIEJO, asi que lo nuevo esta al PRINCIPIO de la lista. Recorrerla al
    reves (como hacia antes) devolvia la respuesta vieja y el gato repetia
    siempre el mismo texto.

    Uso ids y no indices porque la lista intercala entradas sinteticas
    ('idle', 'model-switched') que se insertan y desplazan las posiciones.
    """
    seen = seen_ids if isinstance(seen_ids, (set, frozenset)) else set()
    for m in msgs:                      # ya viene de mas nueva a mas antigua
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if mid is not None and mid in seen:
            continue
        if m.get("error"):
            err = m["error"]
            return "⚠ " + str(err.get("message", err))[:170]
        t = _text_of(m)
        if t:
            return t
    return ""


def _hsv_to_rgb(h, s, v):
    """pycairo no trae set_source_hsv en esta version; lo calculo aqui."""
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    return [(v, t, p), (q, v, p), (p, v, t),
            (p, q, v), (t, p, v), (v, p, q)][i % 6]


def _round_rect(cr, x, y, w, h, r):
    """Rectángulo redondeado como subcamino (para rellenar sin cerrar el previo)."""
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -0.5 * math.pi, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 0.5 * math.pi)
    cr.arc(x + r, y + h - r, r, 0.5 * math.pi, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


# ---------------------------------------------------------------------- gato

class Cat:
    """Gato con primitivas Cairo. Cambia de cara segun el estado."""

    name = "gato"

    def __init__(self):
        self.size = 84
        self.eyes = 0.5
        self.blink_at = time.time() + 3.0
        self.mood = "idle"
        self.mood_until = 0.0
        self.t = 0.0

    def set_mood(self, mood, secs=0.0):
        self.mood = mood
        if secs:
            self.mood_until = time.time() + secs

    def tick(self):
        self.t += 0.1
        now = time.time()
        # un mood con caducidad vuelve solo a idle (si no, nunca se aquieta)
        if self.mood != "idle" and now > self.mood_until:
            self.mood = "idle"
        if now > self.blink_at:
            self.eyes = 0.0
            if now > self.blink_at + 0.18:
                self.eyes = 0.5
                self.blink_at = now + 2.4 + (self.t % 1.9)
        else:
            self.eyes = 0.5

    def draw(self, cr):
        cr.set_operator(1)

        # sombra suave bajo la cabeza: da altura sin coste real
        cr.save()
        cr.set_source_rgba(0, 0, 0, 0.16)
        cr.translate(42, 64)
        cr.scale(1.05, 0.16)
        cr.arc(0, 0, 24, 0, 2 * 3.14159265)
        cr.fill()
        cr.restore()

        base = {
            "alarm": (0.99, 0.47, 0.44),
            "happy": (0.58, 0.90, 0.62),
            "typing": (1.00, 0.81, 0.47),
        }.get(self.mood)
        if base is None:
            base = _hsv_to_rgb(0.08, 0.42, 0.98)

        # cola, por detras de la cabeza
        cr.set_source_rgb(base[0] * 0.94, base[1] * 0.94, base[2] * 0.94)
        cr.set_line_width(6)
        cr.set_line_cap(1)                       # CAP_ROUND
        cr.move_to(62, 64)
        cr.curve_to(79, 64, 81, 50, 73, 46)
        cr.stroke()

        # cabeza con degradado vertical (de arriba mas claro)
        grad = cairo.LinearGradient(0, 16, 0, 70)
        grad.add_color_stop_rgb(0, min(base[0] * 1.10, 1.0),
                                min(base[1] * 1.10, 1.0),
                                min(base[2] * 1.10, 1.0))
        grad.add_color_stop_rgb(1, base[0] * 0.88, base[1] * 0.88, base[2] * 0.88)
        cr.set_source(grad)
        cr.move_to(16, 16)
        cr.arc(42, 44, 26, 0, 2 * 3.14159265)
        cr.fill()

        # orejas
        cr.set_source(grad)
        cr.move_to(19, 31); cr.line_to(23, 7); cr.line_to(38, 22); cr.close_path()
        cr.move_to(65, 31); cr.line_to(61, 7); cr.line_to(46, 22); cr.close_path()
        cr.fill()

        # interior de las orejas
        cr.set_source_rgba(0.95, 0.62, 0.62, 0.55)
        cr.move_to(25, 27); cr.line_to(27, 15); cr.line_to(34, 23); cr.close_path()
        cr.move_to(59, 27); cr.line_to(57, 15); cr.line_to(50, 23); cr.close_path()
        cr.fill()

        # rubor
        cr.set_source_rgba(0.98, 0.50, 0.52, 0.15)
        cr.arc(25, 51, 6.0, 0, 2 * 3.14159265)
        cr.arc(59, 51, 6.0, 0, 2 * 3.14159265)
        cr.fill()

        # bigotes: finos y discretos, curvados hacia fuera
        cr.set_source_rgba(0.30, 0.24, 0.20, 0.30)
        cr.set_line_width(1.0)
        cr.set_line_cap(0)                       # CAP_BUTT
        for y0, y1, x1 in ((46, 44, 9), (50, 50, 8), (54, 56, 10)):
            cr.move_to(21, y0); cr.curve_to(16, y0 - 1, 13, y1 - 1, x1, y1)
            cr.move_to(63, y0); cr.curve_to(68, y0 - 1, 71, y1 - 1, 75, y1)
        cr.stroke()

        self._eyes(cr)
        self._mouth(cr)

    def _eyes(self, cr):
        dark = (0.25, 0.20, 0.18)
        if self.mood == "happy":
            cr.set_source_rgb(*dark)
            cr.set_line_width(2.6)
            for cx in (33, 51):
                cr.move_to(cx - 5, 45)
                cr.arc(cx, 45, 5, 3.05, 6.10)
                cr.stroke()
            return
        for cx in (33, 51):
            if self.eyes < 0.25:                       # parpadeo
                cr.set_source_rgb(*dark)
                cr.set_line_width(2.2)
                cr.move_to(cx - 5, 43); cr.line_to(cx + 5, 43); cr.stroke()
                continue
            dy = -2.5 if self.mood == "thinking" else 0
            cr.set_source_rgb(*dark)
            cr.arc(cx, 42 + dy, 5.6, 0, 2 * 3.14159265)
            cr.fill()
            cr.set_source_rgb(1, 1, 1)
            cr.arc(cx + 1.8, 40 + dy, 1.7, 0, 2 * 3.14159265)
            cr.fill()

    def _mouth(self, cr):
        cr.set_source_rgba(0.25, 0.20, 0.18, 0.9)
        cr.set_line_width(2.0)
        if self.mood == "typing":
            cr.arc(42, 56, 2.4, 0, 2 * 3.14159265)
            cr.fill()
        elif self.mood == "happy":
            cr.arc(42, 52, 6, 0.35, 2.79)
            cr.stroke()
        elif self.mood == "thinking":
            cr.move_to(38, 55); cr.line_to(46, 55)
            cr.stroke()
        else:
            cr.arc(42, 51, 5, 0.45, 2.69)
            cr.stroke()


class Terminal:
    """Mini terminal con cara: la alternativa al gato.

    Mismo contrato que Cat (size / tick / set_mood / draw) para que el resto
    del programa no sepa cuál está dibujando. Los estados salen en la pantalla:
    cursor parpadeando (en reposo), "> <" con barra de progreso (trabajando),
    "^ ^" (feliz) y ojos rojos (alerta/permiso). La antena pulsa si hay faena.
    """

    name = "terminal"

    def __init__(self):
        self.size = 84
        self.eyes = 0.5          # 0.5 = cursor visible | 0 = parpadeo
        self.blink_at = time.time() + 2.6
        self.mood = "idle"
        self.mood_until = 0.0
        self.t = 0.0
        self.animating = False

    def set_mood(self, mood, secs=0.0):
        self.mood = mood
        if secs:
            self.mood_until = time.time() + secs
        self.t = 0.0

    def tick(self):
        self.t += 0.1
        now = time.time()
        if self.mood != "idle" and now > self.mood_until:
            self.mood = "idle"
        # la "boca" es un cursor de terminal: parpadea, los ojos no
        if now > self.blink_at:
            self.eyes = 0.0
            if now > self.blink_at + 0.55:
                self.eyes = 0.5
                self.blink_at = now + 2.5 + (self.t % 2.1)
        else:
            self.eyes = 0.5
        # el pulso de la antena solo corre con un estado activo (reposo = 0 CPU)
        self.animating = self.mood != "idle"

    def draw(self, cr):
        cr.set_operator(1)
        base = {
            "alarm": (0.99, 0.47, 0.44),
            "happy": (0.58, 0.90, 0.62),
            "typing": (1.00, 0.81, 0.47),
        }.get(self.mood)
        if base is None:
            base = _hsv_to_rgb(0.08, 0.42, 0.98)

        def shade(f):
            return tuple(max(0.0, min(1.0, v * f)) for v in base)

        # sombra bajo los pies
        cr.save()
        cr.set_source_rgba(0, 0, 0, 0.18)
        cr.translate(42, 79)
        cr.scale(1.2, 0.15)
        cr.arc(0, 0, 24, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

        # flotación de 1 px solo mientras hay actividad
        cr.save()
        if self.mood != "idle":
            cr.translate(0, -1.2 * abs(math.sin(self.t * 1.6)))

        # antena + LED (pulsa si trabaja)
        cr.set_source_rgb(*shade(0.78))
        cr.set_line_width(2.2)
        cr.move_to(42, 27)
        cr.line_to(42, 17)
        cr.stroke()
        led = 0.55
        if self.mood != "idle":
            led = 0.55 + 0.45 * abs(math.sin(self.t * 2.4))
        cr.set_source_rgba(*shade(1.05), led)
        cr.arc(42, 14, 3.6, 0, 2 * math.pi)
        cr.fill()

        # cuerpo
        grad = cairo.LinearGradient(0, 26, 0, 74)
        grad.add_color_stop_rgb(0, *shade(1.06))
        grad.add_color_stop_rgb(1, *shade(0.82))
        cr.set_source(grad)
        _round_rect(cr, 14, 26, 56, 48, 10)
        cr.fill()

        # pantalla
        cr.set_source_rgb(0.10, 0.12, 0.17)
        _round_rect(cr, 20, 32, 44, 30, 5)
        cr.fill()

        eye = (0.93, 0.98, 1.0)
        if self.mood == "happy":
            cr.set_source_rgb(*eye)
            cr.set_line_width(2.6)
            for cx in (33, 51):
                cr.move_to(cx - 4.5, 45)
                cr.arc(cx, 45, 4.5, 3.05, 6.10)
                cr.stroke()
            cr.set_line_width(2.2)
            cr.move_to(38, 52)
            cr.curve_to(40, 55, 44, 55, 46, 52)
            cr.stroke()
        elif self.mood == "alarm":
            cr.set_source_rgb(1.0, 0.42, 0.38)
            for cx in (33, 51):
                _round_rect(cr, cx - 4, 39, 8, 12, 2)
                cr.fill()
            cr.set_line_width(2.4)
            cr.move_to(38, 55)
            cr.line_to(46, 55)
            cr.stroke()
        elif self.mood == "typing":
            cr.set_source_rgb(*eye)
            cr.set_line_width(2.6)
            for cx, d in ((33, 1), (51, -1)):
                cr.move_to(cx - 4 * d, 41)
                cr.line_to(cx + 4 * d, 45)
                cr.line_to(cx - 4 * d, 49)
                cr.stroke()
            cr.set_source_rgba(*eye, 0.35)
            _round_rect(cr, 30, 54, 24, 3, 1.5)
            cr.fill()
            cr.set_source_rgb(*eye)
            _round_rect(cr, 30, 54, 15, 3, 1.5)
            cr.fill()
        else:
            cr.set_source_rgb(*eye)
            for cx in (33, 51):
                _round_rect(cr, cx - 3.5, 40, 7, 9, 2)
                cr.fill()
            if self.eyes >= 0.25:            # cursor de la boca
                cr.set_source_rgba(*eye, 0.85)
                _round_rect(cr, 39, 55, 6, 3, 1)
                cr.fill()

        # pies
        cr.set_source_rgb(*shade(0.72))
        _round_rect(cr, 24, 73, 12, 7, 3)
        cr.fill()
        _round_rect(cr, 48, 73, 12, 7, 3)
        cr.fill()
        cr.restore()


MASCOTS = {"gato": Cat, "terminal": Terminal}


def make_mascot(name=None):
    """Crea la mascota del config ("mascot"). El gato sigue disponible."""
    key = str(name if name is not None else (CFG.get("mascot") or "terminal"))
    return MASCOTS.get(key.strip().lower(), Terminal)()


# ------------------------------------------------------------------ burbuja

class Bubble(Gtk.Window):
    """Panel flotante: etiqueta + entrada, o etiqueta + botones."""

    def __init__(self, pet):
        # TOPLEVEL + hint UTILITY, NO POPUP: en X11 un POPUP es override-redirect
        # y no recibe el foco del teclado (set_accept_focus no basta), que es
        # justamente lo que hacia imposible escribir en el input.
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.pet = pet
        self.on_yes = self.on_no = None
        self._focus_target = None
        self._full_text = ""
        self.editable = False
        self._inside = False        # el puntero estaba dentro (para el cierre)
        self.compact = False        # true = solo spinner de "pensando"
        self._hist = []             # lo ya escrito (se recorre con ↑/↓)
        self._hist_pos = None
        self._spin = 0.0            # fase del reloj de arena (0..1)
        self._t0 = 0.0              # instante de arranque del ciclo
        self._think_timer = None

        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_accept_focus(True)
        self.set_focus_on_map(True)
        self.get_style_context().add_class("pet-bubble")

        # Transparencia real de la ventana. Sin el visual RGBA + app_paintable,
        # X11 pinta un fondo opaco DETRAS de la tarjeta redondeada y por eso se
        # veian las esquinas negras del rectangulo. Con esto las esquinas de
        # fuera del border-radius quedan transparentes y se ve el escritorio.
        self.set_app_paintable(True)
        vis = self.get_screen().get_rgba_visual()
        if vis is not None:
            self.set_visual(vis)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.set_border_width(14)
        outer.pack_start(card, True, True, 0)

        # cabecera: el gato (pensando) + el mensaje, en horizontal
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card.pack_start(head, False, False, 0)

        # el mismo gato del escritorio, en pequeño, para ver si esta pensando
        self.thinking = Gtk.DrawingArea()
        self.thinking.set_size_request(48, 48)
        self.thinking.set_no_show_all(True)   # si no, show_all lo resucita
        self.thinking.connect("draw", self._draw_thinking)
        head.pack_start(self.thinking, False, False, 0)

        self.label = Gtk.Label()
        self.label.set_line_wrap(True)
        self.label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label.set_max_width_chars(56)
        self.label.set_xalign(0.0)
        self.label.set_yalign(0.0)
        self.label.set_selectable(True)
        self.label.get_style_context().add_class("pet-msg")

        # mas ancho y mas bajo: antes crecia hacia abajo, ahora a lo ancho
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_max_content_height(190)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_propagate_natural_width(True)
        self.scroll.set_shadow_type(Gtk.ShadowType.NONE)
        self.scroll.add(self.label)
        head.pack_start(self.scroll, True, True, 0)

        # clic derecho -> copiar el texto entero (lo que se ve esta recortado)
        self.label.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.label.connect("button-press-event", self._on_label_click)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.row = row
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("dile algo a opencode…")
        self.entry.connect("activate", self._on_send)
        self.entry.get_style_context().add_class("pet-input")
        row.pack_start(self.entry, True, True, 0)
        self.mic_btn = Gtk.Button(label="🎤")
        self.mic_btn.set_tooltip_text(
            "Hablar: clic para empezar a grabar, clic para parar "
            "(también Ctrl+Shift+F)")
        self.mic_btn.connect("clicked", lambda _b: self.pet.voice_toggle())
        self.mic_btn.get_style_context().add_class("pet-mic")
        row.pack_start(self.mic_btn, False, False, 0)
        self.send_btn = Gtk.Button(label="Enviar")
        self.send_btn.connect("clicked", self._on_send)
        self.send_btn.get_style_context().add_class("pet-send")
        row.pack_start(self.send_btn, False, False, 0)
        card.pack_start(row, False, False, 0)

        self.btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.yes = Gtk.Button(label="✓ Sí")
        self.no = Gtk.Button(label="✗ No")
        self.yes.get_style_context().add_class("pet-yes")
        self.no.get_style_context().add_class("pet-no")
        self.ok = Gtk.Button(label="Ok")
        self.ok.get_style_context().add_class("pet-ok")
        self.ok.connect("clicked", lambda _w: self.hide_bubble())
        self.yes.connect("clicked", self._on_yes)
        self.no.connect("clicked", self._on_no)
        for b in (self.yes, self.no, self.ok):
            self.btns.pack_start(b, True, True, 0)
        card.pack_start(self.btns, False, False, 0)

        self.add(outer)
        outer.show_all()

        # show_all() resucita todo lo oculto; con no_show_all el control
        # es mio y no vuelve a salir la fila de botones en modo texto.
        self.btns.set_no_show_all(True)
        self.row.set_no_show_all(True)
        self.btns.hide()
        self.row.hide()
        self.connect("key-press-event", self._on_key)
        self.connect("draw", self._draw_bg)

    def _draw_bg(self, _w, cr):
        """Pinta el fondo: negro semitransparente con borde redondeado.

        El CSS no renderiza bien el background-color de la ventana en GTK3 (el
        tema lo pinta por encima y se ve transparente). Asi que lo dibujo yo
        mismo con Cairo: un rectangulo redondeado semitransparente.

        Ojo: Gtk.Widget NO tiene get_width()/get_height() en GTK3; hay que usar
        get_allocated_width()/height(). Con los otros nombres el handler lanzaba
        excepcion en cada dibujo y el fondo nunca se pintaba (se veia
        transparente).
        """
        # En modo "pensando" (compact) NO queremos fondo: solo el spinner
        # flotando. Limpio a transparente con OPERATOR_SOURCE y salgo.
        if getattr(self, "compact", False):
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_OVER)
            return False
        w = max(_w.get_allocated_width(), 1)
        h = max(_w.get_allocated_height(), 1)
        r = 14.0
        cr.set_source_rgba(0.07, 0.07, 0.086, 0.88)
        cr.new_path()
        cr.move_to(r, 0)
        cr.line_to(w - r, 0)
        cr.arc(w - r, r, r, -1.5707963, 0)
        cr.line_to(w, h - r)
        cr.arc(w - r, h - r, r, 0, 1.5707963)
        cr.line_to(r, h)
        cr.arc(r, h - r, r, 1.5707963, 3.14159265)
        cr.line_to(0, r)
        cr.arc(r, r, r, 3.14159265, 4.71238898)
        cr.close_path()
        cr.fill()
        return False

    # -- el reloj de arena de "pensando" --------------------------------
    def start_thinking(self):
        """Muestra un pequeño reloj de arena mientras opencode trabaja.

        Antes era el mismo gato en miniatura dentro de una burbuja con fondo;
        después un aro girando. El usuario pidió un reloj de arena sin fondo:
        la arena cae y, al acabarse, el reloj da media vuelta y reinicia el
        ciclo (el bucle es continuo).
        """
        self._t0 = time.monotonic()
        self.thinking.show()

        def frame():
            if not self.thinking.get_visible():
                return False
            # ciclo completo de ~2.4s, fase siempre en [0, 1)
            self._spin = ((time.monotonic() - self._t0) / 2.4) % 1.0
            self.thinking.queue_draw()
            return True

        self._think_timer = GLib.timeout_add(60, frame)

    def stop_thinking(self):
        self.thinking.hide()
        if getattr(self, "_think_timer", None):
            GLib.source_remove(self._think_timer)
            self._think_timer = None

    def _draw_thinking(self, _w, cr):
        """Escala el dibujo del reloj de arena al tamaño del área."""
        w = max(_w.get_allocated_width(), 1)
        h = max(_w.get_allocated_height(), 1)
        k = min(w, h) / 64.0
        cr.scale(k, k)
        self._draw_hourglass(cr, getattr(self, "_spin", 0.0))
        return False

    def _draw_hourglass(self, cr, phase):
        """Reloj de arena 64x64 con la arena bajando y volteo (vuelta entera)."""
        DISH = 0.8                     # % del ciclo que tarda en vaciarse
        if phase < DISH:
            t = phase / DISH
            rot = 0.0
        else:
            t = 1.0                    # arriba vacío, abajo lleno
            rot = math.pi * (phase - DISH) / (1.0 - DISH)
        cr.save()
        if rot:
            cr.translate(32, 32)
            cr.rotate(rot)
            cr.translate(-32, -32)

        x0, x1 = 16, 48
        yt, ym, yb = 12, 32, 52
        mx = (x0 + x1) / 2
        hw0 = (x1 - x0) / 2            # media anchura arriba y abajo
        nw = 3.0                       # media anchura en el cuello

        def hw_top(y):
            return hw0 + (nw - hw0) * (y - yt) / (ym - yt)

        def hw_bot(y):
            return nw + (hw0 - nw) * (y - ym) / (yb - ym)

        # vidrio: contorno oscuro + trazo blanco (visible en claro y oscuro)
        cr.new_path()
        cr.move_to(x0, yt); cr.line_to(x1, yt); cr.line_to(mx + nw, ym)
        cr.line_to(x1, yb); cr.line_to(x0, yb); cr.line_to(mx - nw, ym)
        cr.close_path()
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_width(5.6)
        cr.set_source_rgba(0.05, 0.05, 0.07, 0.5)
        cr.stroke_preserve()
        cr.set_line_width(3.0)
        cr.set_source_rgba(1, 1, 1, 0.96)
        cr.stroke()

        # arena (dorada): triangulo de arriba vaciandose
        lvl = yt + (ym - yt) * min(1.0, t * 1.15)
        if lvl < ym - 1:
            cr.new_path()
            cr.move_to(mx - hw_top(lvl), lvl)
            cr.line_to(mx - nw, ym); cr.line_to(mx + nw, ym)
            cr.line_to(mx + hw_top(lvl), lvl)
            cr.close_path()
            cr.set_source_rgb(0.94, 0.70, 0.24)
            cr.fill()

        # triangulo de abajo llenandose
        lvl2 = yb - (yb - ym) * min(1.0, max(0.0, (t - 0.05) * 1.05))
        if lvl2 > ym + 1:
            cr.new_path()
            cr.move_to(mx - nw, ym); cr.line_to(mx + nw, ym)
            cr.line_to(mx + hw_bot(lvl2), lvl2)
            cr.line_to(mx - hw_bot(lvl2), lvl2)
            cr.close_path()
            cr.set_source_rgb(0.94, 0.70, 0.24)
            cr.fill()

        # chorro de arena cayendo por el cuello
        if 0.02 < t < 0.98:
            cr.rectangle(mx - 0.9, ym, 1.8, max(0, lvl2 - ym))
            cr.set_source_rgb(0.94, 0.70, 0.24)
            cr.fill()

        cr.restore()
        return False

    # -- acciones -------------------------------------------------------
    def _on_label_click(self, _w, ev):
        if ev.button != 3:
            return False
        menu = Gtk.Menu()
        item = Gtk.MenuItem(label="Copiar mensaje")
        item.connect("activate", lambda *_: self._copy())
        menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(ev)
        return True

    def _copy(self):
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clip.set_text(self._full_text or "", -1)
        clip.store()
        self.show_reply("📋 copiado al portapapeles", editable=False,
                        auto_hide=1600)

    def _on_send(self, _w):
        text = self.entry.get_text().strip()
        if text:
            if not self._hist or self._hist[-1] != text:
                self._hist.append(text)
                del self._hist[:-40]        # 40 mensajes bastan
            self._hist_pos = None
            self.hide_bubble()
            self.pet.send(text)

    def _on_yes(self, _w):
        fn, self.on_yes = self.on_yes, None
        self.hide_bubble()
        if fn:
            fn()

    def _on_no(self, _w):
        fn, self.on_no = self.on_no, None
        self.hide_bubble()
        if fn:
            fn()

    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.hide_bubble()
            return True
        if self.editable and ev.keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            return self._historial(ev.keyval == Gdk.KEY_Up)
        return False

    def _historial(self, atras):
        """↑/↓ recorren los mensajes ya escritos, como en una terminal."""
        if not self._hist:
            return False
        if self._hist_pos is None:
            if not atras:
                return False
            self._hist_pos = len(self._hist)
        paso = -1 if atras else 1
        self._hist_pos = max(0, min(len(self._hist), self._hist_pos + paso))
        self.entry.set_text(self._hist[self._hist_pos]
                            if self._hist_pos < len(self._hist) else "")
        self.entry.set_position(-1)
        return True

    # -- modos ----------------------------------------------------------
    def focus_input(self):
        """Devuelve el foco al campo de texto (usado por el atajo Ctrl+F)."""
        self._focus_later(self.entry)

    def _focus_later(self, widget):
        """Conceder el foco a un widget recien mostrado.

        Antes solo usaba GLib.idle_add y era una carrera: segun cuando
        llegara el idle, la ventana estaba mapeada o no, y el foco se perdia.
        Ahora lo intento por los tres caminos seguros.
        """
        self._focus_target = widget

        def grab():
            w = self._focus_target
            if w is None:
                return False
            self.present()
            w.grab_focus()
            return False

        self.connect("map-event", lambda *_: GLib.idle_add(grab))
        GLib.idle_add(grab)
        GLib.timeout_add(120, grab)      # red de seguridad
        return None

    def show_reply(self, text, editable=True, auto_hide=0):
        self._full_text = text or ""
        self.label.set_markup(render_message(text))
        self.editable = bool(editable)
        self.compact = False
        self.btns.hide()
        self.show_all()
        self.scroll.show()
        self.stop_thinking()      # hay respuesta: ya no esta pensando
        if editable:
            self.row.show()
            self.entry.set_text("")
            self._focus_later(self.entry)
        else:
            self.row.hide()
        self._relocate()

        if auto_hide:
            GLib.timeout_add(auto_hide, self._auto_hide)

    def show_working(self):
        """Solo el reloj de arena animado: sin texto ni fondo.

        Pedido del usuario: mientras opencode piensa se ve un icono de
        procesamiento, no una palabra.
        """
        self.editable = False
        self._full_text = ""
        self.compact = True          # solo el gato: burbuja pequeña
        self.label.set_text("")
        self.show_all()
        self.scroll.hide()
        self.row.hide()
        self.btns.hide()
        self._relocate()
        self.start_thinking()

    def show_recording(self, maxs):
        """Modo grabando: fila visible con mic en ⏹ y el entry deshabilitado."""
        self.editable = False
        self.compact = False
        self.stop_thinking()
        self._full_text = (f"🎤 grabando… (máx {maxs} s)\n"
                           "clic en ⏹ o Ctrl+Shift+F para parar")
        self.label.set_markup(render_message(self._full_text))
        self.show_all()
        self.scroll.show()
        self.btns.hide()
        self.row.show()
        self.entry.set_sensitive(False)
        self.mic_btn.set_label("⏹")
        self._relocate()

    def recording_done(self):
        """Vuelve el mic a 🎤 y el entry a activo tras la grabación."""
        self.entry.set_sensitive(True)
        self.mic_btn.set_label("🎤")

    def show_ask(self, text, on_yes=None, on_no=None):
        self.on_yes, self.on_no = on_yes, on_no
        self.editable = False
        self.compact = False
        self.stop_thinking()
        self._full_text = text or ""
        self.label.set_markup(render_message(text))
        self.show_all()
        self.thinking.hide()
        self.row.hide()
        self.btns.show()
        self.yes.show()
        self.no.show()
        self.ok.hide()
        self._relocate()

        self._focus_later(self.yes)

    def _auto_hide(self):
        """Se llama cuando vence el temporizador de una respuesta.

        No la oculto si el usuario está escribiendo o el ratón está encima: en
        ese caso reintento un poco más tarde, para no quitársela de las manos.
        """
        if not self.get_visible():
            return False
        if self.entry.get_text().strip():
            return False
        pos = self._puntero()
        if pos and self._dentro(*pos):
            GLib.timeout_add(4000, self._auto_hide)
            return False
        self.hide_bubble()
        return False

    def _relocate(self):
        self.show_all()
        _, nat = self.get_preferred_size()
        # mas ancho y mas bajo: antes crecia hacia abajo, ahora a lo ancho
        minw = 64 if getattr(self, "compact", False) else 560
        w, h = max(nat.width, minw), max(nat.height, 60)
        try:
            gx, gy = self.pet.get_position()
            size = self.pet.cat.size
        except Exception:
            gx, gy, size = 60, 60, 84
        screen = self.get_screen()
        try:
            geo = self.get_window().get_display() \
                     .get_primary_monitor().get_geometry()
        except Exception:
            geo = screen.get_monitor_geometry(0)
        # a un lado del gato (no encima/debajo): a la izquierda si cabe,
        # si no a la derecha.
        x = gx - w - 14
        if x < geo.x:
            x = gx + size + 14
        x = max(geo.x, min(x, geo.x + geo.width - w))
        # centrada verticalmente respecto al gato
        y = gy + size // 2 - h // 2
        y = max(geo.y, min(y, geo.y + geo.height - h))
        self.move(int(x), int(y))
        self.resize(w, h)

    def hide_bubble(self):
        self.entry.set_text("")
        self.on_yes = self.on_no = None
        self.stop_thinking()

        self.hide()

    # -- clic fuera ->cerrar ------------------------------------------
    # Probe dos veces: GdkSeat.grab con ventana capturadora, y focus-out-event.
    # En XFCE no sirve ninguno: el grab no entrega eventos al capturador y la
    # burbuja nunca llega a tener foco de toplevel, asi que focus-out no
    # dispara al hacer clic en otra ventana.
    # Lo que si funciona es mirar el puntero: si estaba dentro de la burbuja y
    # sale entre dos muestras seguidas (o sea, de golpe), va a pulsar fuera.
    def _dentro(self, x, y, holgura=8):
        """¿El punto cae dentro de la burbuja o del gato?"""
        for w in (self, self.pet):
            try:
                gx, gy = w.get_position()
                ww, wh = w.get_size()
            except Exception:
                continue
            if gx - holgura <= x <= gx + ww + holgura and \
               gy - holgura <= y <= gy + wh + holgura:
                return True
        return False

    def _puntero(self):
        try:
            seat = self.get_display().get_default_seat()
            pos = seat.get_pointer().get_position()
            return pos.x, pos.y          # (x, y), no un objeto
        except Exception:
            return None

    def vigilar_puntero(self):
        """La llama el tick del gato mientras la burbuja esta visible."""
        if not self.get_visible():
            self._inside = False
            return
        pos = self._puntero()
        if pos is None:
            return
        if self._dentro(*pos):
            self._inside = True
            return
        if self._inside:
            self._inside = False
            # no cierro si hay algo escrito: no quiero perder el texto
            if not self.entry.get_text().strip():
                self.hide_bubble()



# ------------------------------------------------------------------ mascota

class Pet(Gtk.Window):
    """Ventana del gato. UI en hilo principal, red en un hilo worker."""

    TICK_MS = 120

    def __init__(self):
        # TOPLEVEL + UTILITY (no POPUP) para que acepte teclado y sea arrastrable.
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.cat = make_mascot()
        self.api = Api(CFG["server"], CFG["username"], CFG["password"])
        self.status = "arrancando…"
        self.connected = False
        self.waiting = False
        self.asked = set()
        self.q = queue.Queue()
        self._press = None      # (x_root, y_root) al pulsar
        self._origin = None     # esquina de la ventana al pulsar
        self._dragged = False
        self._seen_ids = set()  # ids de mensajes ya mostrados
        self._pending_external = []   # mensajes venidos del plugin
        # voz: grabación en curso y turno que debe responder hablado
        self._rec = None            # subprocess de arecord
        self._rec_file = None       # wav temporal de la grabación
        self._speak_next = False    # la respuesta de este turno sale en audio
        self._speaking = False      # piper/aplay ya están sonando
        self._player = None         # aplay/ffplay en curso (para el silencio)
        self._tts_proc = None       # daemon de kokoro (carga el modelo 1 vez)
        self._tts_buf = b""         # bytes ya leídos y aún sin procesar
        self._tts_lock = threading.Lock()   # serializa warmup y síntesis
        self._tts_warming = False   # ya hay un calentamiento en marcha
        self._last_tts = 0.0        # última vez que la voz estuvo en uso
        self._last_reply = ""       # para "copiar última respuesta"
        self._reconnecting = False  # reintento de conexión en marcha
        self._fs_ticks = 0          # contador para mirar pantalla completa
        self._oculta_fs = False     # oculta porque hay vídeo a pantalla completa

        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_app_paintable(True)
        self.set_size_request(self.cat.size, self.cat.size)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_visual(self.get_screen().get_rgba_visual())
        self.move(*self._saved_pos())   # abajo a la derecha por defecto

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                        | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_press)
        self.connect("button-release-event", self.on_release)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("destroy", lambda *_: (self._stop_tts(), Gtk.main_quit()))

        self.bubble = Bubble(self)
        self._settings = None

        self._sent_at = 0
        GLib.timeout_add(self.TICK_MS, self.on_tick)
        GLib.timeout_add(1500, self.on_poll)
        threading.Thread(target=self.worker, daemon=True).start()

    # --- posicion persistente -------------------------------------------
    def _default_pos(self):
        """Abajo a la derecha del monitor principal, sin pisar el panel."""
        size = self.cat.size
        geo = None
        try:
            # ojo: la geometria la da el Monitor, no el Display
            geo = self.get_display().get_primary_monitor().get_geometry()
        except Exception:
            geo = None
        if geo is None:
            try:
                geo = self.get_screen().get_monitor_geometry(0)
            except Exception:
                return 80, 80
        return (int(geo.x + geo.width - size - 20),
                int(geo.y + geo.height - size - 52))   # 52 = panel de abajo

    def _saved_pos(self):
        try:
            p = CFG.get("position")
            if isinstance(p, list) and len(p) == 2:
                return int(p[0]), int(p[1])
        except Exception:
            pass
        return self._default_pos()

    def _save_cfg(self):
        """Un solo sitio para persistir la config (antes habia tres copias)."""
        try:
            os.makedirs(os.path.dirname(CFG_PATH), exist_ok=True)
            with open(CFG_PATH, "w") as f:
                json.dump(CFG, f, indent=2)
            os.chmod(CFG_PATH, 0o600)
        except Exception:
            pass

    def _save_pos(self):
        try:
            x, y = self.get_position()
            CFG["position"] = [x, y]
            self._save_cfg()
        except Exception:
            pass

    # --- UI ------------------------------------------------------------
    def on_tick(self):
        before = self.cat.eyes
        self.cat.tick()
        # solo redibujo si hay algo que cambiar: parpadeo o mood activo.
        # en reposo puro no hay ni un pixel que repintar -> CPU ~0.
        animating = (before != self.cat.eyes
                     or self.cat.eyes != 0.5
                     or self.cat.mood != "idle"
                     or time.time() < self.cat.mood_until
                     or getattr(self.cat, "animating", False))
        if animating:
            self.queue_draw()
        self._fs_ticks += 1
        if self._fs_ticks >= 8:          # ~1 vez por segundo basta
            self._fs_ticks = 0
            self._mirar_pantalla_completa()
        self.bubble.vigilar_puntero()
        return True

    def _mirar_pantalla_completa(self):
        """En vídeo o presentación a pantalla completa, la mascota estorba."""
        if not CFG.get("hide_fullscreen", True):
            return
        try:
            win = Gdk.Screen.get_default().get_active_window()
            lleno = bool(win and (win.get_state() & Gdk.WindowState.FULLSCREEN))
        except Exception:
            return
        if lleno == self._oculta_fs:
            return
        self._oculta_fs = lleno
        if lleno:
            self.hide()
            if not self.bubble.editable:    # no tirar lo que estés escribiendo
                self.bubble.hide()
        else:
            self.show()

    def on_draw(self, _w, cr):
        self.cat.draw(cr)
        return False

    def on_press(self, _w, ev):
        if ev.button == 3:
            self.popup_menu(ev)
            return True
        if ev.button != 1:
            return False
        self._press = (ev.x_root, ev.y_root)
        self._dragged = False
        try:
            self._origin = self.get_position()
        except Exception:
            self._origin = (80, 80)
        return True

    def on_motion(self, _w, ev):
        """Arrastre manual: a partir de 4 px de recorrido, mueve la ventana."""
        if not self._press:
            return False
        dx = ev.x_root - self._press[0]
        dy = ev.y_root - self._press[1]
        if not self._dragged and (abs(dx) > 4 or abs(dy) > 4):
            self._dragged = True
        if self._dragged:
            self.move(int(self._origin[0] + dx), int(self._origin[1] + dy))
        return True

    def on_release(self, _w, ev):
        if ev.button != 1:
            return False
        was_drag = self._dragged
        self._press = None
        self._dragged = False
        if was_drag:
            self._save_pos()
            return True
        self.toggle_bubble()          # clic limpio -> abrir/cerrar
        return True

    # --- menú del clic derecho -----------------------------------------
    def _build_menu(self):
        """Menú del clic derecho: configuración, portapapeles y sesión."""
        menu = Gtk.Menu()
        item_cfg = Gtk.MenuItem(label="Configuración…")
        item_cfg.connect("activate", lambda *_: self.open_settings())
        menu.append(item_cfg)
        menu.append(Gtk.SeparatorMenuItem())

        item_copy = Gtk.MenuItem(label="Copiar última respuesta")
        item_copy.set_sensitive(bool(self._last_reply))
        item_copy.connect("activate", lambda *_: self._copy_last())
        menu.append(item_copy)

        item_mute = Gtk.CheckMenuItem(label="Silencio (no hablar)")
        item_mute.set_active(bool(voice_cfg().get("muted")))
        item_mute.connect("toggled", self._on_mute)
        menu.append(item_mute)

        item_new = Gtk.MenuItem(label="Nueva sesión")
        item_new.connect("activate", lambda *_: self._new_session())
        menu.append(item_new)

        menu.append(Gtk.SeparatorMenuItem())
        item_quit = Gtk.MenuItem(label="Cerrar")
        item_quit.connect("activate", lambda *_: Gtk.main_quit())
        menu.append(item_quit)
        return menu

    def _copy_last(self):
        """La burbuja se oculta sola; desde el menú se copia cuando sea."""
        if not self._last_reply:
            return
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clip.set_text(self._last_reply, -1)
        clip.store()
        self.bubble.show_reply("📋 copiado", editable=False, auto_hide=1400)

    def _on_mute(self, item):
        v = dict(voice_cfg())
        v["muted"] = bool(item.get_active())
        CFG["voice"] = v
        self._save_cfg()
        if v["muted"]:                  # corta lo que esté sonando ahora
            p = self._player
            self._player = None
            if p is not None and p.poll() is None:
                try:
                    p.kill()
                except OSError:
                    pass
        print("voz:", "en silencio" if v["muted"] else "activa")

    def _new_session(self):
        """Sesión nueva sin reiniciar: contexto corto = menos CPU y latencia."""
        self.q.put(("new_session", None))

    def popup_menu(self, ev):
        """Clic derecho: muestra el menú junto al puntero."""
        menu = self._build_menu()
        menu.show_all()
        menu.popup_at_pointer(ev)

    def open_settings(self):
        """Abre (o trae al frente) la ventana de configuración."""
        win = getattr(self, "_settings", None)
        if win is not None:
            win.present()
            return
        self._settings = PetSettings(self)
        self._settings.connect("destroy",
                               lambda *_: setattr(self, "_settings", None))

    def apply_settings(self):
        """Después de editar la config, reaplica agente/modelo a la sesión."""
        self._apply_mascot()
        self.q.put(("apply", None))

    def _apply_mascot(self):
        """Cambia el dibujo sin reiniciar (todas las mascotas miden lo mismo)."""
        name = str(CFG.get("mascot") or "terminal").strip().lower()
        if getattr(self.cat, "name", "") == name:
            return
        self.cat = make_mascot(name)
        self.set_size_request(self.cat.size, self.cat.size)
        self.resize(self.cat.size, self.cat.size)
        self.queue_draw()

    def toggle_bubble(self):
        """Clic en el gato.

        Antes esto era un toggle a lo bruto y era una trampa: al terminar un
        turno la burbuja sigue abierta enseñando la respuesta, asi que el
        siguiente clic la CERRABA y lo que escribieras se perdia. Ahora:
          · si ya estas escribiendo -> el clic cierra (lo que se pidió)
          · si solo se ve el texto  -> el clic pasa a escritura
        """
        if self.bubble.get_visible():
            if self.bubble.editable:
                self.bubble.hide_bubble()
            else:
                self.bubble.show_reply("¿qué le digo?", editable=True)
            return
        if not self.connected:
            self.bubble.show_reply(self.status, editable=False)
        elif self.waiting:
            self.bubble.show_reply("⏳ opencode está trabajando…", editable=False)
        else:
            self.bubble.show_reply("¿qué le digo?", editable=True)

    def open_input(self):
        """Atajo Ctrl+F: deja la burbuja lista para escribir.

        Lleva al mismo sitio que un clic limpio en el gato cuando no estas
        escribiendo, para que atajo y clic se comporten igual. Si la burbuja ya
        esta en modo escritura, solo le devuelvo el foco.
        """
        if self.bubble.get_visible() and self.bubble.editable:
            self.bubble.focus_input()
            return False
        if not self.connected:
            self.bubble.show_reply(self.status, editable=False)
        elif self.waiting:
            self.bubble.show_reply("⏳ opencode está trabajando…", editable=False)
        else:
            self.bubble.show_reply("¿qué le digo?", editable=True)
        return False

    def send(self, text):
        self._speak_next = False    # escrito a mano: respuesta en texto
        self.q.put(("send", text))

    # --- voz: micrófono (whisper.cpp) y respuesta hablada (piper) ----------
    # Todo por invocación: arecord/whisper-cli/piper son procesos que nacen y
    # mueren; en reposo el gato sigue costando 0 CPU y los 66 MB de siempre.
    def voice_installed(self):
        if tts_engine() == "kokoro":
            return (os.path.exists(WHISPER_BIN) and os.path.exists(WHISPER_MODEL)
                    and os.path.exists(KOKORO_TTS)
                    and os.path.exists(kokoro_model())
                    and os.path.exists(KOKORO_VOICES))
        v = tts_voice_path()
        return (os.path.exists(WHISPER_BIN) and os.path.exists(WHISPER_MODEL)
                and os.path.exists(PIPER_BIN)
                and os.path.exists(v) and os.path.exists(v + ".json"))

    def voice_ok(self):
        return bool(voice_cfg().get("enabled", True)) and self.voice_installed()

    def voice_toggle(self):
        """Botón 🎤 / atajo Ctrl+Shift+F: empieza o para la grabación."""
        if not voice_cfg().get("enabled", True):
            GLib.idle_add(self.bubble.show_reply,
                          "🎤 voz desactivada (clic derecho → Configuración)",
                          False, 5000)
            return
        if not self.voice_installed():
            GLib.idle_add(self.bubble.show_reply,
                          "🎤 falta instalar la voz: ejecuta "
                          f"{os.path.join(BASE_DIR, 'voice-setup.sh')}",
                          False, 7000)
            return
        if self._rec is not None:
            self.q.put(("voice_stop", None))
            return
        if not self.connected:
            GLib.idle_add(self.bubble.show_reply, self.status, False, 4000)
            return
        if self.waiting:
            GLib.idle_add(self.bubble.show_reply,
                          "⏳ opencode está trabajando…", False, 4000)
            return
        self.q.put(("voice_start", None))

    def _do_voice_start(self, _):
        maxs = max(3, int(voice_cfg().get("max_seconds", 20)))
        path = os.path.join(VOICE_DIR, "tmp", "pet-voice.wav")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cmd = ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1",
               "-t", "wav", "-d", str(maxs), path]
        try:
            self._rec = subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
        except Exception as exc:
            GLib.idle_add(self._ui_error, f"micro: {exc}")
            return
        self._rec_file = path
        GLib.idle_add(self._ui_recording, maxs)

    def _do_voice_stop(self, _):
        rec, self._rec = self._rec, None
        path, self._rec_file = self._rec_file, None
        if rec is not None and rec.poll() is None:
            try:
                rec.send_signal(signal.SIGINT)   # arecord finaliza el wav limpio
                rec.wait(timeout=5)
            except Exception:
                try:
                    rec.kill()
                except Exception:
                    pass
        GLib.idle_add(self._ui_recording_done)
        # menos de ~0.25 s a 16 kHz = no se oyó nada
        if not path or not os.path.exists(path) \
                or os.path.getsize(path) < 8000:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            GLib.idle_add(self.bubble.show_reply, "🎤 no grabé nada",
                          False, 3000)
            return
        self.q.put(("voice_stt", path))

    def _do_voice_stt(self, path):
        txt = ""
        try:
            out = subprocess.run(
                NICE + [WHISPER_BIN, "-m", WHISPER_MODEL, "-f", path,
                 "-l", str(voice_cfg().get("language", "es")),
                 "-t", "2", "-nt", "-np"],
                capture_output=True, text=True, timeout=240)
            txt = (out.stdout or "").strip()
        except Exception as exc:
            print("voice stt:", exc)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        if not txt:
            GLib.idle_add(self.bubble.show_reply, "🎤 no te entendí",
                          False, 4000)
            return
        print(f"voice stt: {txt!r}")
        self._speak_next = str(voice_cfg().get("reply", "voz")) != "nunca"
        self._do_send(txt)          # mismo camino que un mensaje escrito

    def _play(self, wav, fx=None):
        """Reproduce un wav y lo borra (aplay, o ffplay si falla).

        Con `fx` (filtros de ffmpeg) aplica volumen/graves/agudos antes de
        sonar. `fx=None` usa la config; `fx=""` no procesa nada.
        """
        filtros = audio_fx() if fx is None else fx
        fuente = wav
        if filtros:
            salida = (wav[:-4] if wav.endswith(".wav") else wav) + ".fx.wav"
            try:
                try:
                    os.remove(salida)
                except OSError:
                    pass
                r = subprocess.run(
                    [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                     "-i", wav, "-af", filtros, salida],
                    timeout=60, stderr=subprocess.DEVNULL)
                if (r.returncode == 0 and os.path.exists(salida)
                        and os.path.getsize(salida) > 44):
                    fuente = salida
                else:
                    try:
                        os.remove(salida)
                    except OSError:
                        pass
            except Exception as exc:
                print("audio fx:", exc)
        proc = None
        try:
            proc = subprocess.Popen(["aplay", "-q", fuente],
                                    stderr=subprocess.DEVNULL)
            self._player = proc          # para poder callarlo desde el menú
            rc = proc.wait(timeout=300)
            if rc != 0 and self._player is not None:   # no lo mataron a mano
                proc = subprocess.Popen(["ffplay", "-nodisp", "-autoexit",
                                         "-loglevel", "quiet", fuente],
                                        stderr=subprocess.DEVNULL)
                self._player = proc
                proc.wait(timeout=300)
        except Exception:
            pass
        finally:
            if proc is not None and proc.poll() is None:
                proc.kill()
            if self._player is proc:
                self._player = None
            for f in ({wav, fuente} - {""}):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def _speak_piper(self, clean):
        wav = os.path.join(VOICE_DIR, "tmp", "reply.wav")
        subprocess.run(NICE + [PIPER_BIN, "--model", tts_voice_path()]
                       + _piper_args()
                       + ["--output_file", wav],
                       input=clean + "\n", text=True,
                       capture_output=True, timeout=120)
        if os.path.exists(wav):
            self._play(wav)

    def _tts_daemon(self):
        """Sintetizador kokoro vivo: carga el modelo una vez y se autoapaga
        tras `kokoro_idle_exit` segundos en silencio (vuelve la RAM a su nivel)."""
        proc = self._tts_proc
        if proc is not None:
            if proc.poll() is None:
                return proc
            try:                       # terminó por idle-exit: recogerlo
                proc.wait(timeout=1)
            except Exception:
                pass
            self._tts_proc = None
        try:
            proc = subprocess.Popen(NICE + [sys.executable, KOKORO_DAEMON],
                                    stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, bufsize=0)
        except OSError as exc:
            print("tts daemon:", exc)
            return None
        self._tts_proc = proc
        self._tts_buf = b""             # nada que leer todavía
        return proc

    def _tts_read(self, proc, timeout=90):
        """Lee una línea del daemon con tope: si se cuelga, no congela al gato.

        El pipe se lee en binario y se trocea aquí a propósito: con `select`
        sobre un pipe en modo texto, el buffer interno del TextIOWrapper puede
        quedarse con líneas ya recibidas (el daemon manda `chunk` y `done` de
        golpe) y `select` no vuelve a avisar hasta agotar el tope.
        """
        data = self._tts_buf
        while b"\n" not in data:
            try:
                listos, _, _ = select.select([proc.stdout], [], [], timeout)
            except (OSError, ValueError):
                return ""
            if not listos:
                return ""
            try:
                trozo = os.read(proc.stdout.fileno(), 4096)
            except (OSError, ValueError):
                return ""
            if not trozo:               # EOF: el daemon murió
                self._tts_buf = b""
                return ""
            data += trozo
        linea, _, resto = data.partition(b"\n")
        self._tts_buf = resto
        return linea.decode("utf-8", "replace").strip()

    def _tts_kill(self, proc):
        """Mata al daemon si dejó de responder y limpia la referencia."""
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass
        if self._tts_proc is proc:
            self._tts_proc = None
            self._tts_buf = b""

    def _tts_req(self):
        """Campos comunes de toda petición al daemon de kokoro."""
        idle = kokoro_num("kokoro_idle_exit", 180)
        # Si venimos de hablar hace poco, mantener el modelo cargado ahorra
        # ~2,5 s en la siguiente respuesta (238 MiB, el 3 % de esta RAM).
        if idle > 0 and time.time() - self._last_tts < 300:
            idle = max(idle, 600)
        return {"voice": kokoro_voice(),
                "lang": str(voice_cfg().get("kokoro_lang") or "es"),
                "speed": voice_cfg().get("kokoro_speed") or 1.0,
                "model": kokoro_model(),
                "voices": KOKORO_VOICES,
                "threads": kokoro_num("kokoro_threads", 0),
                "idle_exit": idle}

    def _warm_tts(self):
        """Deja el sintetizador caliente MIENTRAS opencode piensa.

        Cargar el modelo y el primer `run` de onnxruntime cuestan ~4-5 s en este
        CPU. Si se hacen al llegar la respuesta, ese tiempo se suma al retraso
        del audio; haciéndolos al enviar el mensaje, quedan escondidos en la
        espera del turno (que dura más). Es un hilo aparte: el worker sigue
        sondeando permisos y estado.
        """
        if tts_engine() != "kokoro" or not self.voice_ok():
            return
        if self._tts_warming:
            return                      # el que ya corre nos vale
        self._tts_warming = True

        def run():
            try:
                with self._tts_lock:
                    proc = self._tts_daemon()
                    if proc is None:
                        return
                    try:
                        req = dict(self._tts_req(), cmd="warmup")
                        proc.stdin.write((json.dumps(req) + "\n").encode())
                        proc.stdin.flush()
                        while True:
                            line = self._tts_read(proc, 60)
                            if not line:        # murió, se colgó o idle-exit
                                self._tts_kill(proc)
                                return
                            try:
                                msg = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if msg.get("error"):
                                print("tts warmup:", msg["error"])
                                return
                            if msg.get("warm"):
                                self._last_tts = time.time()
                                return
                    except (OSError, ValueError):
                        self._tts_proc = None
            finally:
                self._tts_warming = False

        threading.Thread(target=run, daemon=True).start()

    def _speak_kokoro(self, clean):
        """Kokoro frase por frase: el gato empieza a hablar en cuanto sale la
        primera, sin esperar a que se sintetice todo."""
        with self._tts_lock:
            proc = self._tts_daemon()
            if proc is None:
                return self._speak_kokoro_once(clean)
            base = os.path.join(VOICE_DIR, "tmp", "kokoro-%d" % os.getpid())
            req = dict(self._tts_req(), out=base + ".wav")
            # Sin pausas: en este CPU kokoro tarda MÁS que el audio, así que
            # trocear en streaming deja silencios entre trozos. De una vez
            # tarda más en empezar pero se oye seguido. "kokoro_batch": false
            # recupera el modo troceado (empieza antes, con pausas).
            lote = bool(voice_cfg().get("kokoro_batch", True))
            req.update({
                "text": clean[:400],
                # el 1er trozo, corto: el audio arranca antes (solo si no lote)
                "first_max": 0 if lote else kokoro_num("kokoro_first_chars",
                                                       36),
                # los siguientes tampoco enormes: si no, el anterior acaba de
                # sonar y aún queda medio minuto de síntesis (silencio largo)
                "chunk_max": 0 if lote else kokoro_num("kokoro_chunk_chars",
                                                       50),
                "max_chunks": kokoro_num("kokoro_max_sentences", 2),
                "single": lote,
            })
            try:
                proc.stdin.write((json.dumps(req) + "\n").encode())
                proc.stdin.flush()
            except (OSError, ValueError):
                self._tts_proc = None       # daemon muerto: tiro de una sola vez
                return self._speak_kokoro_once(clean)
            while True:
                line = self._tts_read(proc)
                if not line:                    # murió, se colgó o idle-exit
                    self._tts_kill(proc)
                    return self._speak_kokoro_once(clean)
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("error"):
                    print("tts:", msg["error"])
                    return
                if msg.get("chunk"):
                    if voice_cfg().get("muted"):     # silencio a mitad: no suena
                        try:
                            os.remove(msg["chunk"])
                        except OSError:
                            pass
                    else:
                        self._play(msg["chunk"])   # suena ya, lo demás sigue
                if msg.get("done"):
                    return

    def _speak_kokoro_once(self, clean):
        """Sin daemon: una llamada y listo (arranque lento, pero funciona)."""
        wav = os.path.join(VOICE_DIR, "tmp", "reply.wav")
        try:
            subprocess.run(
                NICE + [sys.executable, KOKORO_TTS, "--model", kokoro_model(),
                 "--voices", KOKORO_VOICES, "--voice", kokoro_voice(),
                 "--lang", str(voice_cfg().get("kokoro_lang") or "es"),
                 "--speed", str(voice_cfg().get("kokoro_speed") or 1.0),
                 "--threads", str(kokoro_num("kokoro_threads", 0)),
                 "--output_file", wav],
                input=clean[:400] + "\n", text=True,
                capture_output=True, timeout=300)
            if os.path.exists(wav):
                self._play(wav)
        except Exception as exc:
            print("tts:", exc)

    def _stop_tts(self):
        """Cierra el sintetizador (al salir del gato)."""
        proc = self._tts_proc
        self._tts_proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.stdin.write((json.dumps({"cmd": "quit"}) + "\n").encode())
            proc.stdin.flush()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    def _speak(self, text):
        """Lee la respuesta en voz alta (hilo aparte para no congelar el gato).

        Devuelve True si el audio va a sonar de verdad; el llamador usa eso para
        decidir si puede ocultar el texto (nunca ocultar algo que no se oye).
        """
        if self._speaking or not self.voice_installed():
            return False
        if voice_cfg().get("muted"):       # silencio rápido desde el menú
            return False
        clean = _speakable(text)
        if not clean:
            return False
        self._speaking = True
        self._last_tts = time.time()

        def run():
            try:
                os.makedirs(os.path.join(VOICE_DIR, "tmp"), exist_ok=True)
                if tts_engine() == "kokoro":
                    self._speak_kokoro(clean)
                else:
                    self._speak_piper(clean)
            except Exception as exc:
                print("tts:", exc)
            finally:
                self._speaking = False

        threading.Thread(target=run, daemon=True).start()
        return True

    def _ui_recording(self, maxs):
        self.cat.set_mood("typing")
        self.queue_draw()
        self.bubble.show_recording(maxs)
        # si arecord ya se paró solo (-d maxs), recoger el archivo ya
        rec = self._rec
        if rec is not None and rec.poll() is not None:
            self.q.put(("voice_stop", None))
        else:
            GLib.timeout_add_seconds(maxs + 1, self._rec_deadline)
        return False

    def _rec_deadline(self):
        if self._rec is not None:
            self.q.put(("voice_stop", None))
        return False

    def _ui_recording_done(self):
        self.cat.set_mood("idle")
        self.queue_draw()
        self.bubble.recording_done()
        self.bubble.show_reply("🎤 escuchando…", False)
        return False

    def on_poll(self):
        self.q.put(("poll", None))
        return True

    # --- worker --------------------------------------------------------
    def worker(self):
        while True:
            try:
                kind, payload = self.q.get(timeout=0.6)
            except queue.Empty:
                continue
            try:
                getattr(self, "_do_" + kind)(payload)
            except Exception as exc:
                GLib.idle_add(self._ui_error, f"{kind}: {exc}")

    def _do_connect(self, _):
        era_reconexion = self._reconnecting
        try:
            self.api.health()
            if not self.api.session_id:     # no pisar una sesión viva
                self._ensure_session()
            self._reconnecting = False
            GLib.idle_add(self._ui_ready)
            if era_reconexion:
                GLib.idle_add(self.bubble.show_reply, "✅ reconectado",
                              False, 2200)
        except Exception as exc:
            msg = str(exc)
            auth = "401" in msg or "Unauthorized" in msg
            if auth:
                # opencode serve cambia la contraseña en cada arranque:
                # solo start.sh puede arreglarla, reintentar no sirve.
                tip = ("el server se reinició y cambió la contraseña.\n"
                       "vuelve a lanzar start.sh (o el atajo del menú)")
                self._reconnecting = False  # y paro los reintentos
            elif "no pude hablar" in msg:
                tip = "arráncalo con start.sh"
            else:
                tip = "reintentando cada 15 s…"
            if not self._reconnecting:      # no repetir el aviso cada intento
                GLib.idle_add(self._ui_status, f"⚠ {msg[:120]}\n\n{tip}")
            if not auth and not self._reconnecting:
                self._reconnecting = True
                GLib.timeout_add_seconds(15, self._reintentar)

    def _reintentar(self):
        """Reintenta conectar cada 15 s mientras siga sin haber server."""
        if not self._reconnecting:
            return False                    # conectado o error sin arreglo
        if not self.waiting:                # no cambiar de sesión a media faena
            self.q.put(("connect", None))
        return True

    def _caida(self, msg):
        """El server dejó de contestar a mitad de sesión."""
        if not self.connected:
            return
        self.connected = False
        self.waiting = False
        self.api.session_id = None      # la del server viejo ya no sirve
        print("conexión perdida:", msg)
        GLib.idle_add(self._ui_status, "⚠ sin conexión con opencode\n\n"
                                       "reintentando…")
        if not self._reconnecting:
            self._reconnecting = True
            GLib.timeout_add_seconds(15, self._reintentar)

    def _do_new_session(self, _):
        """Olvida la sesión actual y abre otra (menú → Nueva sesión)."""
        self.waiting = False
        self._speak_next = False
        self.api.session_id = None
        try:
            self._ensure_session(force_new=True)
        except Exception as exc:
            GLib.idle_add(self._ui_error, f"sesión: {exc}")
            return
        GLib.idle_add(self.bubble.show_reply, "🆕 sesión nueva", False, 2000)

    def _ensure_session(self, force_new=False):
        """Sesion PROPIA del gato.

        Por defecto EMPIEZA UNA SESION NUEVA en cada arranque: si reutiliza la
        anterior, al abrir el gato cada dia te reencuentras con la conversacion
        de ayer. Si prefieres continuarla, pon "reuse_session": true en el
        config.

        Antes de esto cogia 'la primera sesion del directorio', que podia ser
        la tuya; con una sesion ocupada el prompt vuelve con delivery='steer'
        y se inyecta en el turno ajeno, asi que el gato no hacia nada.
        """
        sid = None
        if not force_new and CFG.get("reuse_session"):
            saved = CFG.get("session_id")
            if saved:
                try:
                    if self.api.get_session_by_id(saved):
                        sid = saved
                except RuntimeError:
                    sid = None

        if not sid:
            sid = self.api.create_session()["id"]

        self.api.session_id = sid
        self._remember_session(sid)
        try:
            self.api.set_agent()     # agente de respuestas cortas (mascota)
        except RuntimeError:
            pass
        try:
            self.api.set_model()
        except RuntimeError:
            pass

    def _remember_session(self, sid):
        CFG["session_id"] = sid
        self._save_cfg()

    def _do_send(self, text):
        # si la respuesta va a sonar, deja el sintetizador caliente YA: la carga
        # del modelo queda escondida en el tiempo que tarda opencode en pensar.
        if self._speak_next or str(voice_cfg().get("reply")) == "siempre":
            self._warm_tts()
        # ids de lo ya visto: al terminar solo me interessa lo nuevo.
        try:
            self._seen_ids = {m.get("id") for m in self.api.messages()
                              if isinstance(m, dict)}
        except RuntimeError:
            self._seen_ids = set()
        self.api.prompt(text)
        self.waiting = True
        self._sent_at = time.time() * 1000.0   # idle viene en ms epoch
        GLib.idle_add(self._ui_status, "trabajando…")
        GLib.idle_add(self.bubble.show_working)

    def _do_poll(self, _):
        if not self.api.session_id:
            return
        try:
            perms = self.api.pending_permissions()
        except RuntimeError:
            return
        for p in perms:
            if not isinstance(p, dict):
                continue
            rid = p.get("id")
            if not rid or rid in self.asked:
                continue
            if p.get("effect") not in (None, "ask"):
                continue
            self.asked.add(rid)
            action = p.get("action", "?")
            res = ", ".join(p.get("resources") or [])[:140]
            GLib.idle_add(self._ui_ask, rid, f"🔐 ¿permiso?\n{action}\n{res}")

        if not self.waiting:
            self._drain_external()
            return
        try:
            sess = self.api.get_session() or {}
        except RuntimeError as exc:
            self._caida(str(exc))
            return
        idle = ((sess.get("time") or {}).get("idle")) or 0
        if idle > self._sent_at:
            self.waiting = False
            if sess.get("outcome") == "failed":
                GLib.idle_add(self._ui_done, "⚠ opencode falló", False)
            else:
                try:
                    msgs = self.api.messages()
                except RuntimeError:
                    msgs = []
                txt = text_since(msgs, self._seen_ids) or "terminado."
                self._seen_ids = {m.get("id") for m in msgs if isinstance(m, dict)}
                GLib.idle_add(self._ui_done, txt[:400], True)

    def _drain_external(self):
        """Saca los mensajes externos en cola, uno por sondeo."""
        if not self._pending_external:
            return
        payload = self._pending_external.pop(0)
        GLib.idle_add(self._ui_external, payload)

    def _do_decide(self, payload):
        rid, decision = payload
        try:
            self.api.reply_permission(rid, decision)
        except RuntimeError as exc:
            GLib.idle_add(self._ui_error, f"permiso: {exc}")

    def _do_say(self, payload):
        """Mensaje que llega de fuera (plugin de opencode, otro agente)."""
        # si el gato esta en su propio turno, no lo interrumpo: se encola
        self._pending_external.append(payload)

    def _do_apply(self, _):
        """Aplica agente/modelo guardados a la sesión actual (best effort)."""
        if not self.api.session_id:
            GLib.idle_add(self._ui_status, "configuración guardada")
            return
        try:
            self.api.set_agent()
            self.api.set_model()
        except RuntimeError as exc:
            GLib.idle_add(self._ui_error, f"config: {exc}")
            return
        GLib.idle_add(self.bubble.show_reply, "✅ configuración aplicada",
                      False, 2600)

    def _ui_external(self, payload):
        text = payload.get("text", "")
        mood = payload.get("mood", "idle")
        title = payload.get("title")
        self.cat.set_mood(mood if mood in
                           ("idle", "typing", "happy", "alarm") else "idle")
        self.queue_draw()
        body = f"{title}\n{text}" if title else text
        if text:
            self._last_reply = body
        self.bubble.show_reply(body, editable=False,
                               auto_hide=payload.get("auto_hide")
                               or reply_hide_ms())
        return False

    # --- vuelta al hilo UI ---------------------------------------------
    def _ui_ready(self):
        self.connected = True
        self.status = "listo"
        self.cat.set_mood("happy", secs=1.5)
        self.queue_draw()
        return False

    def _ui_status(self, text):
        self.status = text
        if text.startswith("trabajando"):
            self.cat.set_mood("typing")
        self.queue_draw()
        return False

    def _ui_ask(self, rid, text):
        self.cat.set_mood("alarm")
        self.queue_draw()
        self.bubble.show_ask(
            text,
            on_yes=lambda: self.q.put(("decide", (rid, "once"))),
            on_no=lambda: self.q.put(("decide", (rid, "reject"))),
        )
        return False

    def _ui_done(self, text, editable):
        self._last_reply = text
        self.cat.set_mood("happy", secs=2.5)
        self.queue_draw()
        # ¿este turno va a sonar? (vino del micrófono, o reply="siempre")
        speaks = bool(self._speak_next or
                      (self.voice_ok()
                       and str(voice_cfg().get("reply")) == "siempre"))
        self._speak_next = False
        # _speak devuelve True solo si hay voz instalada y algo que decir: si no
        # sonara nada, nunca ocultamos el texto (te quedarias sin respuesta).
        played = self._speak(text) if speaks else False
        if played and bool(voice_cfg().get("hide_text")):
            # solo audio: quita también el reloj de "pensando"
            self.bubble.hide_bubble()
        else:
            # la respuesta llega en modo LECTURA: asi el siguiente clic siempre
            # lleva a escribir, en vez de cerrar la burbuja.
            # y se oculta sola pasado un rato para no dejarla colgada.
            self.bubble.show_reply(text, editable=False,
                                   auto_hide=reply_hide_ms())
        return False

    def _ui_error(self, msg):
        self.cat.set_mood("alarm")
        self.queue_draw()
        self.bubble.show_reply(f"⚠ {msg[:150]}", editable=False,
                               auto_hide=reply_hide_ms())
        return False


# --------------------------------------------------------------- ajustes

class PetSettings(Gtk.Window):
    """Ventana de configuración: mascota, agente, modelo y voz.

    La lista de agentes y modelos se pide en un hilo aparte (la red nunca corre
    en el hilo de la UI) y se vuelca al desplegable con GLib.idle_add. Al
    guardar: se escribe config.json y se le pide al gato que aplique agente y
    modelo a su sesión actual. La mascota cambia al instante (es solo dibujo).
    """

    def __init__(self, pet):
        super().__init__(title="Configuración del pet")
        self.pet = pet
        self._dead = False
        self.set_border_width(16)
        self.set_default_size(460, -1)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.connect("destroy", self._on_destroy)

        grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        self.add(grid)

        grid.attach(Gtk.Label(label="Mascota:", xalign=0), 0, 0, 1, 1)
        self.mascot_combo = Gtk.ComboBoxText()
        self.mascot_combo.set_hexpand(True)
        self.mascot_combo.set_tooltip_text(
            "Dibujo de la mascota. Se aplica al guardar, sin reiniciar.")
        for key, name in (("terminal", "mini terminal"),
                          ("gato", "gato")):
            self.mascot_combo.append(key, name)
        self.mascot_combo.set_active_id(str(CFG.get("mascot") or "terminal"))
        if self.mascot_combo.get_active() is None:
            self.mascot_combo.set_active(0)
        grid.attach(self.mascot_combo, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Agente:", xalign=0), 0, 1, 1, 1)
        self.agent_combo = Gtk.ComboBoxText()
        self.agent_combo.set_hexpand(True)
        self.agent_combo.set_tooltip_text("Agente con el que conversa la mascota.")
        grid.attach(self.agent_combo, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="Modelo:", xalign=0), 0, 2, 1, 1)
        self.model_combo = Gtk.ComboBoxText()
        self.model_combo.set_hexpand(True)
        self.model_combo.set_tooltip_text("Modelo que usa ese agente.")
        grid.attach(self.model_combo, 1, 2, 1, 1)

        # -- voz ---------------------------------------------------------
        v = voice_cfg()
        grid.attach(Gtk.Label(label="Voz:", xalign=0), 0, 3, 1, 1)
        self.voice_check = Gtk.CheckButton(
            label="micrófono (🎤 / Ctrl+Shift+F)")
        self.voice_check.set_active(bool(v.get("enabled", True)))
        self.voice_check.set_tooltip_text(
            "Botón 🎤 y atajo Ctrl+Shift+F para hablar con la mascota.")
        grid.attach(self.voice_check, 1, 3, 1, 1)

        grid.attach(Gtk.Label(label="Respuestas con audio:", xalign=0),
                    0, 4, 1, 1)
        self.voice_reply = Gtk.ComboBoxText()
        self.voice_reply.set_tooltip_text(
            "Cómo lee la mascota sus respuestas.")
        for key, name in (("voz", "solo si hablé por el micrófono"),
                          ("siempre", "siempre"),
                          ("nunca", "nunca (solo texto)")):
            self.voice_reply.append(key, name)
        self.voice_reply.set_active_id(str(v.get("reply", "voz")))
        if self.voice_reply.get_active() is None:
            self.voice_reply.set_active(0)
        grid.attach(self.voice_reply, 1, 4, 1, 1)

        grid.attach(Gtk.Label(label="Motor de voz:", xalign=0), 0, 5, 1, 1)
        self.engine_combo = Gtk.ComboBoxText()
        self.engine_combo.set_hexpand(True)
        self.engine_combo.set_tooltip_text(
            "piper: empieza en ~2 s y lee todo seguido. kokoro: más natural, "
            "pero en este CPU tarda y hace pausas entre frases.")
        for key, name in (("piper", "piper · fluido (recomendado)"),
                          ("kokoro", "kokoro · más natural, más lento")):
            self.engine_combo.append(key, name)
        self.engine_combo.set_active_id(tts_engine())
        if self.engine_combo.get_active() is None:
            self.engine_combo.set_active(0)
        grid.attach(self.engine_combo, 1, 5, 1, 1)

        grid.attach(Gtk.Label(label="Voz del pet:", xalign=0), 0, 6, 1, 1)
        caja_voz = Gtk.Box(spacing=6)
        self.pet_voice = Gtk.ComboBoxText()
        self.pet_voice.set_hexpand(True)
        self.pet_voice.set_tooltip_text(
            "Voz de kokoro con la que habla la mascota (se aplica al guardar).")
        for key, name in (("ef_dora", "ef_dora · mujer (España)"),
                          ("em_alex", "em_alex · hombre (España)"),
                          ("em_santa", "em_santa · hombre grave")):
            self.pet_voice.append(key, name)
        actual = str(v.get("kokoro_voice") or "ef_dora")
        if not self.pet_voice.set_active_id(actual):
            self.pet_voice.append(actual, actual)
            self.pet_voice.set_active_id(actual)
        self.btn_oir = Gtk.Button(label="▶ oír")
        self.btn_oir.set_tooltip_text(
            "Sintetiza una frase de ejemplo con la voz actual.")
        self.btn_oir.connect("clicked", self._on_oir)
        caja_voz.pack_start(self.pet_voice, True, True, 0)
        caja_voz.pack_start(self.btn_oir, False, False, 0)
        grid.attach(caja_voz, 1, 6, 1, 1)
        self.engine_combo.connect("changed", lambda *_: self._on_engine())
        self._on_engine()

        grid.attach(Gtk.Label(label="Texto:", xalign=0), 0, 7, 1, 1)
        self.hide_text_check = Gtk.CheckButton(
            label="no mostrarlo si la respuesta suena")
        self.hide_text_check.set_active(bool(v.get("hide_text", False)))
        self.hide_text_check.set_tooltip_text(
            "Si la respuesta se lee en voz alta, no se muestra también en la "
            "burbuja (la mascota solo habla). Los avisos y errores siempre se ven.")
        grid.attach(self.hide_text_check, 1, 7, 1, 1)

        et_audio = Gtk.Label(label="Audio:", xalign=0)
        et_audio.set_valign(Gtk.Align.START)
        grid.attach(et_audio, 0, 8, 1, 1)
        caja_audio = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.vol_slider = self._audio_slider(
            caja_audio, "Volumen", "audio_gain", -15, 15, v,
            "Ganancia solo para la voz de la mascota (no cambia el volumen "
            "del sistema).")
        self.graves_slider = self._audio_slider(
            caja_audio, "Graves", "audio_bass", -12, 12, v,
            "Refuerza (derecha) o quita (izquierda) las frecuencias bajas.")
        self.agudos_slider = self._audio_slider(
            caja_audio, "Nitidez", "audio_treble", -12, 12, v,
            "Agudos (~3,5 kHz): hacia la derecha se oye más nítido.")
        self.btn_probar = Gtk.Button(label="▶ probar")
        self.btn_probar.set_halign(Gtk.Align.END)
        self.btn_probar.set_tooltip_text(
            "Oír la voz actual con estos ajustes de audio.")
        self.btn_probar.connect("clicked", self._on_oir)
        caja_audio.pack_start(self.btn_probar, False, False, 0)
        grid.attach(caja_audio, 1, 8, 1, 1)

        self.info = Gtk.Label(label="cargando…", xalign=0)
        self.info.set_line_wrap(True)
        self.info.set_max_width_chars(52)
        self.info.get_style_context().add_class("dim-label")
        grid.attach(self.info, 0, 9, 2, 1)

        box = Gtk.Box(spacing=8)
        box.set_halign(Gtk.Align.END)
        self.btn_cancel = Gtk.Button(label="Cancelar")
        self.btn_cancel.connect("clicked", lambda *_: self.destroy())
        self.btn_save = Gtk.Button(label="Guardar")
        self.btn_save.get_style_context().add_class("suggested-action")
        self.btn_save.set_sensitive(False)
        self.btn_save.connect("clicked", self._on_save)
        box.pack_start(self.btn_cancel, False, False, 0)
        box.pack_start(self.btn_save, False, False, 0)
        grid.attach(box, 0, 10, 2, 1)

        self.show_all()
        threading.Thread(target=self._load, daemon=True).start()

    def _on_destroy(self, *_a):
        self._dead = True

    def _load(self):
        """Pide agentes y modelos al server (fuera del hilo de la UI)."""
        agents = models = None
        err = None
        try:
            agents = self.pet.api.list_agents()
            models = self.pet.api.list_models()
        except Exception as exc:
            err = str(exc)
        GLib.idle_add(self._populate, agents, models, err)

    def _populate(self, agents, models, err):
        if self._dead:
            return False
        if err:
            self.info.set_text(f"⚠ no pude leer agentes/modelos:\n{err[:180]}")
            return False

        # -- agentes ----------------------------------------------------
        cur_agent = (CFG.get("agent") or "").strip()
        ids = []
        for a in agents:
            if not isinstance(a, dict):
                continue
            aid = a.get("id")
            if not aid:
                continue
            ids.append(aid)
            name = a.get("name") or aid
            mode = a.get("mode") or "?"
            if a.get("hidden"):
                mode += ", oculto"
            self.agent_combo.append(aid, f"{name}  ({mode})")
        if cur_agent and cur_agent not in ids:
            self.agent_combo.append(cur_agent, cur_agent)
        if cur_agent:
            self.agent_combo.set_active_id(cur_agent)
        if self.agent_combo.get_active() is None and ids:
            self.agent_combo.set_active(0)

        # -- modelos ----------------------------------------------------
        cur_model = CFG.get("model") or {}
        cur_key = f"{cur_model.get('providerID', '')}/{cur_model.get('id', '')}"
        keys = []
        for m in models:
            if not isinstance(m, dict):
                continue
            mid = m.get("modelID") or m.get("id")
            pid = m.get("providerID") or ""
            if not mid:
                continue
            key = f"{pid}/{mid}"
            keys.append(key)
            name = m.get("name") or mid
            self.model_combo.append(key, f"{name}  ({pid})")
        if cur_key and cur_key not in keys:
            self.model_combo.append(cur_key, cur_key)
        if cur_key:
            self.model_combo.set_active_id(cur_key)
        if self.model_combo.get_active() is None and keys:
            self.model_combo.set_active(0)

        self.btn_save.set_sensitive(True)
        self.info.set_text("Elige mascota, agente, modelo y voz. Todo se "
                           "aplica al guardar.")
        return False

    def _on_save(self, _b):
        aid = self.agent_combo.get_active_id()
        if aid:
            CFG["agent"] = aid
        mid = self.model_combo.get_active_id()
        if mid:
            pid, _, name = mid.partition("/")
            CFG["model"] = {"providerID": pid, "id": name}
        mascot = self.mascot_combo.get_active_id()
        if mascot:
            CFG["mascot"] = mascot
        # voz: guardo lo editable; el resto de claves (rutas, prosodia) no se tocan
        v = dict(voice_cfg())
        v["enabled"] = self.voice_check.get_active()
        v["reply"] = self.voice_reply.get_active_id() or "voz"
        v["hide_text"] = self.hide_text_check.get_active()
        v["audio_gain"] = round(self.vol_slider.get_value())
        v["audio_bass"] = round(self.graves_slider.get_value())
        v["audio_treble"] = round(self.agudos_slider.get_value())
        v["engine"] = (self.engine_combo.get_active_id()
                       or v.get("engine") or "piper")
        v["kokoro_voice"] = (self.pet_voice.get_active_id()
                             or v.get("kokoro_voice") or "ef_dora")
        CFG["voice"] = v
        self.pet._save_cfg()
        self.pet.apply_settings()
        self.destroy()

    def _audio_slider(self, caja, nombre, clave, lo, hi, v, tip):
        """Un deslizador de dB con su etiqueta, dentro de la caja de audio."""
        fila = Gtk.Box(spacing=8)
        et = Gtk.Label(label=nombre, xalign=0)
        et.set_width_chars(8)
        esc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
        esc.set_digits(0)
        esc.set_draw_value(False)
        esc.set_hexpand(True)
        esc.set_tooltip_text(tip)
        esc.add_mark(0, Gtk.PositionType.BOTTOM, None)   # raya del cero
        valor = Gtk.Label(label="0 dB", xalign=1)
        valor.set_width_chars(7)

        def _actualizar(_s, lbl=valor):
            n = int(round(_s.get_value()))
            lbl.set_text("0 dB" if not n else f"{n:+d} dB")

        esc.connect("value-changed", _actualizar)
        try:
            esc.set_value(float(v.get(clave, 0) or 0))
        except (TypeError, ValueError):
            esc.set_value(0)
        _actualizar(esc)
        fila.pack_start(et, False, False, 0)
        fila.pack_start(esc, True, True, 0)
        fila.pack_start(valor, False, False, 0)
        caja.pack_start(fila, False, False, 0)
        return esc

    def _audio_cfg(self):
        """La config de voz con los valores actuales de los deslizadores."""
        v = dict(voice_cfg())
        v["audio_gain"] = self.vol_slider.get_value()
        v["audio_bass"] = self.graves_slider.get_value()
        v["audio_treble"] = self.agudos_slider.get_value()
        return v

    def _on_oir(self, _b):
        """Oye la voz elegida con una frase de ejemplo (sin bloquear la UI)."""
        motor = self.engine_combo.get_active_id() or tts_engine()
        voz = self.pet_voice.get_active_id() or "ef_dora"
        fx = audio_fx(self._audio_cfg())
        for b in (self.btn_oir, self.btn_probar):
            b.set_sensitive(False)
        self.info.set_text(f"generando la muestra con {motor}…")
        threading.Thread(target=self._oir_hilo, args=(motor, voz, fx),
                         daemon=True).start()

    def _on_engine(self):
        """La voz de kokoro solo tiene sentido con el motor kokoro."""
        piper = (self.engine_combo.get_active_id() or "piper") == "piper"
        self.pet_voice.set_sensitive(not piper)
        self.btn_oir.set_tooltip_text(
            "Oír la voz de piper (rápida, sin pausas)." if piper
            else "Oír la voz de kokoro elegida (tarda unos segundos).")

    def _oir_hilo(self, motor, voz, fx):
        wav = os.path.join(VOICE_DIR, "tmp", "prueba-voz.wav")
        frase = "Hola, soy tu mascota. Así suena mi voz.\n"
        try:
            os.makedirs(os.path.dirname(wav), exist_ok=True)
            if motor == "piper":
                r = subprocess.run(
                    NICE + [PIPER_BIN, "--model", tts_voice_path()]
                    + _piper_args() + ["--output_file", wav],
                    input=frase, text=True, capture_output=True, timeout=120)
            else:
                r = subprocess.run(
                    NICE + [sys.executable, KOKORO_TTS,
                            "--model", kokoro_model(),
                            "--voices", KOKORO_VOICES,
                            "--voice", voz,
                            "--lang", str(voice_cfg().get("kokoro_lang")
                                          or "es"),
                            "--speed", str(voice_cfg().get("kokoro_speed")
                                           or 1.0),
                            "--threads", str(kokoro_num("kokoro_threads", 0)),
                            "--output_file", wav],
                    input=frase, text=True, capture_output=True, timeout=120)
            if r.returncode != 0 or not os.path.exists(wav):
                raise RuntimeError((r.stderr or "sin audio").strip()[-200:])
            self.pet._play(wav, fx)     # aplay o ffplay; borra el temporal
            GLib.idle_add(self._oir_fin, "")
        except Exception as exc:
            GLib.idle_add(self._oir_fin, str(exc))

    def _oir_fin(self, err):
        if not self._dead:
            for b in (self.btn_oir, self.btn_probar):
                b.set_sensitive(True)
            self.info.set_text("muestra lista" if not err
                               else f"no pude generarla: {err[:50]}")
        return False


# --------------------------------------------------------------- atajo global

class Hotkey:
    """Atajo global en X11 (Ctrl+F) usando XGrabKey.

    Gdk no expone XGrabKey, asi que uso python-xlib: agarro la combinacion en
    la ventana raiz (asi funciona aunque el foco lo tenga otra aplicacion) y
    voy vaciando la cola de eventos X desde un timeout de GLib.

    OJO: al ser un grab global, Ctrl+F deja de llegar a las demas aplicaciones
    (navegador, editor...). Si molestara, pon "hotkey": "" en config.json.
    """

    def __init__(self, callback, keysym_name="f", shift=False):
        self.callback = callback
        self.ok = False
        self._d = None
        self._keycode = None
        try:
            from Xlib import X, XK, display as xdisplay
        except Exception as exc:
            print("xlib no disponible, sin atajo Ctrl+F:", exc)
            return
        self._X = X
        try:
            self._d = xdisplay.Display()
            self._d.set_error_handler(lambda *_: None)   # grab ocupado: callar
            root = self._d.screen().root
            keysym = XK.string_to_keysym(keysym_name)
            self._keycode = self._d.keysym_to_keycode(keysym)
            mask = X.ControlMask | (X.ShiftMask if shift else 0)
            # incluyo Bloq Mayus / Bloq Num porque cambian los modificadores
            for extra in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
                root.grab_key(self._keycode, mask | extra, False,
                              X.GrabModeAsync, X.GrabModeAsync)
            self._d.sync()
            self.ok = True
        except Exception as exc:
            print("no pude registrar el atajo Ctrl+F:", exc)

    def pump(self):
        """Procesa los eventos X pendientes (se llama desde el bucle de GLib)."""
        if not self.ok:
            return False
        try:
            while self._d.pending_events():
                ev = self._d.next_event()
                if ev.type == self._X.KeyPress and ev.detail == self._keycode:
                    GLib.idle_add(self.callback)
        except Exception:
            pass
        return True


# ------------------------------------------------------------------- main

def main():
    setup_log()
    if not CFG["password"]:
        print(f"Falta 'password' en {CFG_PATH}")
        return 1
    pet = Pet()
    install_css()
    srv = start_pet_server(pet, int(os.environ.get("PET_PORT", 4747)))
    if srv is None:
        print("aviso: no pude abrir el puerto del pet (¿ya hay otro gato?)")
    pet.show()
    pet.resize(pet.cat.size, pet.cat.size)
    pet.move(*pet._saved_pos())
    pet.q.put(("connect", None))
    # atajo global Ctrl+F -> abrir el input (como clicar el gato)
    if str(CFG.get("hotkey", "ctrl+f")).strip():
        hot = Hotkey(pet.open_input)
        if hot.ok:
            GLib.timeout_add(120, hot.pump)
        else:
            print("aviso: el atajo Ctrl+F no se pudo registrar")
    # atajo de voz Ctrl+Shift+F -> empezar/parar la grabación (push-to-talk)
    if voice_cfg().get("enabled", True):
        hot2 = Hotkey(pet.voice_toggle, "f", shift=True)
        if hot2.ok:
            GLib.timeout_add(120, hot2.pump)
        else:
            print("aviso: el atajo Ctrl+Shift+F (voz) no se pudo registrar")
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
