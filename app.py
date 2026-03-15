import json
import logging
import os
import queue
import shutil
import sys
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable

import customtkinter as ctk

try:
    from PIL import Image, ImageOps, ImageTk
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Falta Pillow. Instálalo con: pip install -r requirements.txt"
    ) from exc

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pillow_heif = None

try:
    import vlc
except Exception:
    vlc = None


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".heic"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS
STATE_FILENAME = ".trash_image_eraser_state.json"
DELETED_DIRNAME = "_deleted_by_trash_image_eraser"


def _logging_base_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home())
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home)
    return Path.home() / ".local" / "state"


def _configure_logger() -> logging.Logger:
    logger = logging.getLogger("trash_image_eraser")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    try:
        log_dir = _logging_base_dir() / "trash-image-eraser"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
    return logger


LOGGER = _configure_logger()


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def scan_media_files(
    folder: Path,
    media_exts: set[str] | None = None,
    deleted_dirname: str = DELETED_DIRNAME,
) -> list[Path]:
    exts = media_exts or MEDIA_EXTS
    deleted_dir = folder / deleted_dirname
    results = [
        p
        for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in exts and deleted_dir not in p.parents
    ]
    return sorted(results)


def _safe_relative(path: Path, folder: Path) -> str:
    try:
        return str(path.relative_to(folder))
    except Exception:
        return str(path)


def has_state_progress(state: dict) -> bool:
    return bool(state.get("kept") or state.get("deleted") or _safe_int(state.get("index", 0), 0) > 0)


def sanitize_state_payload(state: dict, files: list[Path], folder: Path) -> dict:
    valid_rel = {_safe_relative(path, folder) for path in files}
    raw_deleted = state.get("deleted", [])
    raw_kept = state.get("kept", [])
    if not isinstance(raw_deleted, list):
        raw_deleted = []
    if not isinstance(raw_kept, list):
        raw_kept = []
    deleted = {str(rel) for rel in raw_deleted if str(rel) in valid_rel}
    kept = {str(rel) for rel in raw_kept if str(rel) in valid_rel}
    kept.difference_update(deleted)
    index = _safe_int(state.get("index", 0), 0)
    if files:
        index = max(0, min(index, len(files) - 1))
    else:
        index = 0
    return {
        "index": index,
        "kept": sorted(kept),
        "deleted": sorted(deleted),
    }


def resolve_initial_index(
    files: list[Path],
    state: dict,
    start_path: Path | None,
) -> int:
    if not files:
        return 0
    if has_state_progress(state):
        return max(0, min(_safe_int(state.get("index", 0), 0), len(files) - 1))
    if start_path is None:
        return 0
    for idx, candidate in enumerate(files):
        try:
            if candidate.samefile(start_path):
                return idx
        except Exception:
            if candidate == start_path:
                return idx
    return 0


def unique_target_path(target: Path) -> Path:
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


def update_marks_after_move(
    kept: set[str],
    deleted: set[str],
    moved: set[str],
    unselected: set[str] | None = None,
) -> tuple[set[str], set[str]]:
    next_kept = set(kept)
    next_deleted = set(deleted)
    unselected_set = set(unselected or set())

    if unselected_set:
        next_kept.update(unselected_set)
    next_deleted.difference_update(moved | unselected_set)
    next_kept.difference_update(moved)
    return next_kept, next_deleted


def _prepend_env_path(path: Path) -> None:
    value = str(path)
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    if value not in parts:
        os.environ["PATH"] = value + (os.pathsep + current if current else "")


def _vlc_candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        dirs.append(exe_dir)
        vlcbin_dir = exe_dir / "vlcbin"
        if vlcbin_dir not in dirs:
            dirs.append(vlcbin_dir)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            meipass_dir = Path(meipass)
            if meipass_dir != exe_dir:
                dirs.append(meipass_dir)
            vlcbin_meipass = meipass_dir / "vlcbin"
            if vlcbin_meipass not in dirs and vlcbin_meipass != meipass_dir:
                dirs.append(vlcbin_meipass)
    else:
        root = Path(__file__).resolve().parent
        local_vlc = root / "dependencias" / "vlc"
        if local_vlc.exists():
            dirs.append(local_vlc)

    env_vlc_home = os.environ.get("VLC_HOME")
    if env_vlc_home:
        env_path = Path(env_vlc_home)
        if env_path not in dirs:
            dirs.append(env_path)
    return dirs


def _prepare_vlc_environment() -> Path | None:
    plugin_path: Path | None = None

    for base in _vlc_candidate_dirs():
        if (base / "libvlc.dll").exists():
            _prepend_env_path(base)

    for base in _vlc_candidate_dirs():
        candidate = base / "plugins"
        if candidate.is_dir():
            plugin_path = candidate
            os.environ["VLC_PLUGIN_PATH"] = str(candidate)
            break

    if plugin_path is None:
        env = os.environ.get("VLC_PLUGIN_PATH", "")
        if env:
            first = env.split(os.pathsep)[0]
            try:
                first_path = Path(first)
                if first_path.is_dir():
                    plugin_path = first_path
            except Exception:
                LOGGER.exception("Error preparando VLC_PLUGIN_PATH")
                plugin_path = None

    return plugin_path


def _resource_path(*parts: str | Path) -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        base = Path(meipass) if meipass else Path(__file__).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


def _resolve_icon_path() -> Path | None:
    icon_name = "trash_image_eraser.ico"
    root = Path(__file__).resolve().parent
    candidates = [
        _resource_path("dependencias", "media", icon_name),
        _resource_path(icon_name),
        root / "dependencias" / "media" / icon_name,
        root / icon_name,
    ]
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        candidates.extend(
            [
                exe_dir / "dependencias" / "media" / icon_name,
                exe_dir / icon_name,
            ]
        )

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists():
            return candidate
    return None


def _decode_image_for_view(path: Path, max_w: int, max_h: int) -> tuple[Image.Image | None, str | None]:
    try:
        with Image.open(path) as img:
            frame = ImageOps.exif_transpose(img)
            if frame.mode not in {"RGB", "RGBA"}:
                frame = frame.convert("RGB")
            frame.thumbnail((max(1, max_w - 20), max(1, max_h - 20)), Image.Resampling.LANCZOS)
            return frame.copy(), None
    except Exception as exc:
        return None, str(exc)


def _decode_image_for_thumb(path: Path, size: int) -> tuple[Image.Image | None, str | None]:
    try:
        with Image.open(path) as img:
            frame = ImageOps.exif_transpose(img)
            if frame.mode not in {"RGB", "RGBA"}:
                frame = frame.convert("RGB")
            frame.thumbnail((size, size), Image.Resampling.LANCZOS)
            return frame.copy(), None
    except Exception as exc:
        return None, str(exc)


@dataclass
class Action:
    kind: str  # "keep" | "delete"
    src: Path
    was_kept: bool
    was_deleted: bool
    index_before: int


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Trash Image Eraser")
        self.geometry("1100x750")
        self.minsize(800, 550)
        self._icon_image: ImageTk.PhotoImage | None = None
        icon_path = _resolve_icon_path()
        if icon_path:
            try:
                self.iconbitmap(default=str(icon_path))
            except Exception:
                LOGGER.debug("No se pudo aplicar iconbitmap desde %s", icon_path, exc_info=True)
            try:
                with Image.open(icon_path) as icon_file:
                    self._icon_image = ImageTk.PhotoImage(icon_file.copy())
                self.iconphoto(True, self._icon_image)
            except Exception:
                LOGGER.debug("No se pudo aplicar iconphoto desde %s", icon_path, exc_info=True)

        self.folder: Path | None = None
        self.images: list[Path] = []
        self.index: int = 0

        self._photo: ImageTk.PhotoImage | None = None
        self._history: list[Action] = []
        self._review_window: tk.Toplevel | None = None
        self._review_selection: dict[str, tk.BooleanVar] = {}
        self._thumb_cache: dict[tuple[Path, int], ImageTk.PhotoImage] = {}
        self._thumb_waiters: dict[tuple[Path, int], list[Callable[[ImageTk.PhotoImage], None]]] = {}
        self._thumb_placeholder = ImageTk.PhotoImage(Image.new("RGB", (64, 64), "#333333"))
        self._review_thumb_placeholder = ImageTk.PhotoImage(Image.new("RGB", (150, 150), "#333333"))
        self._thumb_pending: set[tuple[Path, int]] = set()
        self._display_cache: dict[tuple[Path, int, int], Image.Image] = {}
        self._display_loading_token = 0
        self._media_generation = 0
        self._scan_generation = 0
        self._resize_job: str | None = None
        self._show_job: str | None = None
        self._strip_render_job: str | None = None
        self._current_image_path: Path | None = None
        self._worker = ThreadPoolExecutor(max_workers=2, thread_name_prefix="media-loader")
        self._scan_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="media-scan")
        self._kept_set: set[str] = set()
        self._deleted_set: set[str] = set()
        self._state_save_job: str | None = None
        self._state_dirty = False
        self._is_closing = False
        self._video_available = False
        self._vlc_instance = None
        self._vlc_player = None
        self._vlc_event_manager = None
        self._vlc_events: queue.SimpleQueue[tuple[str, int]] = queue.SimpleQueue()
        self._vlc_event_job: str | None = None
        self._video_path: Path | None = None
        self._video_update_job: str | None = None
        self._video_session = 0
        self._seeking = False
        self._duration_ms = 0
        self._progress_var = tk.DoubleVar(value=0.0)
        self._volume_var = tk.IntVar(value=70)
        self._time_var = tk.StringVar(value="00:00 / 00:00")

        if vlc is not None:
            try:
                plugin_path = _prepare_vlc_environment()
                args = []
                if plugin_path:
                    args.append(f"--plugin-path={plugin_path}")
                self._vlc_instance = vlc.Instance(args)
                self._vlc_player = self._vlc_instance.media_player_new()
                self._vlc_event_manager = self._vlc_player.event_manager()
                self._vlc_event_manager.event_attach(
                    vlc.EventType.MediaPlayerEndReached, self._on_vlc_end
                )
                self._video_available = True
            except Exception:
                LOGGER.exception("No se pudo inicializar VLC")
                self._vlc_instance = None
                self._vlc_player = None
                self._vlc_event_manager = None

        self._build_ui()
        self._bind_keys()
        if self._video_available:
            self._start_vlc_event_poller()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(self)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 5))
        top.grid_columnconfigure(0, weight=1)

        self.folder_var = tk.StringVar(value="(Selecciona una carpeta)")
        ctk.CTkLabel(top, textvariable=self.folder_var, anchor="w").grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(top, text="Elegir archivo o carpeta...", command=self.choose_folder).grid(row=0, column=1, padx=4)
        ctk.CTkButton(top, text="Reanudar", command=self.resume_if_possible).grid(row=0, column=2, padx=4)
        ctk.CTkButton(top, text="Reiniciar", command=self.reset_state).grid(row=0, column=3, padx=4)
        ctk.CTkButton(top, text="Borrar marcadas ahora", command=self._flush_deleted_items).grid(row=0, column=4, padx=4)
        ctk.CTkButton(top, text="Acerca de", command=self._show_about).grid(row=0, column=5, padx=4)

        mid = ctk.CTkFrame(self)
        mid.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        mid.grid_rowconfigure(1, weight=1)
        mid.grid_columnconfigure(0, weight=1)

        strip_frame = ctk.CTkFrame(mid, fg_color="#1a1a1a")
        strip_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        strip_frame.grid_columnconfigure(0, weight=1)

        self.strip_canvas = tk.Canvas(strip_frame, height=90, bg="#1a1a1a", highlightthickness=0)
        self.strip_canvas.grid(row=0, column=0, sticky="ew")

        self.canvas = tk.Canvas(mid, bg="#111111", highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")

        self.video_controls = ctk.CTkFrame(mid)
        self.video_controls.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.video_controls.grid_columnconfigure(2, weight=1)

        self.play_pause_text = tk.StringVar(value="Play")
        self.play_pause_btn = ctk.CTkButton(
            self.video_controls,
            textvariable=self.play_pause_text,
            command=self.toggle_play_pause,
            state="normal",
        )
        self.play_pause_btn.grid(row=0, column=0, padx=(8, 0))

        ctk.CTkLabel(self.video_controls, textvariable=self._time_var).grid(row=0, column=1, padx=(8, 8))

        self.progress_scale = ctk.CTkSlider(
            self.video_controls,
            from_=0,
            to=1,
            command=self._on_seek_change,
            variable=self._progress_var,
        )
        self.progress_scale.grid(row=0, column=2, sticky="ew")
        self.progress_scale.bind("<ButtonPress-1>", self._on_seek_start)
        self.progress_scale.bind("<ButtonRelease-1>", self._on_seek_end)

        ctk.CTkLabel(self.video_controls, text="Vol").grid(row=0, column=3, padx=(8, 0))
        self.volume_scale = ctk.CTkSlider(
            self.video_controls,
            from_=0,
            to=100,
            command=self._on_volume_change,
            variable=self._volume_var,
        )
        self.volume_scale.grid(row=0, column=4, sticky="ew", padx=(4, 8))
        self.video_controls.grid_columnconfigure(4, weight=0)
        self.video_controls.grid_remove()

        bottom = ctk.CTkFrame(self)
        bottom.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 10))
        bottom.grid_columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="Listo.")
        ctk.CTkLabel(bottom, textvariable=self.status_var, anchor="w").grid(row=0, column=0, sticky="ew")
        hints = "Teclas: [D] marcar para borrar | [K] conservar | [U] deshacer | [Esc] salir"
        ctk.CTkLabel(bottom, text=hints, anchor="e").grid(row=0, column=1, sticky="e")

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.strip_canvas.bind("<Configure>", lambda _e: self._schedule_strip_render())

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
            LOGGER.exception("No se pudo leer el estado en %s", state_path)
            return {"index": 0, "kept": [], "deleted": []}

    def _state_payload(self) -> dict:
        return {
            "index": self.index,
            "kept": sorted(self._kept_set),
            "deleted": sorted(self._deleted_set),
        }

    def _apply_state(self, state: dict) -> None:
        self._kept_set = set(state.get("kept", []))
        self._deleted_set = set(state.get("deleted", []))

    def _flush_state_to_disk(self) -> None:
        self._state_save_job = None
        if not self._state_dirty:
            return
        if not self.folder:
            return
        state_path = self._state_path()
        if not state_path:
            return
        try:
            state_path.write_text(
                json.dumps(self._state_payload(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._state_dirty = False
        except Exception:
            LOGGER.exception("No se pudo guardar el estado en %s", state_path)

    def _schedule_state_save(self, immediate: bool = False) -> None:
        self._state_dirty = True
        if immediate:
            if self._state_save_job is not None:
                try:
                    self.after_cancel(self._state_save_job)
                except Exception:
                    LOGGER.debug("No se pudo cancelar _state_save_job", exc_info=True)
                self._state_save_job = None
            self._flush_state_to_disk()
            return
        if self._state_save_job is not None:
            return
        self._state_save_job = self.after(220, self._flush_state_to_disk)

    def _save_state(self) -> None:
        self._schedule_state_save()

    # ------------- Folder / scanning -------------
    def choose_folder(self) -> None:
        filetypes = [
            ("Todos", "*.*"),
            (
                "Media",
                "*.jpg;*.jpeg;*.png;*.heic;*.webp;*.gif;*.bmp;*.tif;*.tiff;*.mp4;*.mov;*.mkv;*.avi",
            ),
        ]
        file_path = filedialog.askopenfilename(
            title="Selecciona un archivo dentro de la carpeta",
            filetypes=filetypes,
        )
        if file_path:
            self.open_folder(Path(file_path).parent, start_path=Path(file_path))
            return
        folder = filedialog.askdirectory(title="Selecciona una carpeta")
        if not folder:
            return
        self.open_folder(Path(folder))

    def open_folder(self, folder: Path, start_path: Path | None = None) -> None:
        if self.folder and self.folder != folder:
            self._schedule_state_save(immediate=True)
        self._close_review_window()
        self.folder = folder
        self.folder_var.set(str(folder))
        self._history.clear()
        self._thumb_cache.clear()
        self._thumb_waiters.clear()
        self._thumb_pending.clear()
        self._display_cache.clear()
        self._display_loading_token += 1
        self._media_generation += 1
        self._scan_generation += 1
        current_scan = self._scan_generation
        self._stop_video()

        deleted_dir = self._deleted_dir()
        if deleted_dir:
            deleted_dir.mkdir(parents=True, exist_ok=True)

        self.images = []
        self.index = 0
        self._kept_set.clear()
        self._deleted_set.clear()
        self.strip_canvas.delete("all")
        self._clear_canvas("Escaneando medios...")
        self.status_var.set("Escaneando carpeta...")

        future = self._scan_worker.submit(scan_media_files, folder, MEDIA_EXTS, DELETED_DIRNAME)

        def _apply_scan() -> None:
            if self._is_closing or current_scan != self._scan_generation:
                return
            if self.folder != folder:
                return
            try:
                media_files = future.result()
            except Exception:
                LOGGER.exception("Error escaneando carpeta %s", folder)
                self.status_var.set("No se pudo escanear la carpeta seleccionada.")
                self._clear_canvas("Error al escanear")
                self.strip_canvas.delete("all")
                return
            self._finalize_open_folder(folder, media_files, start_path)

        def _dispatch(_fut: object) -> None:
            try:
                self.after(0, _apply_scan)
            except Exception:
                LOGGER.debug("No se pudo despachar el resultado del escaneo", exc_info=True)

        future.add_done_callback(_dispatch)

    def _finalize_open_folder(
        self,
        folder: Path,
        media_files: list[Path],
        start_path: Path | None,
    ) -> None:
        if self._is_closing or self.folder != folder:
            return

        self.images = media_files
        if not self.images:
            self.index = 0
            self._kept_set.clear()
            self._deleted_set.clear()
            self.status_var.set("No encontré archivos compatibles en esa carpeta.")
            self._clear_canvas("Sin medios")
            self.strip_canvas.delete("all")
            return

        raw_state = self._load_state()
        state = sanitize_state_payload(raw_state, self.images, folder)
        self._apply_state(state)
        self.index = resolve_initial_index(self.images, state, start_path)
        resumed = has_state_progress(state)

        if resumed:
            self.status_var.set(
                f"{len(self.images)} archivos encontrados. Reanudado automáticamente en {self.index + 1}/{len(self.images)}."
            )
        elif start_path:
            self.status_var.set(
                f"{len(self.images)} archivos encontrados. Empezando desde el seleccionado."
            )
        else:
            self.status_var.set(
                f"{len(self.images)} archivos encontrados. Empezando desde el primero."
            )
        self._schedule_show_current()

    def resume_if_possible(self) -> None:
        if not self.folder:
            messagebox.showinfo("Reanudar", "Primero elige una carpeta.")
            return
        if not self.images:
            messagebox.showinfo("Reanudar", "No hay medios cargados todavía.")
            return
        state = sanitize_state_payload(self._load_state(), self.images, self.folder)
        if not has_state_progress(state):
            messagebox.showinfo("Reanudar", "No hay progreso guardado para esta carpeta.")
            return
        self._apply_state(state)
        saved_index = _safe_int(state.get("index", 0), 0)
        saved_index = max(0, min(saved_index, max(0, len(self.images) - 1)))
        self.index = saved_index
        self.status_var.set(f"Reanudado en {self.index + 1}/{len(self.images)}.")
        self._schedule_show_current()

    def reset_state(self) -> None:
        if not self.folder:
            return
        if not messagebox.askyesno("Reiniciar", "¿Reiniciar el progreso guardado para esta carpeta?"):
            return
        self._close_review_window()
        state_path = self._state_path()
        if state_path and state_path.exists():
            try:
                state_path.unlink(missing_ok=True)
            except Exception:
                LOGGER.exception("No se pudo borrar estado %s", state_path)
        self.index = 0
        self._history.clear()
        self._kept_set.clear()
        self._deleted_set.clear()
        if self._state_save_job is not None:
            try:
                self.after_cancel(self._state_save_job)
            except Exception:
                LOGGER.debug("No se pudo cancelar _state_save_job en reset", exc_info=True)
            self._state_save_job = None
        self._state_dirty = False
        self.status_var.set("Progreso reiniciado.")
        self._schedule_show_current()

    # ------------- Navigation -------------
    def next_image(self) -> None:
        if not self.images:
            return
        if self.index < len(self.images) - 1:
            self.index += 1
            self._save_state()
            self._schedule_show_current()
        else:
            self._open_delete_review()

    def prev_image(self) -> None:
        if not self.images:
            return
        if self.index > 0:
            self.index -= 1
            self._save_state()
            self._schedule_show_current()

    # ------------- Actions -------------
    def keep_current(self) -> None:
        current = self._current_path()
        if not current:
            return
        rel = self._rel(current)
        was_kept = rel in self._kept_set
        was_deleted = rel in self._deleted_set
        if not was_kept:
            self._kept_set.add(rel)
        if was_deleted:
            self._deleted_set.discard(rel)
        self._history.append(
            Action(
                kind="keep",
                src=current,
                was_kept=was_kept,
                was_deleted=was_deleted,
                index_before=self.index,
            )
        )
        self._save_state()
        self.status_var.set(f"Marcada para conservar: {current.name}")
        self.next_image()

    def delete_current(self) -> None:
        current = self._current_path()
        if not current:
            return
        rel = self._rel(current)
        was_kept = rel in self._kept_set
        was_deleted = rel in self._deleted_set
        if was_kept:
            self._kept_set.discard(rel)
        if not was_deleted:
            self._deleted_set.add(rel)
        self._history.append(
            Action(
                kind="delete",
                src=current,
                was_kept=was_kept,
                was_deleted=was_deleted,
                index_before=self.index,
            )
        )
        self._save_state()
        self.status_var.set(f"Marcada para borrar: {current.name}")
        self.next_image()

    def undo(self) -> None:
        if not self._history:
            return
        last = self._history.pop()
        rel = self._rel(last.src)
        self._kept_set.discard(rel)
        self._deleted_set.discard(rel)
        if last.was_kept:
            self._kept_set.add(rel)
        if last.was_deleted:
            self._deleted_set.add(rel)

        self.index = max(0, min(last.index_before, max(0, len(self.images) - 1)))
        self._save_state()
        self.status_var.set(f"Deshecho: {last.kind}.")
        self._schedule_show_current()

    # ------------- Rendering -------------
    def _current_path(self) -> Path | None:
        if not self.images:
            return None
        self.index = max(0, min(self.index, len(self.images) - 1))
        return self.images[self.index]

    def _schedule_show_current(self, delay_ms: int = 0) -> None:
        if self._show_job is not None:
            try:
                self.after_cancel(self._show_job)
            except Exception:
                LOGGER.debug("No se pudo cancelar _show_job", exc_info=True)
            self._show_job = None
        self._show_job = self.after(delay_ms, self._run_show_current)

    def _run_show_current(self) -> None:
        self._show_job = None
        self._show_current()

    def _show_current(self) -> None:
        p = self._current_path()
        if not p:
            self._current_image_path = None
            self._clear_canvas("Sin imágenes")
            self.strip_canvas.delete("all")
            return
        self._display_loading_token += 1
        if self._is_video(p):
            self._current_image_path = None
            if self._video_available:
                self._clear_canvas()
                self._play_video(p)
            else:
                self._stop_video()
                self._clear_canvas(self._video_message(p) + "\nVideo no disponible. Instala VLC y python-vlc.")
                self._show_video_controls(False)
            self._render_strip()
            self.status_var.set(f"{self.index + 1}/{len(self.images)} — Video: {p.name}")
            return
        self._stop_video()
        self._current_image_path = p
        self._request_image_frame(p, token=self._display_loading_token)
        self._render_strip()
        self.status_var.set(f"{self.index + 1}/{len(self.images)} — {p.name} (cargando...)")

    def _redraw_current(self) -> None:
        if self._current_image_path is None:
            return
        self._display_loading_token += 1
        self._request_image_frame(self._current_image_path, token=self._display_loading_token, show_loading=False)

    def _on_canvas_configure(self, _event: tk.Event) -> None:
        if self._is_closing:
            return
        if self._video_path is not None:
            self._set_video_output()
            return
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except Exception:
                LOGGER.debug("No se pudo cancelar _resize_job", exc_info=True)
        self._resize_job = self.after(120, self._redraw_current)

    def _display_cache_key(self, path: Path, max_w: int, max_h: int) -> tuple[Path, int, int]:
        return (path, max(1, max_w // 80), max(1, max_h // 80))

    def _cache_display_image(self, key: tuple[Path, int, int], frame: Image.Image) -> None:
        self._display_cache[key] = frame
        if len(self._display_cache) > 40:
            first = next(iter(self._display_cache))
            self._display_cache.pop(first, None)

    def _draw_image(self, frame: Image.Image) -> None:
        cw = max(1, int(self.canvas.winfo_width()))
        ch = max(1, int(self.canvas.winfo_height()))
        self._photo = ImageTk.PhotoImage(frame)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo, anchor="center")

    def _request_image_frame(self, path: Path, token: int, show_loading: bool = True) -> None:
        max_w = max(1, int(self.canvas.winfo_width()))
        max_h = max(1, int(self.canvas.winfo_height()))
        cache_key = self._display_cache_key(path, max_w, max_h)
        cached = self._display_cache.get(cache_key)
        if cached is not None:
            self._draw_image(cached)
            self.status_var.set(f"{self.index + 1}/{len(self.images)} — {path.name}")
            return

        if show_loading:
            self._clear_canvas("Cargando...")

        future = self._worker.submit(_decode_image_for_view, path, max_w, max_h)

        def _apply() -> None:
            if self._is_closing or token != self._display_loading_token:
                return
            try:
                frame, err = future.result()
            except Exception as exc:
                LOGGER.exception("Error cargando imagen %s", path)
                frame, err = None, str(exc)
            if frame is None:
                self.status_var.set(f"No pude abrir {path.name}: {err or 'error'}")
                self._clear_canvas("Error")
                return
            self._cache_display_image(cache_key, frame)
            self._draw_image(frame)
            if self._current_image_path == path:
                self.status_var.set(f"{self.index + 1}/{len(self.images)} — {path.name}")

        def _dispatch(_fut: object) -> None:
            try:
                self.after(0, _apply)
            except Exception:
                LOGGER.debug("No se pudo despachar render de imagen", exc_info=True)

        future.add_done_callback(_dispatch)

    def _clear_canvas(self, text: str | None = None) -> None:
        self._photo = None
        self.canvas.delete("all")
        cw = max(1, int(self.canvas.winfo_width()))
        ch = max(1, int(self.canvas.winfo_height()))
        if text:
            self.canvas.create_text(
                cw // 2,
                ch // 2,
                text=text,
                fill="white",
                font=("Segoe UI", 16),
                justify="center",
            )

    # ------------- Helpers -------------
    def _rel(self, path: Path) -> str:
        if not self.folder:
            return str(path)
        return _safe_relative(path, self.folder)

    def _is_video(self, path: Path) -> bool:
        return path.suffix.lower() in VIDEO_EXTS

    def _video_message(self, path: Path) -> str:
        try:
            size = self._format_size(path.stat().st_size)
        except Exception:
            LOGGER.debug("No se pudo leer tamano de %s", path, exc_info=True)
            size = "?"
        return f"Video: {path.name}\nTamano: {size}"

    def _format_size(self, bytes_size: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(bytes_size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                return f"{value:.2f} {unit}"
            value /= 1024
        return f"{value:.2f} TB"

    def _format_time_ms(self, ms: int) -> str:
        if ms < 0:
            ms = 0
        seconds = int(ms // 1000)
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins:02d}:{secs:02d}"

    def _show_video_controls(self, show: bool) -> None:
        if show:
            if not self.video_controls.winfo_ismapped():
                self.video_controls.grid()
        else:
            if self.video_controls.winfo_ismapped():
                self.video_controls.grid_remove()

    def _set_video_output(self) -> None:
        if not self._vlc_player:
            return
        try:
            self.update_idletasks()
            handle = self.canvas.winfo_id()
            if sys.platform.startswith("win"):
                self._vlc_player.set_hwnd(handle)
            elif sys.platform == "darwin":
                self._vlc_player.set_nsobject(handle)
            else:
                self._vlc_player.set_xwindow(handle)
        except Exception:
            LOGGER.debug("No se pudo asignar salida de video VLC", exc_info=True)

    def _start_vlc_event_poller(self) -> None:
        if self._vlc_event_job is not None or self._is_closing:
            return
        self._vlc_event_job = self.after(120, self._process_vlc_events)

    def _cancel_vlc_event_poller(self) -> None:
        if self._vlc_event_job is None:
            return
        try:
            self.after_cancel(self._vlc_event_job)
        except Exception:
            LOGGER.debug("No se pudo cancelar poller de VLC", exc_info=True)
        self._vlc_event_job = None

    def _process_vlc_events(self) -> None:
        self._vlc_event_job = None
        if self._is_closing:
            return
        while True:
            try:
                event_name, session_id = self._vlc_events.get_nowait()
            except queue.Empty:
                break
            if event_name == "end":
                self._restart_video_if_current(session_id)
        self._start_vlc_event_poller()

    def _on_vlc_end(self, _event) -> None:
        try:
            self._vlc_events.put(("end", self._video_session))
        except Exception:
            LOGGER.exception("No se pudo registrar evento de fin de video")

    def _restart_video_if_current(self, session_id: int) -> None:
        if self._is_closing or not self._vlc_player or self._video_path is None:
            return
        if session_id != self._video_session:
            return
        try:
            self._vlc_player.set_time(0)
            self._vlc_player.play()
            self.play_pause_text.set("Pausa")
            self._start_video_updates(self._video_session)
        except Exception:
            LOGGER.exception("No se pudo reiniciar video")

    def _play_video(self, path: Path) -> None:
        if not self._vlc_player or not self._vlc_instance:
            return
        if self._video_path and self._video_path == path:
            if not self._vlc_player.is_playing():
                self._vlc_player.play()
            self.play_pause_text.set("Pausa")
            self._show_video_controls(True)
            self._start_video_updates(self._video_session)
            return
        self._stop_video()
        self._video_session += 1
        self._video_path = path
        media = self._vlc_instance.media_new(str(path))
        self._vlc_player.set_media(media)
        self._set_video_output()
        self._vlc_player.play()
        self.play_pause_text.set("Pausa")
        self._show_video_controls(True)
        self._set_volume(self._volume_var.get())
        self._start_video_updates(self._video_session)

    def _stop_video(self) -> None:
        self._video_session += 1
        if self._vlc_player:
            try:
                self._vlc_player.stop()
            except Exception:
                LOGGER.debug("No se pudo detener VLC", exc_info=True)
        self._video_path = None
        self._duration_ms = 0
        self._progress_var.set(0.0)
        self._time_var.set("00:00 / 00:00")
        self._show_video_controls(False)
        self._cancel_video_updates()

    def _start_video_updates(self, session_id: int) -> None:
        self._cancel_video_updates()
        self._video_update_job = self.after(200, lambda: self._update_video_ui(session_id))

    def _cancel_video_updates(self) -> None:
        if self._video_update_job is not None:
            try:
                self.after_cancel(self._video_update_job)
            except Exception:
                LOGGER.debug("No se pudo cancelar _video_update_job", exc_info=True)
        self._video_update_job = None

    def _update_video_ui(self, session_id: int) -> None:
        if (
            not self._vlc_player
            or self._video_path is None
            or self._is_closing
            or session_id != self._video_session
        ):
            return
        try:
            length = self._vlc_player.get_length()
            current = self._vlc_player.get_time()
        except Exception:
            LOGGER.debug("No se pudo leer estado de reproduccion VLC", exc_info=True)
            return
        if length > 0:
            if length != self._duration_ms:
                self._duration_ms = length
                self.progress_scale.configure(to=max(1, length / 1000))
            if not self._seeking:
                self._progress_var.set(current / 1000)
            self._time_var.set(
                f"{self._format_time_ms(current)} / {self._format_time_ms(length)}"
            )
        self._video_update_job = self.after(200, lambda: self._update_video_ui(session_id))

    def _on_seek_start(self, _event: tk.Event) -> None:
        self._seeking = True

    def _on_seek_end(self, _event: tk.Event) -> None:
        if self._vlc_player:
            target = int(self._progress_var.get() * 1000)
            try:
                self._vlc_player.set_time(target)
            except Exception:
                LOGGER.debug("No se pudo ajustar seek de VLC", exc_info=True)
        self._seeking = False

    def _on_seek_change(self, value: str) -> None:
        if not self._seeking:
            return
        try:
            current_ms = int(float(value) * 1000)
        except Exception:
            return
        if self._duration_ms:
            self._time_var.set(
                f"{self._format_time_ms(current_ms)} / {self._format_time_ms(self._duration_ms)}"
            )

    def _set_volume(self, value: int) -> None:
        if self._vlc_player:
            try:
                self._vlc_player.audio_set_volume(int(value))
            except Exception:
                LOGGER.debug("No se pudo ajustar volumen VLC", exc_info=True)

    def _on_volume_change(self, value: str) -> None:
        try:
            vol = int(float(value))
        except Exception:
            return
        self._set_volume(vol)

    def toggle_play_pause(self) -> None:
        if not self._vlc_player:
            return
        if self._vlc_player.is_playing():
            self._vlc_player.pause()
            self.play_pause_text.set("Play")
        else:
            self._vlc_player.play()
            self.play_pause_text.set("Pausa")

    def _unique_target(self, target: Path) -> Path:
        return unique_target_path(target)

    def _request_thumb(
        self,
        path: Path,
        thumb_size: int,
        on_ready: Callable[[ImageTk.PhotoImage], None] | None = None,
    ) -> None:
        key = (path, thumb_size)
        cached = self._thumb_cache.get(key)
        if cached is not None:
            if on_ready:
                on_ready(cached)
            return

        if self._is_video(path):
            if on_ready:
                on_ready(self._thumb_placeholder if thumb_size <= 64 else self._review_thumb_placeholder)
            return

        if on_ready:
            self._thumb_waiters.setdefault(key, []).append(on_ready)
        if key in self._thumb_pending:
            return

        generation = self._media_generation
        self._thumb_pending.add(key)
        future = self._worker.submit(_decode_image_for_thumb, path, thumb_size)

        def _apply() -> None:
            self._thumb_pending.discard(key)
            if self._is_closing or generation != self._media_generation:
                self._thumb_waiters.pop(key, None)
                return
            try:
                frame, _err = future.result()
            except Exception:
                LOGGER.exception("Error creando miniatura de %s", path)
                frame = None
            if frame is None:
                self._thumb_waiters.pop(key, None)
                return
            self._thumb_cache[key] = ImageTk.PhotoImage(frame)
            if len(self._thumb_cache) > 900:
                oldest = next(iter(self._thumb_cache))
                self._thumb_cache.pop(oldest, None)
            callbacks = self._thumb_waiters.pop(key, [])
            for callback in callbacks:
                try:
                    callback(self._thumb_cache[key])
                except Exception:
                    LOGGER.debug("Error aplicando callback de miniatura", exc_info=True)
            self._schedule_strip_render()

        def _dispatch(_fut: object) -> None:
            try:
                self.after(0, _apply)
            except Exception:
                LOGGER.debug("No se pudo despachar miniatura", exc_info=True)

        future.add_done_callback(_dispatch)

    def _schedule_strip_render(self) -> None:
        if self._is_closing:
            return
        if self._strip_render_job is not None:
            return
        self._strip_render_job = self.after(30, self._run_strip_render)

    def _run_strip_render(self) -> None:
        self._strip_render_job = None
        self._render_strip()

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

        kept_set = self._kept_set
        deleted_set = self._deleted_set

        self.strip_canvas.delete("all")
        x = pad // 2
        y = (height - thumb_size) // 2
        for i in range(start, end):
            path = self.images[i]
            rel = self._rel(path)
            key = (path, thumb_size)
            photo = self._thumb_cache.get(key)
            if photo is None:
                photo = self._thumb_placeholder
                if not self._is_video(path):
                    self._request_thumb(path, thumb_size)

            self.strip_canvas.create_image(x, y, image=photo, anchor="nw")
            if self._is_video(path):
                self.strip_canvas.create_text(
                    x + thumb_size // 2,
                    y + thumb_size // 2,
                    text="VID",
                    fill="white",
                    font=("Segoe UI", 9, "bold"),
                )

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
        if self._show_job is not None:
            try:
                self.after_cancel(self._show_job)
            except Exception:
                LOGGER.debug("No se pudo cancelar _show_job al abrir revisión", exc_info=True)
            self._show_job = None
        self._stop_video()
        if self._review_window and self._review_window.winfo_exists():
            self._review_window.lift()
            self._review_window.focus_force()
            return

        review_items: list[tuple[str, Path]] = []
        for rel in sorted(self._deleted_set):
            candidate = self.folder / rel
            if candidate.exists() and candidate.is_file():
                review_items.append((rel, candidate))

        if not review_items:
            self.status_var.set("Fin de revisión. No hay imágenes marcadas para borrar.")
            self._clear_canvas("Revisión completa\nNo hay imágenes marcadas para borrar.")
            self._deleted_set.clear()
            self._save_state()
            return

        win = ctk.CTkToplevel(self)
        self._review_window = win
        win.title("Revisión final de borrado")
        win.geometry("980x680")
        win.minsize(720, 480)
        win.protocol("WM_DELETE_WINDOW", self._close_review_window)

        root = ctk.CTkFrame(win, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(
            root,
            text=(
                "Revisa rápido las imágenes marcadas para borrar. "
                "Desmarca las que quieras conservar y pulsa el botón final."
            ),
            wraplength=640,
            justify="left",
        ).pack(fill="x")

        container = ctk.CTkFrame(root)
        container.pack(fill="both", expand=True, pady=(10, 10))

        canvas = tk.Canvas(container, highlightthickness=0)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ctk.CTkScrollbar(container, orientation="vertical", command=canvas.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.configure(yscrollcommand=scrollbar.set)

        list_frame = ctk.CTkFrame(canvas, fg_color="transparent")
        window_id = canvas.create_window((0, 0), window=list_frame, anchor="nw")
        list_frame.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))

        self._review_selection = {}
        tiles: list[ctk.CTkFrame] = []
        tile_width = 180

        for _idx, (rel, path) in enumerate(review_items, start=1):
            tile = ctk.CTkFrame(list_frame, fg_color="transparent", corner_radius=8, pady=6)
            tiles.append(tile)

            var = tk.BooleanVar(value=True)
            self._review_selection[rel] = var
            ctk.CTkCheckBox(tile, text="", variable=var).pack(anchor="w")

            preview_label = ctk.CTkLabel(
                tile,
                text="Cargando...",
                anchor="center",
                wraplength=150,
                image=self._review_thumb_placeholder,
                compound="center",
            )
            preview_label.pack()
            preview_label.image = self._review_thumb_placeholder
            if self._is_video(path):
                preview_label.configure(text="Video")
            else:
                def _apply_thumb(photo: ImageTk.PhotoImage, label: ctk.CTkLabel = preview_label) -> None:
                    if self._is_closing or not label.winfo_exists():
                        return
                    label.configure(image=photo, text="")
                    label.image = photo

                self._request_thumb(path, 150, on_ready=_apply_thumb)

            ctk.CTkLabel(tile, text=path.name, wraplength=160, justify="center").pack()
            ctk.CTkLabel(tile, text=rel, wraplength=160, justify="center").pack()

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

        buttons = ctk.CTkFrame(root, fg_color="transparent")
        buttons.pack(fill="x")
        ctk.CTkButton(buttons, text="Cerrar", command=self._close_review_window).pack(side="right", padx=8)
        ctk.CTkButton(
            buttons,
            text="Borrar todas",
            command=self._delete_selected_from_review,
        ).pack(side="right", padx=(0, 8))

        self.status_var.set(
            f"Fin de revisión: {len(review_items)} imágenes marcadas. Revisa y confirma el borrado."
        )

    def _close_review_window(self) -> None:
        if self._review_window and self._review_window.winfo_exists():
            self._review_window.destroy()
        self._review_window = None
        self._review_selection = {}

    def _move_rel_paths_to_deleted(self, rel_paths: list[str]) -> tuple[list[str], list[str]]:
        moved: list[str] = []
        failed: list[str] = []
        if not self.folder:
            return moved, failed
        deleted_dir = self._deleted_dir()
        if not deleted_dir:
            return moved, failed
        deleted_dir.mkdir(parents=True, exist_ok=True)

        for rel in rel_paths:
            src = self.folder / rel
            if not src.exists():
                failed.append(f"{rel} (ya no existe)")
                continue
            target = self._unique_target(deleted_dir / src.name)
            try:
                shutil.move(str(src), str(target))
            except Exception as exc:
                LOGGER.exception("No se pudo mover %s a %s", src, target)
                failed.append(f"{rel} ({exc})")
                continue
            moved.append(rel)
        return moved, failed

    def _drop_paths_from_caches(self, moved_rel_paths: set[str]) -> None:
        if not moved_rel_paths or not self.folder:
            return

        thumb_keys = [key for key in self._thumb_cache if self._rel(key[0]) in moved_rel_paths]
        for key in thumb_keys:
            self._thumb_cache.pop(key, None)
            self._thumb_waiters.pop(key, None)
            self._thumb_pending.discard(key)

        display_keys = [key for key in self._display_cache if self._rel(key[0]) in moved_rel_paths]
        for key in display_keys:
            self._display_cache.pop(key, None)

    def _apply_move_results(self, moved: list[str], unselected: list[str] | None = None) -> None:
        moved_set = set(moved)
        self._kept_set, self._deleted_set = update_marks_after_move(
            self._kept_set,
            self._deleted_set,
            moved_set,
            set(unselected or []),
        )
        self.images = [p for p in self.images if self._rel(p) not in moved_set]
        self._drop_paths_from_caches(moved_set)
        if self.index >= len(self.images):
            self.index = max(0, len(self.images) - 1)
        self._save_state()
        self._history.clear()

    def _refresh_after_move(self, moved: list[str], failed: list[str]) -> None:
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
            self._schedule_show_current()
        else:
            self._clear_canvas("Sin imágenes")
            self.strip_canvas.delete("all")

    def _flush_deleted_items(self) -> None:
        if not self.folder:
            return
        if not self._deleted_set:
            messagebox.showinfo("Borrar marcadas", "No hay imágenes marcadas para borrar.")
            return
        if not self._deleted_dir():
            return
        confirmed = messagebox.askyesno(
            "Borrar marcadas",
            f"¿Mover {len(self._deleted_set)} imágenes ya marcadas a {DELETED_DIRNAME}?",
        )
        if not confirmed:
            return
        moved, failed = self._move_rel_paths_to_deleted(sorted(self._deleted_set))
        self._apply_move_results(moved)
        self._close_review_window()
        self._refresh_after_move(moved, failed)

    def _show_about(self) -> None:
        messagebox.showinfo(
            "Acerca de Trash Image Eraser",
            (
                "Revisa imágenes/video y decide qué hacer con cada archivo.\n"
                "- `D` / `Delete`: marcar para borrar.\n"
                "- `K` / `Espacio`: marcar para conservar.\n"
                "- `U`: deshacer última acción.\n"
                "- Flechas `←` / `→`: navegar.\n"
                "- `Borrar marcadas ahora`: mueve ya los archivos con marca de borrado al directorio de eliminados.\n"
                "- El reproductor de video ofrece barra de progreso, pausa y volumen.\n"
                "- El progreso se guarda en `.trash_image_eraser_state.json` dentro de la carpeta revisada."
            ),
        )

    def _delete_selected_from_review(self) -> None:
        if not self.folder:
            return
        if not self._deleted_dir():
            return
        selected = [rel for rel, var in self._review_selection.items() if var.get()]
        unselected = [rel for rel, var in self._review_selection.items() if not var.get()]
        if not selected:
            messagebox.showinfo("Borrado", "No hay imágenes seleccionadas para borrar.")
            return
        if not messagebox.askyesno("Confirmar", f"¿Mover {len(selected)} imágenes a {DELETED_DIRNAME}?"):
            return

        moved, failed = self._move_rel_paths_to_deleted(selected)
        self._apply_move_results(moved, unselected=unselected)
        self._close_review_window()
        self._refresh_after_move(moved, failed)

    def _on_close(self) -> None:
        if messagebox.askokcancel("Salir", "¿Salir de la app?"):
            self._is_closing = True
            self._schedule_state_save(immediate=True)
            if self._resize_job is not None:
                try:
                    self.after_cancel(self._resize_job)
                except Exception:
                    LOGGER.debug("No se pudo cancelar _resize_job al cerrar", exc_info=True)
                self._resize_job = None
            if self._strip_render_job is not None:
                try:
                    self.after_cancel(self._strip_render_job)
                except Exception:
                    LOGGER.debug("No se pudo cancelar _strip_render_job al cerrar", exc_info=True)
                self._strip_render_job = None
            if self._show_job is not None:
                try:
                    self.after_cancel(self._show_job)
                except Exception:
                    LOGGER.debug("No se pudo cancelar _show_job al cerrar", exc_info=True)
                self._show_job = None
            self._cancel_vlc_event_poller()
            self._stop_video()
            self._scan_generation += 1
            self._worker.shutdown(wait=False, cancel_futures=True)
            self._scan_worker.shutdown(wait=False, cancel_futures=True)
            self.destroy()


if __name__ == "__main__":
    App().mainloop()

