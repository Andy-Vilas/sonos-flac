import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path

from .inspector import probe_file, get_audio_specs

logger = logging.getLogger("sonos-flac")


def convert_file(src: Path, dst: Path, target_sample_rate: int, target_bit_depth: int) -> None:
    """Run ffmpeg to convert src → dst (FLAC, downsampled/dithered to target specs)."""
    cmd = [
        "ffmpeg",
        "-y",                    # overwrite dst (temp file)
        "-i", str(src),
        "-af", f"aresample=resampler=soxr:osr={target_sample_rate}",
        "-sample_fmt", "s32",    # 32-bit PCM pipeline for SoX resampler accuracy
        "-c:a", "flac",
        "-compression_level", "8",
        "-bits_per_raw_sample", str(target_bit_depth),
        "-map_metadata", "0",    # preserve all tags
        "-map", "0:a",           # audio stream only (avoids cover art conflicts)
        "-f", "flac",            # explicit format — temp file extension is not .flac
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr[-2000:]}")


def safe_convert_and_replace(
    src: Path,
    temp_suffix: str,
    target_sample_rate: int,
    target_bit_depth: int,
    dry_run: bool,
) -> None:
    """
    Convert src in-place:
    1. Write to a temp file in the same directory
    2. Verify the output meets target specs
    3. Preserve permissions
    4. Atomically replace the original
    On any failure: remove temp file, leave original untouched.
    """
    tmp = src.with_suffix(src.suffix + temp_suffix)

    # Check available disk space (need ~110% of source file size)
    file_size = src.stat().st_size
    free_space = shutil.disk_usage(src.parent).free
    if free_space < file_size * 1.1:
        raise RuntimeError(
            f"Insufficient disk space: need ~{file_size * 1.1 / 1024 / 1024:.1f} MB, "
            f"have {free_space / 1024 / 1024:.1f} MB free"
        )

    if dry_run:
        return

    try:
        convert_file(src, tmp, target_sample_rate, target_bit_depth)

        # Verify output meets target specs
        stream = probe_file(tmp)
        out_rate, out_depth = get_audio_specs(stream)
        if out_rate > target_sample_rate:
            raise RuntimeError(f"Output sample rate {out_rate} still exceeds target {target_sample_rate}")
        if out_depth > 0 and out_depth > target_bit_depth:
            raise RuntimeError(f"Output bit depth {out_depth} still exceeds target {target_bit_depth}")

        # Preserve original file permissions
        original_mode = stat.S_IMODE(src.stat().st_mode)
        os.chmod(tmp, original_mode)

        # Atomic replacement (same directory = same filesystem)
        os.replace(tmp, src)

    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
