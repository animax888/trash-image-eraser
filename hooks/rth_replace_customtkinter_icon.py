import shutil
import sys
from pathlib import Path


def _replace_ctk_icon() -> None:
    if not getattr(sys, "frozen", False):
        return

    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return

    base = Path(meipass)
    src = base / "trash_image_eraser.ico"
    dest = base / "customtkinter" / "assets" / "icons" / "CustomTkinter_icon_Windows.ico"

    try:
        if not src.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    except Exception:
        pass


_replace_ctk_icon()
