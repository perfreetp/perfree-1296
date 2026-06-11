from pathlib import Path
from datetime import datetime
from typing import Optional
import click
from rich.console import Console
from rich.table import Table

from ..config import AppConfig
from ..state import AppState, run_batch, save_material
from ..models import Material, ProcessStatus
from ..utils import safe_filename, ensure_unique_path, parse_date_from_filename

console = Console()


def generate_new_name(material: Material, pattern: str, rule, index: int = 0) -> str:
    original_stem = Path(material.file_name).stem
    original_ext = material.file_ext or Path(material.file_name).suffix
    created_date = material.created_date or ""
    date_parts = created_date.split("-") if created_date else []
    year = date_parts[0] if len(date_parts) > 0 else datetime.now().strftime("%Y")
    month = date_parts[1] if len(date_parts) > 1 else datetime.now().strftime("%m")
    day = date_parts[2] if len(date_parts) > 2 else datetime.now().strftime("%d")

    name = pattern.format(
        id=str(material.id or ""),
        index=str(index).zfill(rule.padding),
        original=original_stem,
        category=material.category or "uncategorized",
        title=material.title or original_stem,
        author=material.author or "unknown",
        year=year,
        month=month,
        day=day,
        type=material.material_type or "unknown",
    )
    name = safe_filename(name, use_slug=rule.use_slug, separator=rule.separator)
    return f"{name}{original_ext}"


@click.command()
@click.option("--pattern", "-p", help="命名模板 (可用占位符: {id}, {index}, {original}, {category}, {title}, {author}, {year}, {month}, {day}, {type})")
@click.option("--category", "-c", help="分类名称")
@click.option("--start-index", type=int, default=1, help="起始序号")
@click.option("--padding", type=int, default=4, help="序号补零位数")
@click.option("--separator", default="_", help="分隔符")
@click.option("--slug/--no-slug", default=True, help="是否清理文件名中的特殊字符")
@click.option("--in-place/--copy", default=False, help="原地重命名还是复制并重命名")
@click.option("--output-dir", "-o", type=click.Path(), help="重命名输出目录(非原地模式)")
@click.option("--dry-run", is_flag=True, help="试运行预览")
@click.option("--preview", is_flag=True, help="显示预览而不执行")
@click.pass_context
def rename_cmd(ctx, pattern, category, start_index, padding, separator, slug, in_place, output_dir, dry_run, preview):
    """按规则批量重命名素材"""
    config: AppConfig = ctx.obj["config"]
    state: AppState = ctx.obj["state"]
    config.dry_run = dry_run or preview or config.dry_run

    from ..config import RenameRule
    rule = config.rename
    if pattern:
        rule.pattern = pattern
    if category:
        rule.padding = padding
        rule.separator = separator
        rule.use_slug = slug

    if not output_dir:
        output_dir = str(Path(config.output_dir) / "renamed")

    session = state.get_session()
    try:
        materials = session.query(Material).filter(
            Material.status.in_([ProcessStatus.IMPORTED, ProcessStatus.INSPECTED])
        ).all()
    finally:
        session.close()

    if not materials:
        console.print("[yellow]没有可重命名的素材[/yellow]")
        return

    console.print(f"[green]待重命名: {len(materials)} 个素材[/green]")

    previews = []
    for i, mat in enumerate(materials, start=start_index):
        if category:
            mat.category = category
        if not mat.created_date:
            mat.created_date = parse_date_from_filename(mat.file_name)
        new_name = generate_new_name(mat, rule.pattern, rule, i)
        old_path = Path(mat.current_path)
        if in_place:
            new_path = old_path.parent / new_name
        else:
            new_path = Path(output_dir) / new_name
        previews.append((old_path, new_path, mat))

    if preview or dry_run:
        table = Table(title="重命名预览")
        table.add_column("#", justify="right", style="dim")
        table.add_column("原文件名", style="yellow")
        table.add_column("→", style="white")
        table.add_column("新文件名", style="green")
        for i, (old, new, _) in enumerate(previews[:50], 1):
            table.add_row(str(i), old.name, "→", new.name)
        if len(previews) > 50:
            table.add_row("...", f"... 还有 {len(previews) - 50} 个文件", "...", "...")
        console.print(table)
        if preview:
            return

    task_id = state.create_batch_task(
        "rename", f"重命名 {len(previews)} 个素材",
        output_path=output_dir,
        params={"pattern": rule.pattern, "in_place": in_place}
    )

    def process_item(item, tid):
        old_path, new_path, material = item
        if config.dry_run:
            return True
        try:
            if not in_place:
                new_path.parent.mkdir(parents=True, exist_ok=True)
            final_new_path = ensure_unique_path(str(new_path))
            if in_place:
                old_path.rename(final_new_path)
            else:
                import shutil
                shutil.copy2(str(old_path), final_new_path)

            material.current_path = final_new_path
            material.file_name = Path(final_new_path).name
            material.status = ProcessStatus.RENAMED
            save_material(state, material)
            state.log_process(material_id=material.id, task_id=tid, action="rename", success=True,
                              message=f"{old_path.name} → {Path(final_new_path).name}")
            return True
        except Exception as e:
            console.print(f"[red]重命名失败 {old_path.name}: {e}[/red]")
            return False

    result = run_batch(state, task_id, previews, process_item, description="重命名素材")

    table = Table(title="重命名结果")
    table.add_column("项目", style="cyan")
    table.add_column("数量", justify="right")
    table.add_row("总数", str(result["total"]))
    table.add_row("成功", str(result["success"]))
    table.add_row("失败", str(result["failed"]))
    console.print(table)
