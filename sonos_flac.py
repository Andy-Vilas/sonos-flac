#!/usr/bin/env python3
"""
sonos_flac.py — Scan SMB-mounted music shares and convert high-bitrate FLAC
files to 24-bit/48kHz for Sonos compatibility.

Usage:
    python3 sonos_flac.py --config config.yaml [--dry-run] [--verbose]
"""

import argparse
import errno
import logging
import os
import shutil
import sys
from pathlib import Path

PID_FILE = "/var/run/sonos-flac.pid"


def parse_args():
    p = argparse.ArgumentParser(description="Convert high-bitrate FLACs for Sonos compatibility")
    p.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    p.add_argument("--dry-run", action="store_true", help="Report what would be converted without making changes")
    p.add_argument("--verbose", action="store_true", help="Log skipped files and extra detail")
    return p.parse_args()


def acquire_pid_lock(pid_file: str) -> None:
    """Write PID file; raise if another instance is already running."""
    pid_path = Path(pid_file)
    pid_path.parent.mkdir(parents=True, exist_ok=True)

    if pid_path.exists():
        try:
            existing_pid = int(pid_path.read_text().strip())
            # Check if that process is still alive
            os.kill(existing_pid, 0)
            raise SystemExit(f"Another instance is already running (PID {existing_pid}). Exiting.")
        except (ValueError, ProcessLookupError, PermissionError):
            # Stale PID file — safe to overwrite
            pass

    pid_path.write_text(str(os.getpid()))


def release_pid_lock(pid_file: str) -> None:
    try:
        Path(pid_file).unlink(missing_ok=True)
    except OSError:
        pass


def check_dependencies() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    if missing:
        raise SystemExit(f"Required tools not found in PATH: {', '.join(missing)}\nInstall with: apt install ffmpeg")


def process_mount_point(mount_point: str, cfg, dry_run: bool, verbose: bool, log) -> dict:
    from lib.scanner import scan_for_flac
    from lib.inspector import needs_conversion
    from lib.converter import safe_convert_and_replace

    counts = {"scanned": 0, "converted": 0, "skipped": 0, "errors": 0}

    if not os.path.ismount(mount_point):
        log.error("MOUNT    %s — not mounted, skipping", mount_point)
        counts["errors"] += 1
        return counts

    try:
        # Quick access check
        os.listdir(mount_point)
    except OSError as e:
        log.error("MOUNT    %s — not accessible: %s", mount_point, e)
        counts["errors"] += 1
        return counts

    log.info("MOUNT    Scanning %s", mount_point)

    for path in scan_for_flac(
        mount_point,
        follow_symlinks=cfg.follow_symlinks,
        min_age_seconds=cfg.min_age_seconds,
        temp_suffix=cfg.temp_suffix,
    ):
        counts["scanned"] += 1

        if verbose:
            log.debug("SCAN     %s", path)

        try:
            convert, sample_rate, bit_depth = needs_conversion(
                path, cfg.target_sample_rate, cfg.target_bit_depth
            )
        except (RuntimeError, ValueError) as e:
            log.error("FAIL     %s — probe error: %s", path, e)
            counts["errors"] += 1
            continue
        except OSError as e:
            if e.errno in (errno.ESTALE, errno.EIO):
                log.error("FAIL     %s — NAS I/O error (ESTALE/EIO): %s", path, e)
            else:
                log.error("FAIL     %s — OS error: %s", path, e)
            counts["errors"] += 1
            continue

        if not convert:
            log.debug("SKIP     %s — already within spec (%dbit/%dHz)", path, bit_depth, sample_rate)
            counts["skipped"] += 1
            continue

        depth_str = f"{bit_depth}bit" if bit_depth else "?bit"
        log.info(
            "CONVERT%s %s — %s/%dHz → %dbit/%dHz",
            " [DRY]" if dry_run else "      ",
            path,
            depth_str,
            sample_rate,
            cfg.target_bit_depth,
            cfg.target_sample_rate,
        )

        if not dry_run:
            try:
                safe_convert_and_replace(
                    path,
                    temp_suffix=cfg.temp_suffix,
                    target_sample_rate=cfg.target_sample_rate,
                    target_bit_depth=cfg.target_bit_depth,
                    dry_run=dry_run,
                )
                counts["converted"] += 1
            except RuntimeError as e:
                log.error("FAIL     %s — conversion error: %s", path, e)
                counts["errors"] += 1
            except OSError as e:
                if e.errno in (errno.ESTALE, errno.EIO):
                    log.error("FAIL     %s — NAS I/O error during conversion: %s", path, e)
                else:
                    log.error("FAIL     %s — OS error during conversion: %s", path, e)
                counts["errors"] += 1
        else:
            counts["converted"] += 1

    return counts


def main():
    args = parse_args()

    # Import here so config/logger modules are in lib/
    sys.path.insert(0, str(Path(__file__).parent))
    from lib.config import load_config
    from lib.logger import setup_logger

    try:
        cfg = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(f"Config error: {e}")

    log = setup_logger(cfg.log_file, verbose=args.verbose)

    if args.dry_run:
        log.info("=== DRY RUN — no files will be modified ===")

    check_dependencies()

    try:
        acquire_pid_lock(PID_FILE)
    except SystemExit as e:
        log.error("%s", e)
        sys.exit(1)

    totals = {"scanned": 0, "converted": 0, "skipped": 0, "errors": 0}

    try:
        for mount_point in cfg.mount_points:
            counts = process_mount_point(mount_point, cfg, args.dry_run, args.verbose, log)
            for k in totals:
                totals[k] += counts[k]
    finally:
        release_pid_lock(PID_FILE)

    log.info(
        "SUMMARY  scanned=%d converted=%d skipped=%d errors=%d%s",
        totals["scanned"],
        totals["converted"],
        totals["skipped"],
        totals["errors"],
        " [DRY RUN]" if args.dry_run else "",
    )

    sys.exit(1 if totals["errors"] > 0 else 0)


if __name__ == "__main__":
    main()
