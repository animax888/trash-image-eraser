import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from app import (
    has_state_progress,
    resolve_initial_index,
    sanitize_state_payload,
    scan_media_files,
    unique_target_path,
    update_marks_after_move,
)


@contextmanager
def _workspace_tempdir() -> Path:
    base = Path(__file__).resolve().parent / ".tmp"
    base.mkdir(exist_ok=True)
    temp_dir = base / uuid.uuid4().hex
    temp_dir.mkdir()
    try:
        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


class AppLogicTests(unittest.TestCase):
    def test_sanitize_state_payload_removes_missing_and_conflicts(self) -> None:
        with _workspace_tempdir() as folder:
            first = folder / "a.jpg"
            second = folder / "b.jpg"
            first.write_bytes(b"x")
            second.write_bytes(b"x")
            files = [first, second]

            raw_state = {
                "index": 8,
                "kept": ["a.jpg", "missing.jpg"],
                "deleted": ["b.jpg", "a.jpg", "ghost.jpg"],
            }
            sanitized = sanitize_state_payload(raw_state, files, folder)

            self.assertEqual(sanitized["index"], 1)
            self.assertEqual(sanitized["deleted"], ["a.jpg", "b.jpg"])
            self.assertEqual(sanitized["kept"], [])

    def test_has_state_progress_detects_real_progress(self) -> None:
        self.assertFalse(has_state_progress({"index": 0, "kept": [], "deleted": []}))
        self.assertTrue(has_state_progress({"index": 1, "kept": [], "deleted": []}))
        self.assertTrue(has_state_progress({"index": 0, "kept": ["x.jpg"], "deleted": []}))

    def test_resolve_initial_index_prioritizes_state_over_start_path(self) -> None:
        with _workspace_tempdir() as folder:
            files = []
            for name in ("a.jpg", "b.jpg", "c.jpg"):
                path = folder / name
                path.write_bytes(b"x")
                files.append(path)

            state_with_progress = {"index": 1, "kept": [], "deleted": []}
            chosen = resolve_initial_index(files, state_with_progress, files[2])
            self.assertEqual(chosen, 1)

            state_empty = {"index": 0, "kept": [], "deleted": []}
            chosen_from_selected = resolve_initial_index(files, state_empty, files[2])
            self.assertEqual(chosen_from_selected, 2)

    def test_scan_media_files_excludes_deleted_dir(self) -> None:
        with _workspace_tempdir() as folder:
            keep = folder / "keep.jpg"
            keep.write_bytes(b"x")

            nested = folder / "nested" / "photo.png"
            nested.parent.mkdir(parents=True)
            nested.write_bytes(b"x")

            deleted = folder / "_deleted_by_trash_image_eraser"
            deleted.mkdir()
            (deleted / "removed.jpg").write_bytes(b"x")
            (folder / "notes.txt").write_text("ignored", encoding="utf-8")

            results = scan_media_files(folder)
            rel_paths = {str(p.relative_to(folder)).replace("\\", "/") for p in results}
            self.assertEqual(rel_paths, {"keep.jpg", "nested/photo.png"})

    def test_unique_target_path_generates_suffix(self) -> None:
        with _workspace_tempdir() as folder:
            base = folder / "photo.jpg"
            base.write_bytes(b"x")
            (folder / "photo (1).jpg").write_bytes(b"x")
            candidate = unique_target_path(base)
            self.assertEqual(candidate.name, "photo (2).jpg")

    def test_update_marks_after_move_keeps_state_consistent(self) -> None:
        kept = {"keep.jpg", "unselected.jpg", "moved.jpg"}
        deleted = {"delete.jpg", "unselected.jpg", "moved.jpg"}
        moved = {"moved.jpg"}
        unselected = {"unselected.jpg"}

        new_kept, new_deleted = update_marks_after_move(kept, deleted, moved, unselected)
        self.assertNotIn("moved.jpg", new_kept)
        self.assertNotIn("moved.jpg", new_deleted)
        self.assertIn("unselected.jpg", new_kept)
        self.assertNotIn("unselected.jpg", new_deleted)
        self.assertIn("delete.jpg", new_deleted)


if __name__ == "__main__":
    unittest.main()
