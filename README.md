# sonos-flac

Scans SMB-mounted music shares for FLAC files that exceed Sonos's maximum supported spec (24-bit / 48kHz) and converts them in-place using ffmpeg. Designed to run as a nightly systemd timer on a Rocky Linux VM with the NAS shares mounted via CIFS.

## How it works

1. Walks each configured mount point recursively for `.flac` files
2. Uses `ffprobe` to inspect sample rate and bit depth
3. Converts non-compliant files with `ffmpeg` (SoX resampler, lossless FLAC output)
4. Atomically replaces the original only after verifying the converted file meets the target spec
5. Logs every action — scanned, skipped, converted, failed — with a summary at the end

Originals are never deleted if a conversion fails. A `--dry-run` mode reports what would be converted without touching any files.

## Requirements

- Python 3.8+
- `ffmpeg` and `ffprobe` (must be built with `libsoxr` support — available via RPMFusion on Rocky Linux)
- `cifs-utils` (for mounting SMB shares)
- `pyyaml`

```bash
# Enable EPEL and RPMFusion (ffmpeg is not in the default Rocky Linux repos)
sudo dnf install epel-release
sudo dnf install --nogpgcheck \
  https://mirrors.rpmfusion.org/free/el/rpmfusion-free-release-$(rpm -E %rhel).noarch.rpm \
  https://mirrors.rpmfusion.org/nonfree/el/rpmfusion-nonfree-release-$(rpm -E %rhel).noarch.rpm

# Enable CRB repo — required to satisfy the ladspa dependency in the ffmpeg chain
sudo dnf config-manager --enable crb
sudo dnf install ladspa

sudo dnf install ffmpeg cifs-utils python3-pip
pip3 install -r requirements.txt
```

> **Note:** if `pip3` is not found after installing `python3-pip`, run `hash -r` to refresh your shell's command cache, or use `python3 -m pip` as a drop-in replacement.

## Setup

### 1. Clone and install

```bash
git clone <repo-url> /opt/sonos-flac
cd /opt/sonos-flac
pip3 install -r requirements.txt
```

### 2. Mount the NAS shares

Add entries to `/etc/fstab` for each Synology NAS share:

```
//192.168.1.10/music  /mnt/nas1/music  cifs  credentials=/etc/.nas.cred,uid=1000,gid=1000,file_mode=0664,dir_mode=0775,iocharset=utf8,_netdev,nofail,vers=2.0  0  0
//192.168.1.11/music  /mnt/nas2/music  cifs  credentials=/etc/.nas.cred,uid=1000,gid=1000,file_mode=0664,dir_mode=0775,iocharset=utf8,_netdev,nofail,vers=2.0  0  0
```

Create a shared credentials file (owned by root, mode 600):

```
# /etc/.nas.cred
username=mediauser
password=yourpassword
domain=WORKGROUP
```

```bash
chmod 600 /etc/.nas.cred
mount -a
```

Key mount options:
- `_netdev` — wait for network before mounting
- `nofail` — don't block boot if the NAS is unreachable

**SELinux note:** Rocky Linux enforces SELinux by default. CIFS mounts may need a file context label so the service can read and write them. Either add `context=system_u:object_r:samba_share_t:s0` to each fstab entry, or allow the policy boolean:

```bash
setsebool -P use_samba_home_dirs 1
```

If `ffmpeg` or the log directory throw permission denials, check `ausearch -m avc -ts recent` and use `audit2allow` to generate a local policy module if needed.

### 3. Configure

Edit `config.yaml`:

```yaml
mount_points:
  - /mnt/nas1/music
  - /mnt/nas2/music

target_sample_rate: 48000   # Hz — Sonos maximum
target_bit_depth: 24        # bits — Sonos maximum

log_file: /var/log/sonos-flac/conversion.log
temp_suffix: .sonosconvert.tmp

# Skip files modified within this many seconds (guards against active rips)
min_age_seconds: 60

# Set to true to follow symlinks when walking directories
follow_symlinks: false
```

### 4. Test with a dry run

```bash
python3 /opt/sonos-flac/sonos_flac.py --config /opt/sonos-flac/config.yaml --dry-run --verbose
```

This scans and reports what would be converted without modifying any files.

### 5. Schedule with systemd

```bash
cp /opt/sonos-flac/sonos-flac.service /etc/systemd/system/
cp /opt/sonos-flac/sonos-flac.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sonos-flac.timer
```

The timer runs nightly at 2 AM. `Persistent=true` means it will catch up on missed runs after a reboot.

Check timer status:

```bash
systemctl list-timers sonos-flac.timer
journalctl -u sonos-flac.service
```

## Usage

```
python3 sonos_flac.py --config config.yaml [--dry-run] [--verbose]
```

| Flag | Description |
|------|-------------|
| `--config PATH` | Path to config file (default: `config.yaml`) |
| `--dry-run` | Report what would be converted; make no changes |
| `--verbose` | Log every scanned and skipped file (very noisy on large libraries) |

## Log format

```
2026-03-26T02:01:05 [INFO ] MOUNT    Scanning /mnt/nas1/music
2026-03-26T02:01:07 [INFO ] CONVERT  /mnt/nas1/music/Artist/Album/track.flac — 32bit/96000Hz → 24bit/48000Hz
2026-03-26T02:01:22 [ERROR] FAIL     /mnt/nas1/music/Artist/Album/bad.flac — ffmpeg failed: ...
2026-03-26T02:03:00 [INFO ] SUMMARY  scanned=412 converted=38 skipped=371 errors=1
```

Logs rotate at 10 MB, keeping 5 backups.

## Safety features

- **Atomic replacement** — converts to a temp file in the same directory, verifies it, then renames over the original
- **Original preserved on failure** — temp file is deleted and original is untouched if anything goes wrong
- **Disk space check** — skips conversion if free space is less than 110% of the source file size
- **Active-file guard** — skips files modified within `min_age_seconds` (protects files being actively ripped)
- **PID lock** — prevents two instances from running concurrently against the same shares
- **Stale temp cleanup** — removes any `.sonosconvert.tmp` files left by a previously interrupted run
- **Hardlink dedup** — won't process the same inode twice if hardlinks exist across the tree

## Project structure

```
sonos_flac.py          Entry point — orchestrates scan/convert loop
config.yaml            Configuration
requirements.txt       Python dependencies
sonos-flac.service     Systemd service unit
sonos-flac.timer       Systemd timer unit
lib/
  config.py            YAML config loader and validation
  scanner.py           Recursive .flac file discovery with safety checks
  inspector.py         ffprobe wrapper — detects non-compliant files
  converter.py         ffmpeg wrapper — converts and atomically replaces
  logger.py            Rotating log file setup
```

## Conversion details

ffmpeg command used:

```
ffmpeg -y -i <source> \
  -af aresample=resampler=soxr:osr=48000 \
  -sample_fmt s32 \
  -c:a flac \
  -compression_level 8 \
  -bits_per_raw_sample 24 \
  -map_metadata 0 \
  -map 0:a \
  <output>
```

- **SoX resampler** (`libsoxr`) provides high-quality anti-aliased downsampling
- **32-bit PCM intermediate** preserves precision through the resampling pipeline before the FLAC encoder quantises to 24-bit
- **`-map_metadata 0`** preserves all Vorbis comment tags (artist, album, track, etc.)
- **`-map 0:a`** selects the audio stream only, avoiding conflicts with embedded cover art streams

## License

MIT
