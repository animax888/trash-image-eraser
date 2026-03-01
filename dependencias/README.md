## Dependencias para compilaciones offline

- Coloca aquí `libvlc.dll` y `libvlccore.dll` (extraídos de la carpeta `bin` de VLC).
- Copia toda la carpeta `plugins` dentro de `dependencias/vlc/`, tal como aparece en la instalación oficial (`plugins/*`).
- Coloca el icono en `dependencias/media/trash_image_eraser.ico` (ya está en `media/` dentro del repo, pero también puedes duplicarlo aquí para builds offline).
- Cuando ejecutes `python -m PyInstaller trash-image-eraser.spec`, el `.spec` buscará primero en `dependencias/vlc/` y `dependencias/media` antes de mirar `VLC_HOME`.

Si algún archivo falta, PyInstaller recurre otra vez a la instalación del sistema. Esta carpeta permite compilar incluso si el equipo de build no tiene VLC instalado.
