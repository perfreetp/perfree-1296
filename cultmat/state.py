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

    def get_last_incomplete_task(self, task_type: str = None) -> Optional[BatchTask]:
        """获取最近一次未完成或失败的同类任务"""
        session = self.get_session()
        try:
            query = session.query(BatchTask).filter(
                BatchTask.task_type == task_type if task_type else True,
                BatchTask.status.in_([
                    TaskStatus.PENDING,
                    TaskStatus.RUNNING,
                    TaskStatus.PAUSED,
                    TaskStatus.FAILED
                ])
            )
            return query.order_by(BatchTask.created_at.desc()).first()
        finally:
            session.close()

    def get_task_by_id(self, task_id: int) -> Optional[BatchTask]:
        """根据ID获取任务"""
        session = self.get_session()
        try:
            return session.query(BatchTask).filter(BatchTask.id == task_id).first()
        finally:
            session.close()

    def list_tasks_filtered(self, task_type: str = None, status: str = None,
                            limit: int = 50) -> List[BatchTask]:
        """按类型/状态筛选任务列表"""
        session = self.get_session()
        try:
            query = session.query(BatchTask)
            if task_type:
                query = query.filter(BatchTask.task_type == task_type)
            if status:
                query = query.filter(BatchTask.status == status)
            return query.order_by(BatchTask.created_at.desc()).limit(limit).all()
        finally:
            session.close()

    def get_task_logs(self, task_id: int, only_failed: bool = False,
                      limit: int = 500) -> List[ProcessLog]:
        """获取任务的处理日志"""
        session = self.get_session()
        try:
            query = session.query(ProcessLog).filter(ProcessLog.task_id == task_id)
            if only_failed:
                query = query.filter(ProcessLog.success == False)
            return query.order_by(ProcessLog.created_at.asc()).limit(limit).all()
        finally:
            session.close()

    def get_task_success_material_ids(self, task_id: int) -> set:
        """获取任务中处理成功的素材ID集合（从日志判断）"""
        session = self.get_session()
        try:
            from sqlalchemy import func
            # 每个素材ID取最后一条日志判断最终状态
            subq = session.query(
                ProcessLog.material_id,
                func.max(ProcessLog.id).label("last_log_id")
            ).filter(
                ProcessLog.task_id == task_id,
                ProcessLog.material_id.isnot(None)
            ).group_by(ProcessLog.material_id).subquery()

            results = session.query(
                ProcessLog.material_id, ProcessLog.success
            ).join(
                subq, ProcessLog.id == subq.c.last_log_id
            ).filter(ProcessLog.success == True).all()
            return {r[0] for r in results}
        except Exception:
            return set()

    def get_task_failed_material_ids(self, task_id: int) -> set:
        """获取任务中处理失败的素材ID集合（从日志判断）"""
        session = self.get_session()
        try:
            from sqlalchemy import func
            subq = session.query(
                ProcessLog.material_id,
                func.max(ProcessLog.id).label("last_log_id")
            ).filter(
                ProcessLog.task_id == task_id,
                ProcessLog.material_id.isnot(None)
            ).group_by(ProcessLog.material_id).subquery()

            results = session.query(
                ProcessLog.material_id
            ).join(
                subq, ProcessLog.id == subq.c.last_log_id
            ).filter(ProcessLog.success == False).all()
            return {r[0] for r in results}
        except Exception:
            return set()

    def get_task_stats_from_logs(self, task_id: int) -> Dict:
        """从日志统计任务的真实成功/失败/跳过数量"""
        success_ids = self.get_task_success_material_ids(task_id)
        failed_ids = self.get_task_failed_material_ids(task_id)
        task = self.get_task_by_id(task_id)
        total = task.total_items or 0 if task else 0
        processed = len(success_ids) + len(failed_ids)
        skipped = max(0, total - processed)
        return {
            "total": total,
            "success": len(success_ids),
            "failed": len(failed_ids),
            "skipped": skipped,
            "processed": processed,
        }

    def delete_tasks(self, task_ids: List[int], delete_logs: bool = True) -> Tuple[int, int]:
        """批量删除任务及其日志
        Returns: (删除的任务数, 删除的日志数)
        """
        session = self.get_session()
        try:
            task_count = 0
            log_count = 0
            for tid in task_ids:
                if delete_logs:
                    logs_deleted = session.query(ProcessLog).filter(
                        ProcessLog.task_id == tid
                    ).delete(synchronize_session=False)
                    log_count += logs_deleted
                t = session.query(BatchTask).filter(BatchTask.id == tid).first()
                if t:
                    session.delete(t)
                    task_count += 1
            session.commit()
            return task_count, log_count
        except Exception:
            session.rollback()
            raise
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
    state.update_task_progress(task_id, checkpoint={"total": len(items), "index": start_index})
    session = state.get_session()
    try:
        task = session.query(BatchTask).filter(BatchTask.id == task_id).first()
        if task and not task.total_items:
            task.total_items = len(items)
            session.commit()
    finally:
        session.close()

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
                              new_task_name: str,
                              resume: bool = False,
                              force_resume_task_id: int = None,
                              **kwargs) -> Tuple[int, bool]:
    """
    获取可续跑的任务，或创建新任务

    Args:
        state: AppState 实例
        task_type: 任务类型
        new_task_name: 新建任务时使用的名称
        resume: 是否启用自动续跑（查找最近一次未完成任务）
        force_resume_task_id: 强制指定任务ID进行续跑（task retry 使用）
        **kwargs: 新建任务时的参数

    Returns:
        (task_id, is_resume): 任务ID, 是否为续跑任务
    """
    resumed_task = None

    if force_resume_task_id is not None:
        session = state.get_session()
        try:
            resumed_task = session.query(BatchTask).filter(
                BatchTask.id == force_resume_task_id
            ).first()
            if resumed_task:
                console.print(
                    f"[yellow]指定任务 #{resumed_task.id}: "
                    f"{resumed_task.name}，将继续执行[/yellow]"
                )
        finally:
            session.close()

    if resumed_task is None and resume:
        last_task = state.get_last_incomplete_task(task_type)
        if last_task:
            resumed_task = last_task
            status_text = {
                TaskStatus.PENDING: "待执行",
                TaskStatus.RUNNING: "进行中",
                TaskStatus.PAUSED: "已暂停",
                TaskStatus.FAILED: "失败",
            }.get(last_task.status, last_task.status)
            console.print(
                f"[yellow]发现{status_text}的任务 #{last_task.id}: "
                f"{last_task.name}，将继续执行[/yellow]"
            )

    if resumed_task:
        resume_marker = f"[续跑#{resumed_task.id}]"
        if resume_marker not in resumed_task.name:
            session = state.get_session()
            try:
                task = session.query(BatchTask).filter(BatchTask.id == resumed_task.id).first()
                if task:
                    task.name = f"{resume_marker} {task.name}"
                    task.status = TaskStatus.RUNNING
                    session.commit()
            finally:
                session.close()
        else:
            session = state.get_session()
            try:
                task = session.query(BatchTask).filter(BatchTask.id == resumed_task.id).first()
                if task:
                    task.status = TaskStatus.RUNNING
                    session.commit()
            finally:
                session.close()

        return resumed_task.id, True

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
