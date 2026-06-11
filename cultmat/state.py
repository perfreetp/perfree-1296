import json
from datetime import datetime
from typing import Optional, List, Callable, Any, Dict, Tuple
from pathlib import Path
from tqdm import tqdm
from rich.console import Console

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session as SASession

from .config import AppConfig
from .models import (
    init_db, Material, Tag, BatchTask, ProcessLog,
    ProcessStatus, TaskStatus
)
from .utils import json_dumps, json_loads

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

console = Console()


class AppState:
    def __init__(self, config: AppConfig):
        self.config = config
        config.ensure_dirs()
        self.SessionLocal = init_db(config.db_path)

    def get_session(self) -> "SASession":
        return self.SessionLocal()

    def get_or_create_tag(self, name: str, category: str = None, description: str = None) -> Tag:
        session = self.get_session()
        try:
            tag = session.query(Tag).filter(Tag.name == name).first()
            if not tag:
                tag = Tag(name=name, category=category, description=description)
                session.add(tag)
                session.commit()
                session.refresh(tag)
            return tag
        finally:
            session.close()

    def add_tags_to_material(self, material_id: int, tag_names: List[str]):
        session = self.get_session()
        try:
            material = session.query(Material).filter(Material.id == material_id).first()
            if material:
                for name in tag_names:
                    tag = self.get_or_create_tag(name)
                    if tag not in material.tags:
                        material.tags.append(tag)
                session.commit()
        finally:
            session.close()

    def create_batch_task(self, task_type: str, name: str, source_path: str = None,
                          output_path: str = None, params: Dict = None) -> int:
        session = self.get_session()
        try:
            task = BatchTask(
                task_type=task_type,
                name=name,
                source_path=source_path,
                output_path=output_path,
                params=json_dumps(params) if params else None,
                status=TaskStatus.PENDING,
            )
            session.add(task)
            session.commit()
            session.refresh(task)
            return task.id
        finally:
            session.close()

    def update_task_progress(self, task_id: int, processed: int = None, failed: int = None,
                             current_index: int = None, status: str = None,
                             checkpoint: Dict = None):
        session = self.get_session()
        try:
            task = session.query(BatchTask).filter(BatchTask.id == task_id).first()
            if task:
                if processed is not None:
                    task.processed_items = processed
                if failed is not None:
                    task.failed_items = failed
                if current_index is not None:
                    task.current_index = current_index
                if status:
                    task.status = status
                    if status == TaskStatus.RUNNING and not task.started_at:
                        task.started_at = datetime.now()
                    if status == TaskStatus.COMPLETED:
                        task.completed_at = datetime.now()
                if checkpoint is not None:
                    task.checkpoint_data = json_dumps(checkpoint)
                session.commit()
        finally:
            session.close()

    def get_task_checkpoint(self, task_id: int) -> Optional[Dict]:
        session = self.get_session()
        try:
            task = session.query(BatchTask).filter(BatchTask.id == task_id).first()
            if task and task.checkpoint_data:
                return json_loads(task.checkpoint_data)
            return None
        finally:
            session.close()

    def log_process(self, material_id: int = None, task_id: int = None,
                    action: str = None, success: bool = True,
                    message: str = None, duration_ms: int = None):
        session = self.get_session()
        try:
            log = ProcessLog(
                material_id=material_id,
                task_id=task_id,
                action=action,
                success=success,
                message=message,
                duration_ms=duration_ms,
            )
            session.add(log)
            session.commit()
        finally:
            session.close()

    def update_material_status(self, material_id: int, status: str, errors: str = None):
        session = self.get_session()
        try:
            material = session.query(Material).filter(Material.id == material_id).first()
            if material:
                material.status = status
                if errors:
                    material.errors = errors
                material.updated_at = datetime.now()
                session.commit()
        finally:
            session.close()

    def list_tasks(self, status: str = None, limit: int = 20) -> List[BatchTask]:
        session = self.get_session()
        try:
            query = session.query(BatchTask)
            if status:
                query = query.filter(BatchTask.status == status)
            return query.order_by(BatchTask.created_at.desc()).limit(limit).all()
        finally:
            session.close()

    def get_paused_task(self, task_type: str = None) -> Optional[BatchTask]:
        session = self.get_session()
        try:
            query = session.query(BatchTask).filter(BatchTask.status == TaskStatus.PAUSED)
            if task_type:
                query = query.filter(BatchTask.task_type == task_type)
            return query.order_by(BatchTask.created_at.desc()).first()
        finally:
            session.close()


def run_batch(state: AppState, task_id: int, items: List[Any],
              processor: Callable[[Any, int], bool],
              description: str = "处理中", resume: bool = False,
              skip_check: Callable[[Any], bool] = None,
              checkpoint_callback: Callable[[], Dict] = None) -> Dict:
    """
    批量处理任务

    Args:
        state: AppState实例
        task_id: 任务ID
        items: 待处理项目列表
        processor: 处理函数，返回bool表示是否成功
        description: 进度条描述
        resume: 是否断点续跑
        skip_check: 可选的跳过检查函数，返回True表示该项目已处理可跳过
        checkpoint_callback: 可选的回调函数，返回额外要保存到checkpoint的数据
    """
    session = state.get_session()
    try:
        task = session.query(BatchTask).filter(BatchTask.id == task_id).first()
        if not task:
            return {"total": 0, "success": 0, "failed": 0}
    finally:
        session.close()

    start_index = 0
    success_count = 0
    failed_count = 0
    skipped_count = 0

    if resume:
        checkpoint = state.get_task_checkpoint(task_id)
        if checkpoint:
            start_index = checkpoint.get("index", 0)
            success_count = checkpoint.get("success", 0)
            failed_count = checkpoint.get("failed", 0)
            skipped_count = checkpoint.get("skipped", 0)

    state.update_task_progress(task_id, status=TaskStatus.RUNNING,
                               processed=success_count, failed=failed_count,
                               current_index=start_index)

    progress_bar = tqdm(
        items[start_index:],
        desc=description,
        initial=start_index,
        total=len(items),
        disable=state.config.dry_run
    )

    for i, item in enumerate(progress_bar, start=start_index):
        try:
            if skip_check and skip_check(item):
                skipped_count += 1
                if state.config.verbose:
                    name = getattr(item, 'file_name', str(item))
                    console.print(f"[dim]跳过已处理: {name}[/dim]")
                state.log_process(
                    material_id=getattr(item, 'id', None), task_id=task_id,
                    action="skip", success=True, message="断点续跑，跳过已处理"
                )
                continue

            ok = processor(item, task_id)
            if ok:
                success_count += 1
            else:
                failed_count += 1
        except Exception as e:
            failed_count += 1
            if state.config.verbose:
                console.print(f"[red]错误: {e}[/red]")

        if (i + 1) % 10 == 0 or i == len(items) - 1:
            checkpoint = {
                "index": i + 1,
                "success": success_count,
                "failed": failed_count,
                "skipped": skipped_count,
            }
            if checkpoint_callback:
                try:
                    extra = checkpoint_callback()
                    if extra:
                        checkpoint.update(extra)
                except Exception as e:
                    console.print(f"[dim]checkpoint回调异常: {e}[/dim]")
            state.update_task_progress(
                task_id, processed=success_count, failed=failed_count,
                current_index=i + 1, checkpoint=checkpoint
            )

    final_status = TaskStatus.COMPLETED if failed_count == 0 else TaskStatus.FAILED
    state.update_task_progress(
        task_id, processed=success_count, failed=failed_count,
        status=final_status
    )

    return {
        "total": len(items),
        "success": success_count,
        "failed": failed_count,
        "skipped": skipped_count,
    }


def get_or_create_resume_task(state: AppState, task_type: str,
                              new_task_name: str, **kwargs) -> Tuple[int, bool]:
    """
    获取可续跑的任务，或创建新任务

    Returns:
        (task_id, is_resume): 任务ID, 是否为续跑任务
    """
    paused_task = state.get_paused_task(task_type)
    if paused_task:
        console.print(f"[yellow]发现未完成的任务 #{paused_task.id}: {paused_task.name}，将继续执行[/yellow]")
        return paused_task.id, True

    params = kwargs.pop("params", None)
    source_path = kwargs.pop("source_path", None)
    output_path = kwargs.pop("output_path", None)

    task_id = state.create_batch_task(
        task_type, new_task_name,
        source_path=source_path,
        output_path=output_path,
        params=params
    )
    return task_id, False


def find_material_by_hash(state: AppState, file_hash: str) -> Optional[Material]:
    session = state.get_session()
    try:
        return session.query(Material).filter(Material.file_hash == file_hash).first()
    finally:
        session.close()


def find_material_by_path(state: AppState, filepath: str) -> Optional[Material]:
    session = state.get_session()
    try:
        return session.query(Material).filter(
            (Material.original_path == filepath) | (Material.current_path == filepath)
        ).first()
    finally:
        session.close()


def save_material(state: AppState, material: Material) -> Material:
    session = state.get_session()
    try:
        if material.id:
            existing = session.query(Material).filter(Material.id == material.id).first()
            if existing:
                for key, value in material.__dict__.items():
                    if not key.startswith("_"):
                        setattr(existing, key, value)
                existing.updated_at = datetime.now()
                session.commit()
                session.refresh(existing)
                return existing
        session.add(material)
        session.commit()
        session.refresh(material)
        return material
    finally:
        session.close()
