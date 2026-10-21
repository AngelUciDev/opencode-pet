# Pruebas manuales de interfaz

Estas pruebas **no** son parte de la suite automática: abren ventanas reales,
leen el config real (`~/.config/opencode-pet/config.json`) y hablan con un
servidor de opencode. Están pensadas para desarrolladores, en Linux/X11.

Requieren `xdotool`, `xwininfo` e ImageMagick (`import`). Además, matan los
procesos de `pet.py` de **este repositorio** antes de empezar:

```bash
sudo apt install xdotool x11-utils imagemagick
```

Ejecuta una a una, con la mascota cerrada:

```bash
cd opencode-pet
./tests/manual/test-menu-settings.sh   # menú y ventana de configuración
./tests/manual/test-mini.sh            # abrir burbuja y mover el ratón
./tests/manual/test-ui.sh              # envío real, capturas en /tmp
./tests/manual/test-changes.sh         # burbuja, tiempo de respuesta, Ctrl+F
```

`test-ui.sh` escribe las capturas en `${TMPDIR:-/tmp}/opencode-pet-tests`, no
en el repositorio. No las ejecutes mientras usas la mascota para otra cosa.
