import hashlib
import os
import re
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any
from slugify import slugify

from .config import (
    IMAGE_EXTENSIONS, AUDIO_EXTENSIONS, VIDEO_EXTENSIONS, DOC_EXTENSIONS
)
from .models import MaterialType


def compute_file_hash(filepath: str, algo: str = "sha256", chunk_size: int = 8192) -> str:
    h = hashlib.new(algo)
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def detect_material_type(filepath: str) -> str:
    ext = Path(filepath).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return MaterialType.IMAGE
    elif ext in AUDIO_EXTENSIONS:
        return MaterialType.AUDIO
    elif ext in VIDEO_EXTENSIONS:
        return MaterialType.VIDEO
    elif ext in DOC_EXTENSIONS:
        return MaterialType.DOCUMENT
    return MaterialType.UNKNOWN


def human_readable_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def human_readable_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}秒"
    elif seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}分{s}秒"
    else:
        h, rem = divmod(int(seconds), 3600)
        m, s = divmod(rem, 60)
        return f"{h}时{m}分{s}秒"


def safe_filename(name: str, use_slug: bool = True, separator: str = "_") -> str:
    if not name:
        return "untitled"
    if use_slug:
        return slugify(name, separator=separator, allow_unicode=True)
    name = re.sub(r'[\\/:*?"<>|]', separator, name)
    name = re.sub(r'\s+', separator, name)
    return name.strip(separator) or "untitled"


def scan_directory(directory: str, recursive: bool = True, extensions: Optional[set] = None) -> List[str]:
    directory = Path(directory)
    if not directory.exists():
        return []
    results = []
    if recursive:
        iterator = directory.rglob("*")
    else:
        iterator = directory.glob("*")
    for item in iterator:
        if item.is_file():
            if extensions is None or item.suffix.lower() in extensions:
                results.append(str(item))
    return sorted(results)


def parse_date_from_filename(filename: str) -> Optional[str]:
    patterns = [
        r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})",
        r"(20\d{2})[-_]?(\d{2})",
        r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})",
    ]
    for pattern in patterns:
        m = re.search(pattern, filename)
        if m:
            groups = m.groups()
            if len(groups) == 3:
                return f"{groups[0]}-{groups[1]}-{groups[2]}"
            elif len(groups) == 2:
                return f"{groups[0]}-{groups[1]}"
    return None


def get_file_metadata(filepath: str) -> Dict[str, Any]:
    path = Path(filepath)
    stat = path.stat()
    return {
        "name": path.name,
        "stem": path.stem,
        "suffix": path.suffix.lower(),
        "size": stat.st_size,
        "created": datetime.fromtimestamp(stat.st_ctime),
        "modified": datetime.fromtimestamp(stat.st_mtime),
    }


def ensure_unique_path(filepath: str) -> str:
    path = Path(filepath)
    if not path.exists():
        return filepath
    counter = 1
    while True:
        new_path = path.parent / f"{path.stem}_{counter}{path.suffix}"
        if not new_path.exists():
            return str(new_path)
        counter += 1


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def json_loads(text: str) -> Any:
    try:
        return json.loads(text) if text else None
    except (json.JSONDecodeError, TypeError):
        return None
