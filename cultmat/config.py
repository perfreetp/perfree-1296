import os
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Dict, Optional

DEFAULT_CONFIG_DIR = Path.home() / ".cultmat"
DEFAULT_DB_PATH = DEFAULT_CONFIG_DIR / "materials.db"
DEFAULT_PREVIEW_DIR = DEFAULT_CONFIG_DIR / "previews"
DEFAULT_OUTPUT_DIR = Path.cwd() / "output"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".raw", ".heic"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg"}
DOC_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".csv", ".rtf"}

SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | DOC_EXTENSIONS


@dataclass
class RenameRule:
    pattern: str = "{category}_{year}_{month}_{original}"
    use_slug: bool = True
    padding: int = 4
    separator: str = "_"


@dataclass
class WatermarkConfig:
    text: str = "数字文化馆"
    position: str = "bottom-right"
    opacity: float = 0.5
    font_size: int = 36


@dataclass
class ConvertConfig:
    image_format: str = "jpg"
    image_quality: int = 85
    image_max_size: int = 1920
    thumbnail_size: int = 300
    audio_format: str = "mp3"
    audio_bitrate: str = "192k"
    video_format: str = "mp4"
    video_bitrate: str = "2M"


@dataclass
class AppConfig:
    db_path: str = str(DEFAULT_DB_PATH)
    preview_dir: str = str(DEFAULT_PREVIEW_DIR)
    output_dir: str = str(DEFAULT_OUTPUT_DIR)
    dry_run: bool = False
    verbose: bool = False
    workers: int = 4
    rename: RenameRule = field(default_factory=RenameRule)
    watermark: WatermarkConfig = field(default_factory=WatermarkConfig)
    convert: ConvertConfig = field(default_factory=ConvertConfig)
    custom_tags: List[str] = field(default_factory=list)

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "AppConfig":
        config_file = Path(config_path) if config_path else DEFAULT_CONFIG_DIR / "config.json"
        if config_file.exists():
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            rename_data = data.pop("rename", {})
            watermark_data = data.pop("watermark", {})
            convert_data = data.pop("convert", {})
            config = cls(**data)
            config.rename = RenameRule(**rename_data)
            config.watermark = WatermarkConfig(**watermark_data)
            config.convert = ConvertConfig(**convert_data)
            return config
        return cls()

    def save(self, config_path: Optional[str] = None):
        config_file = Path(config_path) if config_path else DEFAULT_CONFIG_DIR / "config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    def ensure_dirs(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.preview_dir).mkdir(parents=True, exist_ok=True)
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
