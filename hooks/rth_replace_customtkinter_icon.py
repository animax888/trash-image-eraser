"""Deprecated runtime hook.

Icon replacement is now handled at build-time in `trash-image-eraser.spec`.
This file remains only for backwards compatibility with older build scripts.
"""

import logging

logging.getLogger("trash_image_eraser").debug(
    "Runtime hook rth_replace_customtkinter_icon.py is deprecated and does nothing."
)
