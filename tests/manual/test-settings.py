#!/usr/bin/env python3
"""Prueba aislada de la ventana de configuración (PetSettings).

No toca el config real: usa un Pet falso cuyo _save_cfg no escribe nada.
Comprueba que se rellenan agentes y modelos, y que Guardar actualiza CFG
en memoria + llama a apply_settings.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..")))

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

import pet  # noqa: E402

CFG_BEFORE = {"agent": pet.CFG.get("agent"),
              "model": dict(pet.CFG.get("model") or {})}


class FakePet:
    def __init__(self):
        self.api = pet.Api(pet.CFG["server"], pet.CFG["username"],
                           pet.CFG["password"])
        self.saved = False
        self.applied = False

    def _save_cfg(self):
        self.saved = True

    def apply_settings(self):
        self.applied = True


def count(combo):
    model = combo.get_model()
    if model is None:
        return 0
    it = model.get_iter_first()
    n = 0
    while it:
        n += 1
        it = model.iter_next(it)
    return n


fp = FakePet()
win = pet.PetSettings(fp)

result = {"ok": False}


def check():
    info = win.info.get_text()
    ida = win.agent_combo.get_active_id()
    idm = win.model_combo.get_active_id()
    na = count(win.agent_combo)
    nm = count(win.model_combo)
    print("info          :", info, flush=True)
    print("agentes (n)   :", na, flush=True)
    print("modelos (n)   :", nm, flush=True)
    print("agente activo :", ida, flush=True)
    print("modelo activo :", idm, flush=True)
    print("guardar activa:", win.btn_save.get_sensitive(), flush=True)

    win._on_save(None)          # simula pulsar Guardar
    print("_save_cfg()   :", fp.saved, flush=True)
    print("apply_settings:", fp.applied, flush=True)
    print("CFG.agent     :", pet.CFG.get("agent"), flush=True)
    print("CFG.model     :", pet.CFG.get("model"), flush=True)

    ok = (na > 0 and nm > 0 and ida and idm and fp.saved and fp.applied)
    result["ok"] = bool(ok)
    print("RESULTADO     :", "OK" if ok else "FALLÓ", flush=True)
    Gtk.main_quit()
    return False


GLib.timeout_add(3000, check)
Gtk.main()

# restaurar CFG en memoria (por limpieza; no se escribió fichero)
pet.CFG["agent"] = CFG_BEFORE["agent"]
pet.CFG["model"] = CFG_BEFORE["model"]
sys.exit(0 if result["ok"] else 1)
