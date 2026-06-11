import os
import zipfile
import csv
import json
from pathlib import Path
from typing import List
import click
from rich.console import Console
from rich.table import Table
from datetime import datetime

from ..config import AppConfig
from ..state import AppState, run_batch
from sqlalchemy.orm import joinedload
from ..models import Material, ProcessStatus, MaterialType
from ..utils import human_readable_size, human_readable_duration

console = Console()


def generate_manifest(materials: List[Material], output_path: str, format: str = "csv"):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if format == "csv":
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "ID", "文件名", "类型", "格式", "大小(字节)", "尺寸", "时长(秒)",
                "标题", "作者", "分类", "创建日期", "标签", "状态", "原始路径", "当前路径"
            ])
            for m in materials:
                tags = ", ".join([t.name for t in m.tags]) if hasattr(m, "tags") else ""
                size_str = f"{m.width}x{m.height}" if m.width and m.height else ""
                writer.writerow([
                    m.id, m.file_name, m.material_type, m.file_ext, m.file_size,
                    size_str, m.duration or "", m.title or "", m.author or "",
                    m.category or "", m.created_date or "", tags, m.status,
                    m.original_path, m.current_path
                ])
    elif format == "json":
        data = []
        for m in materials:
            tags = [t.name for t in m.tags] if hasattr(m, "tags") else []
            data.append({
                "id": m.id,
                "file_name": m.file_name,
                "type": m.material_type,
                "ext": m.file_ext,
                "size": m.file_size,
                "width": m.width,
                "height": m.height,
                "duration": m.duration,
                "title": m.title,
                "author": m.author,
                "category": m.category,
                "created_date": m.created_date,
                "tags": tags,
                "status": m.status,
                "original_path": m.original_path,
                "current_path": m.current_path,
            })
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def create_zip_package(file_list: List[str], output_zip: str, base_dir: str = None):
    Path(output_zip).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in file_list:
            path = Path(fp)
            if path.exists():
                if base_dir:
                    arcname = str(path.relative_to(Path(base_dir))) if str(path).startswith(base_dir) else path.name
                else:
                    arcname = path.name
                zf.write(str(path), arcname)


@click.command()
@click.option("--output-dir", "-o", type=click.Path(), help="输出目录")
@click.option("--name", "-n", default="delivery", help="交付包名称")
@click.option("--format", "manifest_format", type=click.Choice(["csv", "json"]), default="csv",
              help="清单文件格式")
@click.option("--zip/--no-zip", default=True, help="是否打包为zip")
@click.option("--include-originals/--no-originals", default=True, help="包含原始文件")
@click.option("--include-previews/--no-previews", default=True, help="包含预览图")
@click.option("--include-thumbnails/--no-thumbnails", default=True, help="包含缩略图")
@click.option("--include-watermarked/--no-watermarked", default=False, help="包含水印版")
@click.option("--include-converted/--no-converted", default=True, help="包含转换后文件")
@click.option("--dry-run", is_flag=True, help="试运行")
@click.option("--resume", is_flag=True, help="断点续跑")
@click.option("--material-type", type=click.Choice(["image", "audio", "video", "document", "all"]),
              default="all", help="按素材类型过滤")
@click.pass_context
def package_cmd(ctx, output_dir, name, manifest_format, zip, include_originals,
                include_previews, include_thumbnails, include_watermarked,
                include_converted, dry_run, resume, material_type):
    """打包交付清单和素材文件"""
    config: AppConfig = ctx.obj["config"]
    state: AppState = ctx.obj["state"]
    config.dry_run = dry_run or config.dry_run

    if not output_dir:
        output_dir = str(Path(config.output_dir) / "packages")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    package_name = f"{name}_{timestamp}"
    package_dir = Path(output_dir) / package_name

    session = state.get_session()
    try:
        query = session.query(Material).options(joinedload(Material.tags))
        if material_type != "all":
            query = query.filter(Material.material_type == material_type)
        materials = query.all()
        for m in materials:
            _ = list(m.tags)
    finally:
        session.close()

    if not materials:
        console.print("[yellow]没有可打包的素材[/yellow]")
        return

    console.print(f"[green]待打包: {len(materials)} 个素材[/green]")

    task_id = state.create_batch_task(
        "package", f"打包 {package_name}",
        output_path=str(package_dir),
        params={"manifest_format": manifest_format, "zip": zip}
    )

    manifest_path = package_dir / f"manifest.{manifest_format}"
    files_to_package = []
    stats = {"originals": 0, "previews": 0, "thumbnails": 0, "watermarked": 0, "converted": 0}

    def process_material(material, tid):
        try:
            if include_originals and material.current_path:
                if Path(material.current_path).exists():
                    files_to_package.append(material.current_path)
                    stats["originals"] += 1
            if include_previews and material.preview_path:
                if Path(material.preview_path).exists():
                    files_to_package.append(material.preview_path)
                    stats["previews"] += 1
            if include_thumbnails and material.thumbnail_path:
                if Path(material.thumbnail_path).exists():
                    files_to_package.append(material.thumbnail_path)
                    stats["thumbnails"] += 1
            if include_watermarked and material.watermarked_path:
                if Path(material.watermarked_path).exists():
                    files_to_package.append(material.watermarked_path)
                    stats["watermarked"] += 1
            if include_converted and material.converted_path:
                if Path(material.converted_path).exists():
                    files_to_package.append(material.converted_path)
                    stats["converted"] += 1
            return True
        except Exception as e:
            console.print(f"[red]收集文件失败 {material.file_name}: {e}[/red]")
            return False

    result = run_batch(state, task_id, materials, process_material,
                       description="收集文件", resume=resume)

    if config.dry_run:
        table = Table(title="打包预览")
        table.add_column("项目", style="cyan")
        table.add_column("数量", justify="right")
        table.add_row("素材总数", str(len(materials)))
        table.add_row("原始文件", str(stats["originals"]))
        table.add_row("预览图", str(stats["previews"]))
        table.add_row("缩略图", str(stats["thumbnails"]))
        table.add_row("水印版", str(stats["watermarked"]))
        table.add_row("转换后", str(stats["converted"]))
        table.add_row("总文件数", str(len(files_to_package)))
        table.add_row("输出目录", str(package_dir))
        console.print(table)
        return

    if not config.dry_run:
        generate_manifest(materials, str(manifest_path), manifest_format)
        files_to_package.append(str(manifest_path))

        if zip:
            zip_path = str(package_dir) + ".zip"
            create_zip_package(files_to_package, zip_path, str(Path(config.output_dir)))
            console.print(f"[green]打包完成: {zip_path}[/green]")
        else:
            package_dir.mkdir(parents=True, exist_ok=True)
            import shutil
            for fp in files_to_package:
                if Path(fp).exists():
                    dest = package_dir / Path(fp).name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(fp, dest)
            console.print(f"[green]打包完成: {package_dir}[/green]")

        for m in materials:
            state.update_material_status(m.id, ProcessStatus.PACKAGED)

        state.update_task_progress(task_id, status="completed")

    table = Table(title="打包结果")
    table.add_column("项目", style="cyan")
    table.add_column("数量", justify="right")
    table.add_row("素材总数", str(len(materials)))
    table.add_row("原始文件", str(stats["originals"]))
    table.add_row("预览图", str(stats["previews"]))
    table.add_row("缩略图", str(stats["thumbnails"]))
    table.add_row("水印版", str(stats["watermarked"]))
    table.add_row("转换后", str(stats["converted"]))
    table.add_row("总文件数", str(len(files_to_package)))
    console.print(table)
