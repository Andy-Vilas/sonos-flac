from dataclasses import dataclass
from pathlib import Path
from typing import List

import yaml


@dataclass
class Config:
    mount_points: List[str]
    target_sample_rate: int
    target_bit_depth: int
    log_file: str
    temp_suffix: str
    min_age_seconds: int
    follow_symlinks: bool
    cache_db: str = "/var/lib/sonos-flac/cache.db"


def load_config(path: str) -> Config:
    with open(path) as f:
        data = yaml.safe_load(f)

    required = [
        "mount_points", "target_sample_rate", "target_bit_depth",
        "log_file", "temp_suffix", "min_age_seconds", "follow_symlinks",
    ]
    for key in required:
        if key not in data:
            raise ValueError(f"Missing required config key: {key}")

    if not data["mount_points"]:
        raise ValueError("mount_points must contain at least one entry")

    return Config(
        mount_points=data["mount_points"],
        target_sample_rate=int(data["target_sample_rate"]),
        target_bit_depth=int(data["target_bit_depth"]),
        log_file=data["log_file"],
        temp_suffix=data["temp_suffix"],
        min_age_seconds=int(data["min_age_seconds"]),
        follow_symlinks=bool(data["follow_symlinks"]),
        cache_db=data.get("cache_db", "/var/lib/sonos-flac/cache.db"),
    )
