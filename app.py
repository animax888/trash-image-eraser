import json
import shutil
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Falta Pillow. Instálalo con: pip install -r requirements.txt"
    ) from exc


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
STATE_FILENAME = ".trash_image_eraser_state.json"
DELETED_DIRNAME = "_deleted_by_trash_image_eraser"


@dataclass
class Action:
    kind: str  # "keep" | "delete"
    src: Path
    dst: Path | None = None


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Trash Image Eraser")
        self.geometry("1100x750")
        self.minsize(800, 550)

        self.folder: Path | None = None
        self.images: list[Path] = []
        self.index: int = 0

        self._photo: ImageTk.PhotoImage | None = None
        self._current_image_pil: Image.Image | None = None
        self._history: list[Action] = []

        self._build_ui()
        self._bind_keys()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(root)
        top.pack(fill=tk.X)

        self.folder_var = tk.StringVar(value="(Selecciona una carpeta)")
        ttk.Label(top, textvariable=self.folder_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(top, text="Elegir carpeta…", command=self.choose_folder).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(top, text="Reanudar", command=self.resume_if_possible).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(top, text="Reiniciar", command=self.reset_state).pack(side=tk.LEFT, padx=(8, 0))

        mid = ttk.Frame(root)
        mid.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self.canvas = tk.Canvas(mid, bg="#111111", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        bottom = ttk.Frame(root)
        bottom.pack(fill=tk.X, pady=(10, 0))

        self.status_var = tk.StringVar(value="Listo.")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        hints = "Teclas: [D] borrar (mover a _deleted) | [K] conservar | [U] deshacer | [Esc] salir"
        ttk.Label(bottom, text=hints).pack(side=tk.RIGHT)

        self.canvas.bind("<Configure>", lambda _e: self._redraw_current())

    def _bind_keys(self) -> None:
        self.bind("<Escape>", lambda _e: self._on_close())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.bind("d", lambda _e: self.delete_current())
        self.bind("D", lambda _e: self.delete_current())
        self.bind("<Delete>", lambda _e: self.delete_current())

        self.bind("k", lambda _e: self.keep_current())
        self.bind("K", lambda _e: self.keep_current())
        self.bind("<space>", lambda _e: self.keep_current())

        self.bind("u", lambda _e: self.undo())
        self.bind("U", lambda _e: self.undo())

        self.bind("<Left>", lambda _e: self.prev_image())
        self.bind("<Right>", lambda _e: self.next_image())

    # ------------- State / files -------------
    def _state_path(self) -> Path | None:
        if not self.folder:
            return None
        return self.folder / STATE_FILENAME

    def _deleted_dir(self) -> Path | None:
        if not self.folder:
            return None
        return self.folder / DELETED_DIRNAME

    def _load_state(self) -> dict:
        state_path = self._state_path()
        if not state_path or not state_path.exists():
            return {"index": 0, "kept": [], "deleted": []}
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"index": 0, "kept": [], "deleted": []}

    def _save_state(self, *, kept: list[str] | None = None, deleted: list[str] | None = None) -> None:
        if not self.folder:
            return
        state_path = self._state_path()
        if not state_path:
            return
        state = self._load_state()
        state["index"] = self.index
        if kept is not None:
            state["kept"] = kept
        if deleted is not None:
            state["deleted"] = deleted
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------- Folder / scanning -------------
    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(title="Selecciona una carpeta con imágenes")
        if not folder:
            return
        self.open_folder(Path(folder))

    def open_folder(self, folder: Path) -> None:
        self.folder = folder
        self.folder_var.set(str(folder))
        self._history.clear()

        deleted_dir = self._deleted_dir()
        if deleted_dir:
            deleted_dir.mkdir(parents=True, exist_ok=True)

        all_images = sorted(
            [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
        )

        # Exclude images already moved to deleted dir
        if deleted_dir:
            all_images = [p for p in all_images if deleted_dir not in p.parents]

        self.images = all_images
        self.index = 0

        if not self.images:
            self.status_var.set("No encontré imágenes en esa carpeta.")
            self._clear_canvas("Sin imágenes")
            return

        self.status_var.set(f"{len(self.images)} imágenes encontradas. Empezando desde la primera.")
        self._save_state(kept=[], deleted=[])
        self._show_current()

    def resume_if_possible(self) -> None:
        if not self.folder:
            messagebox.showinfo("Reanudar", "Primero elige una carpeta.")
            return
        state = self._load_state()
        saved_index = int(state.get("index", 0) or 0)
        saved_index = max(0, min(saved_index, max(0, len(self.images) - 1)))
        self.index = saved_index
        self.status_var.set(f"Reanudado en {self.index + 1}/{len(self.images)}.")
        self._show_current()

    def reset_state(self) -> None:
        if not self.folder:
            return
        if not messagebox.askyesno("Reiniciar", "¿Reiniciar el progreso guardado para esta carpeta?"):
            return
        state_path = self._state_path()
        if state_path and state_path.exists():
            state_path.unlink(missing_ok=True)
        self.index = 0
        self._history.clear()
        self._save_state(kept=[], deleted=[])
        self.status_var.set("Progreso reiniciado.")
        self._show_current()

    # ------------- Navigation -------------
    def next_image(self) -> None:
        if not self.images:
            return
        if self.index < len(self.images) - 1:
            self.index += 1
            self._save_state()
            self._show_current()
        else:
            self.status_var.set("Llegaste al final.")

    def prev_image(self) -> None:
        if not self.images:
            return
        if self.index > 0:
            self.index -= 1
            self._save_state()
            self._show_current()

    # ------------- Actions -------------
    def keep_current(self) -> None:
        current = self._current_path()
        if not current:
            return
        state = self._load_state()
        kept: list[str] = list(state.get("kept", []))
        deleted: list[str] = list(state.get("deleted", []))
        rel = self._rel(current)
        if rel not in kept:
            kept.append(rel)
        self._history.append(Action(kind="keep", src=current))
        self._save_state(kept=kept, deleted=deleted)
        self.status_var.set(f"Conservada: {current.name}")
        self.next_image()

    def delete_current(self) -> None:
        current = self._current_path()
        if not current:
            return
        deleted_dir = self._deleted_dir()
        if not deleted_dir:
            return

        target = self._unique_target(deleted_dir / current.name)
        try:
            shutil.move(str(current), str(target))
        except Exception as exc:
            messagebox.showerror("Error al borrar", f"No pude mover el archivo:\n{exc}")
            return

        state = self._load_state()
        kept: list[str] = list(state.get("kept", []))
        deleted: list[str] = list(state.get("deleted", []))
        rel = self._rel(current)
        if rel not in deleted:
            deleted.append(rel)
        self._history.append(Action(kind="delete", src=current, dst=target))
        self._save_state(kept=kept, deleted=deleted)
        self.status_var.set(f"Movida a {DELETED_DIRNAME}: {target.name}")
        self._refresh_images_after_move(current)
        self._show_current()

    def undo(self) -> None:
        if not self._history:
            return
        last = self._history.pop()
        state = self._load_state()
        kept: list[str] = list(state.get("kept", []))
        deleted: list[str] = list(state.get("deleted", []))

        if last.kind == "keep":
            rel = self._rel(last.src)
            if rel in kept:
                kept.remove(rel)
            self.status_var.set("Deshecho: conservar.")
        elif last.kind == "delete":
            if not last.dst or not self.folder:
                return
            # move back to original location if possible
            original = last.src
            original.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(last.dst), str(original))
            except Exception as exc:
                messagebox.showerror("Error al deshacer", f"No pude restaurar el archivo:\n{exc}")
                return
            rel = self._rel(original)
            if rel in deleted:
                deleted.remove(rel)
            self.images.insert(self.index, original)
            self.status_var.set("Deshecho: borrar (restaurado).")

        self._save_state(kept=kept, deleted=deleted)
        self._show_current()

    # ------------- Rendering -------------
    def _current_path(self) -> Path | None:
        if not self.images:
            return None
        self.index = max(0, min(self.index, len(self.images) - 1))
        return self.images[self.index]

    def _show_current(self) -> None:
        p = self._current_path()
        if not p:
            self._clear_canvas("Sin imágenes")
            return
        try:
            img = Image.open(p)
            self._current_image_pil = img.copy()
            img.close()
        except Exception as exc:
            self.status_var.set(f"No pude abrir {p.name}: {exc}")
            self._clear_canvas("Error")
            return
        self._redraw_current()
        self.status_var.set(f"{self.index + 1}/{len(self.images)} — {p.name}")

    def _redraw_current(self) -> None:
        if not self._current_image_pil:
            return
        cw = max(1, int(self.canvas.winfo_width()))
        ch = max(1, int(self.canvas.winfo_height()))
        img = self._current_image_pil.copy()
        img.thumbnail((cw - 20, ch - 20), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo, anchor="center")

    def _clear_canvas(self, text: str) -> None:
        self._photo = None
        self._current_image_pil = None
        self.canvas.delete("all")
        cw = max(1, int(self.canvas.winfo_width()))
        ch = max(1, int(self.canvas.winfo_height()))
        self.canvas.create_text(cw // 2, ch // 2, text=text, fill="white", font=("Segoe UI", 16))

    # ------------- Helpers -------------
    def _rel(self, path: Path) -> str:
        if not self.folder:
            return str(path)
        try:
            return str(path.relative_to(self.folder))
        except Exception:
            return str(path)

    def _unique_target(self, target: Path) -> Path:
        if not target.exists():
            return target
        stem = target.stem
        suffix = target.suffix
        parent = target.parent
        i = 1
        while True:
            candidate = parent / f"{stem} ({i}){suffix}"
            if not candidate.exists():
                return candidate
            i += 1

    def _refresh_images_after_move(self, moved_original: Path) -> None:
        # Remove the moved image from the current list; keep index pointing to "next" logical item
        try:
            idx = self.images.index(moved_original)
        except ValueError:
            return
        self.images.pop(idx)
        if self.index >= len(self.images):
            self.index = max(0, len(self.images) - 1)
        self._save_state()

    def _on_close(self) -> None:
        if messagebox.askokcancel("Salir", "¿Salir de la app?"):
            self.destroy()


if __name__ == "__main__":
    App().mainloop()

