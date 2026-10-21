#!/usr/bin/env python3
"""
Genera el icono de la mascota a partir del MISMO código que la dibuja, para que
icono y ventana sean exactamente la misma cosa.

    ./make-icon.py [tamaño] [salida] [diseño]

`diseño` es "terminal" (por defecto) o "gato"; si se omite, usa el del config.
"""
import os
import sys
import importlib.util

import cairo  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("pet", os.path.join(HERE, "pet.py"))
pet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pet)

SIZE = int(sys.argv[1]) if len(sys.argv) > 1 else 128
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "opencode-pet.png")
DESIGN = (sys.argv[3] if len(sys.argv) > 3
          else (pet.CFG.get("mascot") or "terminal"))

mascota = pet.make_mascot(DESIGN)

# superficie ARGB con transparencia, a la resolucion pedida
surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
cr = cairo.Context(surf)
cr.scale(SIZE / mascota.size, SIZE / mascota.size)

mascota.draw(cr)
surf.flush()
surf.write_to_png(OUT)
os.chmod(OUT, 0o644)
print(f"icono {OUT} ({SIZE}x{SIZE}, {mascota.name})")
