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
    was_kept: bool
    was_deleted: bool
    index_before: int


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
        self._review_window: tk.Toplevel | None = None
        self._review_selection: dict[str, tk.BooleanVar] = {}
        self._review_thumb_refs: list[ImageTk.PhotoImage] = []
        self._thumb_cache: dict[tuple[Path, int], ImageTk.PhotoImage] = {}

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

        strip = ttk.Frame(mid)
        strip.pack(fill=tk.X, pady=(0, 8))

        self.strip_canvas = tk.Canvas(strip, height=90, bg="#1a1a1a", highlightthickness=0)
        self.strip_canvas.pack(fill=tk.X, expand=False)

        self.canvas = tk.Canvas(mid, bg="#111111", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        bottom = ttk.Frame(root)
        bottom.pack(fill=tk.X, pady=(10, 0))

        self.status_var = tk.StringVar(value="Listo.")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        hints = "Teclas: [D] marcar para borrar | [K] conservar | [U] deshacer | [Esc] salir"
        ttk.Label(bottom, text=hints).pack(side=tk.RIGHT)

        self.canvas.bind("<Configure>", lambda _e: self._redraw_current())
        self.strip_canvas.bind("<Configure>", lambda _e: self._render_strip())

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

    def _decision_lists(self) -> tuple[list[str], list[str]]:
        state = self._load_state()
        kept: list[str] = list(state.get("kept", []))
        deleted: list[str] = list(state.get("deleted", []))
        return kept, deleted

    # ------------- Folder / scanning -------------
    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(title="Selecciona una carpeta con imágenes")
        if not folder:
            return
        self.open_folder(Path(folder))

    def open_folder(self, folder: Path) -> None:
        self._close_review_window()
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
        self._close_review_window()
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
            self._open_delete_review()

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
        kept, deleted = self._decision_lists()
        rel = self._rel(current)
        was_kept = rel in kept
        was_deleted = rel in deleted
        if not was_kept:
            kept.append(rel)
        if was_deleted:
            deleted.remove(rel)
        self._history.append(
            Action(
                kind="keep",
                src=current,
                was_kept=was_kept,
                was_deleted=was_deleted,
                index_before=self.index,
            )
        )
        self._save_state(kept=kept, deleted=deleted)
        self.status_var.set(f"Marcada para conservar: {current.name}")
        self.next_image()

    def delete_current(self) -> None:
        current = self._current_path()
        if not current:
            return
        kept, deleted = self._decision_lists()
        rel = self._rel(current)
        was_kept = rel in kept
        was_deleted = rel in deleted
        if was_kept:
            kept.remove(rel)
        if not was_deleted:
            deleted.append(rel)
        self._history.append(
            Action(
                kind="delete",
                src=current,
                was_kept=was_kept,
                was_deleted=was_deleted,
                index_before=self.index,
            )
        )
        self._save_state(kept=kept, deleted=deleted)
        self.status_var.set(f"Marcada para borrar: {current.name}")
        self.next_image()

    def undo(self) -> None:
        if not self._history:
            return
        last = self._history.pop()
        kept, deleted = self._decision_lists()
        rel = self._rel(last.src)
        if rel in kept:
            kept.remove(rel)
        if rel in deleted:
            deleted.remove(rel)
        if last.was_kept:
            kept.append(rel)
        if last.was_deleted:
            deleted.append(rel)

        self.index = max(0, min(last.index_before, max(0, len(self.images) - 1)))
        self._save_state(kept=kept, deleted=deleted)
        self.status_var.set(f"Deshecho: {last.kind}.")
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
        self._render_strip()
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
        self.strip_canvas.delete("all")

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

    def _render_strip(self) -> None:
        if not self.images:
            self.strip_canvas.delete("all")
            return
        width = max(1, int(self.strip_canvas.winfo_width()))
        height = max(1, int(self.strip_canvas.winfo_height()))
        thumb_size = 64
        pad = 10
        slot = thumb_size + pad
        columns = max(1, width // slot)
        before = columns // 2
        start = max(0, self.index - before)
        end = min(len(self.images), start + columns)
        start = max(0, end - columns)

        kept, deleted = self._decision_lists()
        kept_set = set(kept)
        deleted_set = set(deleted)

        self.strip_canvas.delete("all")
        x = pad // 2
        y = (height - thumb_size) // 2
        for i in range(start, end):
            path = self.images[i]
            rel = self._rel(path)
            key = (path, thumb_size)
            photo = self._thumb_cache.get(key)
            if photo is None:
                try:
                    img = Image.open(path)
                    thumb = img.copy()
                    img.close()
                    thumb.thumbnail((thumb_size, thumb_size), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(thumb)
                except Exception:
                    photo = ImageTk.PhotoImage(Image.new("RGB", (thumb_size, thumb_size), "#333333"))
                self._thumb_cache[key] = photo

            self.strip_canvas.create_image(x, y, image=photo, anchor="nw")

            if rel in kept_set:
                self.strip_canvas.create_rectangle(x, y, x + 30, y + 16, fill="#2e7d32", outline="")
                self.strip_canvas.create_text(x + 15, y + 8, text="OK", fill="white", font=("Segoe UI", 8, "bold"))
            elif rel in deleted_set:
                self.strip_canvas.create_rectangle(x, y, x + 30, y + 16, fill="#c62828", outline="")
                self.strip_canvas.create_text(x + 15, y + 8, text="DEL", fill="white", font=("Segoe UI", 8, "bold"))

            if i == self.index:
                self.strip_canvas.create_rectangle(
                    x - 2, y - 2, x + thumb_size + 2, y + thumb_size + 2, outline="#ffcc00", width=2
                )

            x += slot

    def _open_delete_review(self) -> None:
        if not self.folder:
            return
        if self._review_window and self._review_window.winfo_exists():
            self._review_window.lift()
            self._review_window.focus_force()
            return

        kept, deleted = self._decision_lists()
        review_items: list[tuple[str, Path]] = []
        for rel in deleted:
            candidate = self.folder / rel
            if candidate.exists() and candidate.is_file():
                review_items.append((rel, candidate))

        if not review_items:
            self.status_var.set("Fin de revisión. No hay imágenes marcadas para borrar.")
            self._clear_canvas("Revisión completa\nNo hay imágenes marcadas para borrar.")
            self._save_state(kept=kept, deleted=[])
            return

        win = tk.Toplevel(self)
        self._review_window = win
        win.title("Revisión final de borrado")
        win.geometry("980x680")
        win.minsize(720, 480)
        win.protocol("WM_DELETE_WINDOW", self._close_review_window)

        root = ttk.Frame(win, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            root,
            text=(
                "Revisa rápido las imágenes marcadas para borrar. "
                "Desmarca las que quieras conservar y pulsa el botón final."
            ),
        ).pack(fill=tk.X)

        container = ttk.Frame(root)
        container.pack(fill=tk.BOTH, expand=True, pady=(10, 10))

        canvas = tk.Canvas(container, highlightthickness=0)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.configure(yscrollcommand=scrollbar.set)

        list_frame = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=list_frame, anchor="nw")
        list_frame.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))

        self._review_selection = {}
        self._review_thumb_refs = []
        tiles: list[ttk.Frame] = []
        tile_width = 180

        for _idx, (rel, path) in enumerate(review_items, start=1):
            tile = ttk.Frame(list_frame, padding=(6, 6))
            tiles.append(tile)

            var = tk.BooleanVar(value=True)
            self._review_selection[rel] = var
            ttk.Checkbutton(tile, variable=var).pack(anchor="w")

            preview_label = ttk.Label(tile, text="Sin vista", anchor="center")
            preview_label.pack()
            try:
                img = Image.open(path)
                thumb = img.copy()
                img.close()
                thumb.thumbnail((150, 150), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(thumb)
                self._review_thumb_refs.append(photo)
                preview_label.configure(image=photo, text="")
            except Exception:
                pass

            ttk.Label(tile, text=path.name, wraplength=160, justify="center").pack()
            ttk.Label(tile, text=rel, wraplength=160, justify="center").pack()

        def _layout_tiles() -> None:
            width = max(1, canvas.winfo_width())
            columns = max(1, width // tile_width)
            for col in range(columns):
                list_frame.columnconfigure(col, weight=1)
            for i, tile in enumerate(tiles):
                row = i // columns
                col = i % columns
                tile.grid(row=row, column=col, padx=6, pady=6, sticky="n")

        _layout_tiles()
        canvas.bind(
            "<Configure>",
            lambda _e: (
                canvas.itemconfigure(window_id, width=canvas.winfo_width()),
                _layout_tiles(),
            ),
        )

        buttons = ttk.Frame(root)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Cerrar", command=self._close_review_window).pack(side=tk.RIGHT)
        ttk.Button(
            buttons,
            text="Borrar todas",
            command=self._delete_selected_from_review,
        ).pack(side=tk.RIGHT, padx=(0, 8))

        self.status_var.set(
            f"Fin de revisión: {len(review_items)} imágenes marcadas. Revisa y confirma el borrado."
        )

    def _close_review_window(self) -> None:
        if self._review_window and self._review_window.winfo_exists():
            self._review_window.destroy()
        self._review_window = None
        self._review_selection = {}
        self._review_thumb_refs = []

    def _delete_selected_from_review(self) -> None:
        if not self.folder:
            return
        deleted_dir = self._deleted_dir()
        if not deleted_dir:
            return
        selected = [rel for rel, var in self._review_selection.items() if var.get()]
        unselected = [rel for rel, var in self._review_selection.items() if not var.get()]
        if not selected:
            messagebox.showinfo("Borrado", "No hay imágenes seleccionadas para borrar.")
            return
        if not messagebox.askyesno("Confirmar", f"¿Mover {len(selected)} imágenes a {DELETED_DIRNAME}?"):
            return

        deleted_dir.mkdir(parents=True, exist_ok=True)
        kept, deleted = self._decision_lists()
        moved: list[str] = []
        failed: list[str] = []
        for rel in selected:
            src = self.folder / rel
            if not src.exists():
                failed.append(f"{rel} (ya no existe)")
                continue
            target = self._unique_target(deleted_dir / src.name)
            try:
                shutil.move(str(src), str(target))
            except Exception as exc:
                failed.append(f"{rel} ({exc})")
                continue
            moved.append(rel)

        moved_set = set(moved)
        unselected_set = set(unselected)
        if unselected:
            for rel in unselected:
                if rel not in kept:
                    kept.append(rel)
        deleted = [rel for rel in deleted if rel not in moved_set and rel not in unselected_set]
        self.images = [p for p in self.images if self._rel(p) not in moved_set]
        if self.index >= len(self.images):
            self.index = max(0, len(self.images) - 1)
        self._save_state(kept=kept, deleted=deleted)
        self._history.clear()
        self._close_review_window()

        if failed:
            messagebox.showwarning(
                "Borrado parcial",
                "Algunas imágenes no se pudieron mover:\n" + "\n".join(failed[:10]),
            )
        if moved:
            self.status_var.set(f"Movidas {len(moved)} imágenes a {DELETED_DIRNAME}.")
        else:
            self.status_var.set("No se movió ninguna imagen.")
        if self.images:
            self._show_current()
        else:
            self._clear_canvas("Sin imágenes")

    def _on_close(self) -> None:
        if messagebox.askokcancel("Salir", "¿Salir de la app?"):
            self.destroy()


if __name__ == "__main__":
    App().mainloop()
