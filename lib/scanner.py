import logging
import os
import time
from pathlib import Path
from typing import Generator, Set, Tuple

logger = logging.getLogger("sonos-flac")


def scan_for_flac(
    root: str,
    follow_symlinks: bool,
    min_age_seconds: int,
    temp_suffix: str,
) -> Generator[Path, None, None]:
    """Yield .flac file paths under root that pass safety checks."""
    seen_inodes: Set[Tuple[int, int]] = set()
    now = time.time()

    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        # Clean up stale temp files from prior failed runs
        for name in filenames:
            if name.endswith(temp_suffix):
                stale = Path(dirpath) / name
                try:
                    stale.unlink()
                    logger.warning("CLEANUP  Removed stale temp file: %s", stale)
                except OSError as e:
                    logger.error("CLEANUP  Could not remove stale temp file %s: %s", stale, e)

        for name in filenames:
            if not name.lower().endswith(".flac"):
                continue

            path = Path(dirpath) / name

            # Skip symlinks unless follow_symlinks is enabled
            if path.is_symlink() and not follow_symlinks:
                logger.debug("SKIP     %s — symlink (follow_symlinks=false)", path)
                continue

            try:
                st = path.stat()
            except OSError as e:
                logger.error("SKIP     %s — stat failed: %s", path, e)
                continue

            # Dedup hardlinks
            inode_key = (st.st_dev, st.st_ino)
            if inode_key in seen_inodes:
                logger.debug("SKIP     %s — duplicate inode (hardlink)", path)
                continue
            seen_inodes.add(inode_key)

            # Skip empty files
            if st.st_size == 0:
                logger.warning("SKIP     %s — empty file", path)
                continue

            # Skip recently modified files (active rip guard)
            age = now - st.st_mtime
            if age < min_age_seconds:
                logger.warning("SKIP     %s — modified %.0fs ago (< %ds threshold)", path, age, min_age_seconds)
                continue

            # Skip unreadable files
            if not os.access(path, os.R_OK):
                logger.error("SKIP     %s — not readable", path)
                continue

            yield path
