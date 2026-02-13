# Trash Image Eraser

App de escritorio (Python + Tkinter) para revisar imágenes de una carpeta una por una y decidir:

- **Borrar**: mueve la imagen a `_deleted_by_trash_image_eraser` (no borra permanentemente).
- **Conservar**: deja la imagen en su lugar.

## Ejecutar

1) Crear entorno e instalar dependencias:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2) Iniciar la app:

```powershell
py app.py
```

## Teclas

- `D` o `Delete`: mover a la carpeta de borrados
- `K` o `Espacio`: conservar
- `U`: deshacer la última acción (si fue borrado, restaura)
- Flechas `←` / `→`: navegar
- `Esc`: salir

## Progreso

Se guarda en el archivo `.trash_image_eraser_state.json` dentro de la carpeta seleccionada.

