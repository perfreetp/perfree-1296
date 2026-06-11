import os
import shutil
from pathlib import Path
from typing import Optional
import click
from rich.console import Console
from rich.table import Table

from ..config import AppConfig, SUPPORTED_EXTENSIONS
from ..state import AppState, run_batch, find_material_by_hash, find_material_by_path, save_material, get_or_create_resume_task
from ..models import Material, ProcessStatus, MaterialType
from ..utils import compute_file_hash, scan_directory, detect_material_type, get_file_metadata, human_readable_size

console = Console()


@click.command()
@click.argument("source", type=click.Path(exists=True))
@click.option("--recursive/--no-recursive", default=True, help="是否递归扫描子目录")
@click.option("--copy/--no-copy", default=False, help="是否复制到输出目录")
@click.option("--move/--no-move", default=False, help="是否移动到输出目录")
@click.option("--output-dir", "-o", type=click.Path(), help="导入输出目录")
@click.option("--deduplicate/--no-deduplicate", default=True, help="检测并跳过重复文件")
@click.option("--dry-run", is_flag=True, help="试运行，不实际执行")
@click.option("--resume", is_flag=True, help="从上次中断处继续")
@click.option("--force-task-id", "_force_task_id", type=int, hidden=True, default=None)
@click.pass_context
def import_cmd(ctx, source, recursive, copy, move, output_dir, deduplicate, dry_run, resume, _force_task_id):
    """扫描目录并导入素材文件"""
    config: AppConfig = ctx.obj["config"]
    state: AppState = ctx.obj["state"]
    config.dry_run = dry_run or config.dry_run

    if not output_dir:
        output_dir = str(Path(config.output_dir) / "imported")

    source_path = Path(source)
    if source_path.is_file():
        files = [str(source_path)]
    else:
        files = scan_directory(str(source_path), recursive=recursive, extensions=SUPPORTED_EXTENSIONS)

    if not files:
        console.print("[yellow]未找到支持的素材文件[/yellow]")
        return

    console.print(f"[green]发现 {len(files)} 个文件[/green]")

    duplicates = []

    task_name = f"导入素材: {source}"
    if resume and not _force_task_id:
        task_name = "[续跑] " + task_name

    task_id, is_resume = get_or_create_resume_task(
        state, "import", task_name,
        resume=resume,
        force_resume_task_id=_force_task_id,
        source_path=source, output_path=output_dir,
        params={"recursive": recursive, "copy": copy, "move": move, "deduplicate": deduplicate}
    )

    # task retry 时沿用原输出目录
    if _force_task_id and is_resume:
        orig_task = state.get_task_by_id(_force_task_id)
        if orig_task and orig_task.output_path:
            if str(output_dir) != orig_task.output_path:
                output_dir = orig_task.output_path
                console.print(f"[cyan]指定任务 #{_force_task_id} 续跑：沿用原输出目录 {output_dir}[/cyan]")

    processed_hashes = set()
    # task retry 时：从日志获取已成功的文件路径/哈希，避免重复导入
    already_imported_paths: set = set()
    if _force_task_id:
        success_ids = state.get_task_success_material_ids(_force_task_id)
        if success_ids:
            session = state.get_session()
            try:
                mats = session.query(Material).filter(Material.id.in_(list(success_ids))).all()
                already_imported_paths = {m.original_path for m in mats if m.original_path}
                processed_hashes.update({m.file_hash for m in mats if m.file_hash})
                console.print(f"[cyan]指定任务 #{_force_task_id} 续跑："
                              f"跳过 {len(already_imported_paths)} 个已成功素材，"
                              f"将重试 {len(files) - len(already_imported_paths)} 个未完成/失败文件[/cyan]")
            finally:
                session.close()
    if resume:
        session = state.get_session()
        try:
            imported = session.query(Material.file_hash).filter(
                Material.status == ProcessStatus.IMPORTED
            ).all()
            processed_hashes.update({h[0] for h in imported if h[0]})
        finally:
            session.close()

    def is_imported(filepath):
        if not resume and not _force_task_id:
            return False
        try:
            if _force_task_id and filepath in already_imported_paths:
                return True
            file_hash = compute_file_hash(filepath)
            if file_hash in processed_hashes:
                return True
            existing = find_material_by_hash(state, file_hash)
            return existing is not None
        except Exception:
            return False

    def process_file(filepath, tid):
        if config.dry_run:
            console.print(f"[dim]试运行: 导入 {filepath}[/dim]")
            return True

        try:
            file_hash = compute_file_hash(filepath)

            if deduplicate:
                existing = find_material_by_hash(state, file_hash)
                if existing:
                    duplicates.append(filepath)
                    console.print(f"[yellow]跳过重复文件: {filepath} (已存在 #{existing.id})[/yellow]")
                    return True

            meta = get_file_metadata(filepath)
            mat_type = detect_material_type(filepath)

            target_path = filepath
            if copy or move:
                Path(output_dir).mkdir(parents=True, exist_ok=True)
                target_path = str(Path(output_dir) / Path(filepath).name)
                if Path(target_path).exists():
                    counter = 1
                    while True:
                        stem = Path(filepath).stem
                        suffix = Path(filepath).suffix
                        new_name = f"{stem}_{counter}{suffix}"
                        target_path = str(Path(output_dir) / new_name)
                        if not Path(target_path).exists():
                            break
                        counter += 1
                if copy:
                    shutil.copy2(filepath, target_path)
                elif move:
                    shutil.move(filepath, target_path)

            material = Material(
                file_hash=file_hash,
                original_path=filepath,
                current_path=target_path,
                file_name=Path(target_path).name,
                file_ext=meta["suffix"],
                file_size=meta["size"],
                material_type=mat_type,
                title=meta["stem"],
                status=ProcessStatus.IMPORTED,
            )
            saved = save_material(state, material)
            state.log_process(material_id=saved.id, task_id=tid, action="import", success=True,
                              message=f"文件大小: {human_readable_size(meta['size'])}")
            return True
        except Exception as e:
            console.print(f"[red]导入失败 {filepath}: {e}[/red]")
            return False

    result = run_batch(
        state, task_id, files, process_file,
        description="导入素材", resume=resume,
        skip_check=is_imported if (resume or _force_task_id) else None
    )

    table = Table(title="导入结果")
    table.add_column("项目", style="cyan")
    table.add_column("数量", justify="right")
    table.add_row("总文件数", str(result["total"]))
    table.add_row("成功导入", str(result["success"]))
    table.add_row("失败", str(result["failed"]))
    table.add_row("重复跳过", str(len(duplicates)))
    if result.get("skipped", 0) > 0:
        table.add_row("跳过(已导入)", str(result["skipped"]))
    console.print(table)

    if duplicates and not dry_run:
        dup_file = Path(output_dir) / "duplicates.txt"
        dup_file.parent.mkdir(parents=True, exist_ok=True)
        with open(dup_file, "w", encoding="utf-8") as f:
            for d in duplicates:
                f.write(d + "\n")
        console.print(f"[dim]重复文件列表已保存到: {dup_file}[/dim]")
