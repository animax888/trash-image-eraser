# Trash Image Eraser

App de escritorio (Python + Tkinter) para revisar imágenes de una carpeta una por una y decidir:

- **Borrar**: marca la imagen para borrarla al final.
- **Conservar**: deja la imagen en su lugar.

Al llegar a la última imagen se abre una revisión final con scroll, miniaturas y checkboxes para desmarcar las que no quieras borrar. Luego puedes borrar todas las seleccionadas con un solo botón.

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

- `D` o `Delete`: marcar para borrar
- `K` o `Espacio`: conservar
- `U`: deshacer la última acción
- Flechas `←` / `→`: navegar
- `Esc`: salir

## Progreso

Se guarda en el archivo `.trash_image_eraser_state.json` dentro de la carpeta seleccionada.
