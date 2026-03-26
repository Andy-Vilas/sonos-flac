import json
import logging
import subprocess
from pathlib import Path
from typing import Tuple

logger = logging.getLogger("sonos-flac")


def probe_file(path: Path) -> dict:
    """Return the first audio stream's properties from ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,bits_per_raw_sample,bits_per_sample,codec_name,sample_fmt,channels",
        "-of", "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if not streams:
        raise ValueError("No audio stream found")
    return streams[0]


def get_audio_specs(stream: dict) -> Tuple[int, int]:
    """Return (sample_rate, bit_depth) from a stream dict."""
    sample_rate = int(stream.get("sample_rate", 0))

    # bits_per_raw_sample is the most reliable field; can be absent or "0"
    bit_depth = int(stream.get("bits_per_raw_sample") or 0)

    if bit_depth == 0:
        # Fall back to bits_per_sample (set by some encoders)
        bit_depth = int(stream.get("bits_per_sample") or 0)

    if bit_depth == 0:
        # Infer from sample_fmt: fltp/flt = 32-bit float, s32 = 32, s24 = 24, s16 = 16
        fmt = stream.get("sample_fmt", "")
        fmt_map = {"fltp": 32, "flt": 32, "dblp": 64, "dbl": 64, "s32": 32, "s32p": 32, "s24": 24, "s16": 16, "s16p": 16}
        bit_depth = fmt_map.get(fmt, 0)
        if bit_depth:
            logger.debug("Inferred bit depth %d from sample_fmt '%s'", bit_depth, fmt)
        else:
            logger.warning("Could not determine bit depth for stream (sample_fmt='%s'); assuming compliant", fmt)

    return sample_rate, bit_depth


def needs_conversion(path: Path, target_sample_rate: int, target_bit_depth: int) -> Tuple[bool, int, int]:
    """
    Probe the file and determine if conversion is needed.
    Returns (should_convert, sample_rate, bit_depth).
    Raises RuntimeError/ValueError on probe failure.
    """
    stream = probe_file(path)

    codec = stream.get("codec_name", "")
    if codec != "flac":
        raise ValueError(f"Expected FLAC codec, got '{codec}'")

    sample_rate, bit_depth = get_audio_specs(stream)

    if sample_rate == 0:
        raise ValueError("Could not determine sample rate")

    convert = sample_rate > target_sample_rate or (bit_depth > 0 and bit_depth > target_bit_depth)
    return convert, sample_rate, bit_depth
