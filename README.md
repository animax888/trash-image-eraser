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

Necesitas VLC instalado en la maquina donde compilas. Luego usa `--onedir` y copia los binarios y plugins.

```powershell
python -m PyInstaller --onedir --windowed --name trash-image-eraser app.py `
  --add-binary "C:\\Program Files\\VideoLAN\\VLC\\libvlc.dll;." `
  --add-binary "C:\\Program Files\\VideoLAN\\VLC\\libvlccore.dll;." `
  --add-binary "C:\\Program Files\\VideoLAN\\VLC\\plugins;plugins"
```

El ejecutable quedara en `dist\\trash-image-eraser\\trash-image-eraser.exe` y funcionara sin instalar VLC en el PC destino.

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
