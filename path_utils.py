import os
import re


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_FILENAME_LENGTH = 240


def sanitize_filename(name):
    clean_name = INVALID_FILENAME_CHARS.sub("_", str(name)).strip()
    clean_name = clean_name.rstrip(". ")

    if not clean_name:
        clean_name = "download"

    root, ext = os.path.splitext(clean_name)
    if root.upper() in WINDOWS_RESERVED_NAMES:
        clean_name = f"{root}_{ext}"

    if len(clean_name) > MAX_FILENAME_LENGTH:
        root, ext = os.path.splitext(clean_name)
        keep = max(1, MAX_FILENAME_LENGTH - len(ext))
        clean_name = f"{root[:keep]}{ext}"

    return clean_name


def unique_path(directory, name, reserved_paths=None):
    if reserved_paths is None:
        reserved_paths = set()

    safe_name = sanitize_filename(name)
    root, ext = os.path.splitext(safe_name)
    candidate = os.path.join(directory, safe_name)
    index = 1

    key = os.path.normcase(os.path.abspath(candidate))
    while key in reserved_paths or os.path.exists(candidate):
        candidate = os.path.join(directory, f"{root} ({index}){ext}")
        key = os.path.normcase(os.path.abspath(candidate))
        index += 1

    reserved_paths.add(key)
    return candidate
