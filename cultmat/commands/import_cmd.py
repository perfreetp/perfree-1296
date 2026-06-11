import os
import shutil
from pathlib import Path
from typing import Optional
import click
from rich.console import Console
from rich.table import Table

from ..config import AppConfig, SUPPORTED_EXTENSIONS
from ..state import AppState, run_batch, find_material_by_hash, find_material_by_path, save_material
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
@click.pass_context
def import_cmd(ctx, source, recursive, copy, move, output_dir, deduplicate, dry_run, resume):
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
    task_id = state.create_batch_task(
        "import", f"导入素材: {source}",
        source_path=source, output_path=output_dir,
        params={"recursive": recursive, "copy": copy, "move": move, "deduplicate": deduplicate}
    )

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
        description="导入素材", resume=resume
    )

    table = Table(title="导入结果")
    table.add_column("项目", style="cyan")
    table.add_column("数量", justify="right")
    table.add_row("总文件数", str(result["total"]))
    table.add_row("成功导入", str(result["success"]))
    table.add_row("失败", str(result["failed"]))
    table.add_row("重复跳过", str(len(duplicates)))
    console.print(table)

    if duplicates and not dry_run:
        dup_file = Path(output_dir) / "duplicates.txt"
        dup_file.parent.mkdir(parents=True, exist_ok=True)
        with open(dup_file, "w", encoding="utf-8") as f:
            for d in duplicates:
                f.write(d + "\n")
        console.print(f"[dim]重复文件列表已保存到: {dup_file}[/dim]")
