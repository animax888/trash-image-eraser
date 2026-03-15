# Trash Image Eraser

App de escritorio en **Python + CustomTkinter** para revisar imágenes y videos de una carpeta una por una y decidir:

- **Borrar**: marca el archivo para moverlo al final a `_deleted_by_trash_image_eraser`.
- **Conservar**: mantiene el archivo en su ubicación original.

Al llegar al final se abre una revisión con miniaturas y checkboxes para confirmar qué borrar.

## Ejecutar en desarrollo

1. Crear entorno e instalar dependencias:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

2. Iniciar la app:

```powershell
python app.py
```

## Empaquetar con PyInstaller

```powershell
python -m pip install pyinstaller
pyinstaller trash-image-eraser.spec
```

Salida: `dist\\trash-image-eraser\\trash-image-eraser.exe`.

### Modo offline (recomendado para builds reproducibles)

Si existe `dependencias/vlc/` con `libvlc.dll`, `libvlccore.dll` y `plugins/`, el `.spec` usa esos archivos primero. Esto permite compilar sin VLC instalado en la máquina.

### Modo con VLC del sistema

Si no existe `dependencias/vlc/`, el `.spec` toma VLC de `VLC_HOME` o de `C:\\Program Files\\VideoLAN\\VLC`.

```powershell
$env:VLC_HOME = "D:\\Program Files\\VideoLAN\\VLC"
pyinstaller trash-image-eraser.spec
```

### Icono en build-time

El icono `dependencias/media/trash_image_eraser.ico` se empaqueta directamente (sin runtime hook), incluyendo la ruta de assets de `customtkinter`, para que ventana y ejecutable usen el mismo logo de forma estable.

## Teclas

- `D` o `Delete`: marcar para borrar
- `K` o `Espacio`: conservar
- `U`: deshacer la última acción
- Flechas `←` / `→`: navegar
- `Esc`: salir

## Compatibilidad

- **Imágenes**: `jpg`, `jpeg`, `png`, `bmp`, `gif`, `tif`, `tiff`, `webp`, `heic`
- **Videos**: `mp4`, `mov`, `mkv`, `avi`
- **Ejecución desde código fuente**: para vídeo necesitas `python-vlc` y DLL/plugins de VLC accesibles (instalación del sistema, `VLC_HOME`, o `dependencias/vlc`).
- **Ejecución desde `.exe` empaquetado**: el vídeo funciona con las DLL/plugins VLC incluidos en el bundle.

## Estado y logs

- El progreso se guarda en `.trash_image_eraser_state.json` dentro de la carpeta revisada.
- Si hay errores recuperables, se registran en `app.log` bajo:
  - Windows: `%LOCALAPPDATA%\\trash-image-eraser\\app.log`
  - Linux/macOS: `~/.local/state/trash-image-eraser/app.log` (si no hay `XDG_STATE_HOME`).
