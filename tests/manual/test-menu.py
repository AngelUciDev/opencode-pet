#!/usr/bin/env python3
"""Prueba aislada del menú del clic derecho y de abrir Configuración.

Instancia el Pet real pero NO arranca el bucle principal ni pide conexión, así
que no crea sesión ni escribe el config. Solo comprueba:
  · el menú trae Configuración y Cerrar
  · open_settings() crea la ventana de configuración una sola vez
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

ok = True
p = pet.Pet()

menu = p._build_menu()
labels = []
for child in menu.get_children():
    if isinstance(child, Gtk.MenuItem) and child.get_label():
        labels.append(child.get_label())
print("items del menú:", labels, flush=True)
if labels != ["Configuración…", "Cerrar"]:
    print("FALLÓ: el menú no trae Configuración/Cerrar", flush=True)
    ok = False

p.open_settings()
w1 = p._settings
print("ventana de config:", type(w1).__name__ if w1 else None, flush=True)
p.open_settings()          # segunda vez: debe reutilizar, no crear otra
print("reutiliza ventana:", p._settings is w1, flush=True)
if w1 is None or p._settings is not w1:
    print("FALLÓ: open_settings no reutiliza", flush=True)
    ok = False

print("RESULTADO:", "OK" if ok else "FALLÓ", flush=True)
sys.exit(0 if ok else 1)
