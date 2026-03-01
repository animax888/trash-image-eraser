# Trash Image Eraser

App de escritorio (Python + Tkinter) para revisar imágenes de una carpeta una por una y decidir:

- **Borrar**: marca la imagen para borrarla al final.
- **Conservar**: deja la imagen en su lugar.

Al llegar a la última imagen se abre una revisión final con scroll, miniaturas y checkboxes para desmarcar las que no quieras borrar. Luego puedes borrar todas las seleccionadas con un solo botón.
Puedes elegir un archivo dentro de la carpeta para empezar desde ese archivo.

## Ejecutar

1) Crear entorno e instalar dependencias:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

2) Iniciar la app:

```powershell
python app.py
```

## Empaquetar sin dependencias (VLC incluido)

VLC debe estar instalado en el equipo donde compilas, porque el `.spec` (`trash-image-eraser.spec`) recoge `libvlc`, `libvlccore` y la carpeta `plugins`. Para compilar usa:

```powershell
python -m pip install pyinstaller
pyinstaller trash-image-eraser.spec
```

Si VLC está en otra ruta, establece `VLC_HOME` antes del comando:

```powershell
$env:VLC_HOME = "D:\\Program Files\\VideoLAN\\VLC"
pyinstaller trash-image-eraser.spec
```

El resultado aparece en `dist\\trash-image-eraser\\trash-image-eraser.exe` y los archivos de VLC quedan en `_internal\\vlcbin\\` (incluyendo `plugins\\`).

El `.spec` incluye `dependencias/media/trash_image_eraser.ico` y activa el runtime hook `hooks/rth_replace_customtkinter_icon.py`, que sobrescribe `customtkinter/assets/icons/CustomTkinter_icon_Windows.ico` dentro del bundle para que la ventana y la barra de tareas usen tu logo personalizado.

El `.spec` coloca siempre `libvlc.dll`, `libvlccore.dll` y la carpeta `plugins` dentro de `vlcbin/` en el bundle, por lo que en `onedir` lo verás bajo `_internal\\vlcbin\\`.

## Compilación offline con carpeta `dependencias`

Si el equipo de build no tiene VLC, coloca `libvlc.dll`, `libvlccore.dll` y la carpeta `plugins` bajo `dependencias/vlc/` (sigue la guía en `dependencias/README.md`). El icono y los recursos también vive en `dependencias/media/`, por lo que el `.spec` los toma de allí antes de mirar `VLC_HOME` y puedes compilar sin instalar VLC en cada máquina.

## Teclas

- `D` o `Delete`: marcar para borrar
- `K` o `Espacio`: conservar
- `U`: deshacer la última acción
- Flechas `←` / `→`: navegar
- `Esc`: salir
- Botón `Borrar marcadas ahora`: mueve todos los archivos con marca de eliminación al directorio `_deleted_by_trash_image_eraser` sin esperar a cerrar la revisión.
- Botón `Acerca de`: muestra instrucciones rápidas sobre los atajos y controles.

## Compatibilidad

- **Imágenes**: `jpg`, `jpeg`, `png`, `bmp`, `gif`, `tif`, `tiff`, `webp`, `heic`
- **Videos**: `mp4`, `mov`, `mkv`, `avi` (reproductor integrado)
- **Nota**: para videos necesitas VLC instalado y el paquete `python-vlc` (ya está en `requirements.txt`).

## Progreso

Se guarda en el archivo `.trash_image_eraser_state.json` dentro de la carpeta seleccionada.
