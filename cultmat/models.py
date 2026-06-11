from datetime import datetime
from enum import Enum
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Text, Boolean,
    ForeignKey, Table, create_engine
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()

material_tags = Table(
    "material_tags", Base.metadata,
    Column("material_id", Integer, ForeignKey("materials.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)


class MaterialType(str, Enum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    UNKNOWN = "unknown"


class ProcessStatus(str, Enum):
    PENDING = "pending"
    IMPORTED = "imported"
    INSPECTED = "inspected"
    RENAMED = "renamed"
    TAGGED = "tagged"
    CONVERTED = "converted"
    PACKAGED = "packaged"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class Material(Base):
    __tablename__ = "materials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_hash = Column(String(64), index=True)
    original_path = Column(Text, nullable=False)
    current_path = Column(Text, nullable=False)
    file_name = Column(String(500), nullable=False)
    file_ext = Column(String(20))
    file_size = Column(Integer)
    material_type = Column(String(20), default=MaterialType.UNKNOWN)
    mime_type = Column(String(100))
    width = Column(Integer)
    height = Column(Integer)
    duration = Column(Float)
    bitrate = Column(Integer)
    quality_score = Column(Float)
    title = Column(String(500))
    description = Column(Text)
    author = Column(String(200))
    created_date = Column(String(20))
    category = Column(String(100))
    language = Column(String(20))
    copyright_info = Column(Text)
    tags = relationship("Tag", secondary=material_tags, back_populates="materials")
    status = Column(String(20), default=ProcessStatus.PENDING)
    thumbnail_path = Column(Text)
    preview_path = Column(Text)
    watermarked_path = Column(Text)
    converted_path = Column(Text)
    ocr_text = Column(Text)
    transcription = Column(Text)
    errors = Column(Text)
    imported_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    category = Column(String(100))
    description = Column(Text)
    materials = relationship("Material", secondary=material_tags, back_populates="tags")
    created_at = Column(DateTime, default=datetime.now)


class BatchTask(Base):
    __tablename__ = "batch_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_type = Column(String(50), nullable=False)
    name = Column(String(500))
    source_path = Column(Text)
    output_path = Column(Text)
    params = Column(Text)
    status = Column(String(20), default=TaskStatus.PENDING)
    total_items = Column(Integer, default=0)
    processed_items = Column(Integer, default=0)
    failed_items = Column(Integer, default=0)
    current_index = Column(Integer, default=0)
    checkpoint_data = Column(Text)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)


class ProcessLog(Base):
    __tablename__ = "process_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    material_id = Column(Integer, ForeignKey("materials.id"))
    task_id = Column(Integer, ForeignKey("batch_tasks.id"))
    action = Column(String(50), nullable=False)
    success = Column(Boolean, default=True)
    message = Column(Text)
    duration_ms = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)


def init_db(db_path: str) -> sessionmaker:
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)
