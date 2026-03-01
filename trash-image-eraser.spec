# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project_root = Path.cwd()
local_vlc = project_root / "dependencias" / "vlc"
if (local_vlc / "libvlc.dll").exists():
    vlc_home = local_vlc
else:
    vlc_home = Path(os.environ.get("VLC_HOME") or r"C:\Program Files\VideoLAN\VLC")

icon_file = project_root / "dependencias" / "media" / "trash_image_eraser.ico"
block_cipher = None

binaries = []
datas = []
if icon_file.exists():
    datas.append((str(icon_file), "."))

runtime_hooks = ["hooks/rth_replace_customtkinter_icon.py"]


def _add_tree(root: Path, prefix: str, datas_list: list[tuple[str, str]]) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parent = path.relative_to(root).parent
        target_dir = Path(prefix) if rel_parent == Path(".") else Path(prefix) / rel_parent
        datas_list.append((str(path), str(target_dir)))

for dll_name in ("libvlc.dll", "libvlccore.dll"):
    candidate = vlc_home / dll_name
    if candidate.exists():
        binaries.append((str(candidate), "vlcbin"))

plugins_src = vlc_home / "plugins"
if plugins_src.exists():
    _add_tree(plugins_src, "vlcbin/plugins", datas)

a = Analysis(
    ["app.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=collect_submodules("customtkinter"),
    hookspath=[],
    runtime_hooks=runtime_hooks,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="trash-image-eraser",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(icon_file) if icon_file.exists() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="trash-image-eraser",
)
