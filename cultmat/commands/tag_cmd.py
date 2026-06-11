from pathlib import Path
from typing import Optional, List
import click
from rich.console import Console
from rich.table import Table

from ..config import AppConfig
from ..state import AppState, run_batch, save_material
from ..models import Material, ProcessStatus, Tag
from ..utils import parse_date_from_filename

console = Console()


def auto_infer_tags(material: Material) -> List[str]:
    tags = []
    tags.append(material.material_type or "unknown")
    if material.category:
        tags.append(material.category)
    if material.file_ext:
        ext_tag = material.file_ext.lstrip(".").upper()
        if ext_tag:
            tags.append(f"格式:{ext_tag}")
    if material.width and material.height:
        if material.width >= 3840 or material.height >= 2160:
            tags.append("高清:4K")
        elif material.width >= 1920 or material.height >= 1080:
            tags.append("高清:1080P")
        elif material.width >= 1280 or material.height >= 720:
            tags.append("高清:720P")
    if material.quality_score and material.quality_score >= 80:
        tags.append("质量:优质")
    if material.duration and material.duration > 300:
        tags.append("时长:长")
    return tags


@click.command()
@click.option("--tags", "-t", multiple=True, help="要添加的标签，可多次指定")
@click.option("--from-file", type=click.Path(exists=True), help="从文本文件读取标签(每行一个)")
@click.option("--category", "-c", help="设置分类")
@click.option("--title", help="设置标题模板，可使用 {original} 等占位符")
@click.option("--author", help="设置作者")
@click.option("--language", help="设置语言")
@click.option("--copyright", "copyright_info", help="设置版权信息")
@click.option("--description", help="设置描述")
@click.option("--auto/--no-auto", default=True, help="自动推断标签")
@click.option("--clear/--no-clear", default=False, help="清除已有标签")
@click.option("--dry-run", is_flag=True, help="试运行")
@click.option("--material-type", type=click.Choice(["image", "audio", "video", "document", "all"]),
              default="all", help="按素材类型过滤")
@click.pass_context
def tag_cmd(ctx, tags, from_file, category, title, author, language, copyright_info,
            description, auto, clear, dry_run, material_type):
    """补全基础信息并批量打标签"""
    config: AppConfig = ctx.obj["config"]
    state: AppState = ctx.obj["state"]
    config.dry_run = dry_run or config.dry_run

    tag_list = list(tags)
    if from_file:
        with open(from_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    tag_list.append(line)

    session = state.get_session()
    try:
        query = session.query(Material).filter(
            Material.status.in_([ProcessStatus.IMPORTED, ProcessStatus.INSPECTED, ProcessStatus.RENAMED])
        )
        if material_type != "all":
            query = query.filter(Material.material_type == material_type)
        materials = query.all()
    finally:
        session.close()

    if not materials:
        console.print("[yellow]没有可处理的素材[/yellow]")
        return

    console.print(f"[green]待处理: {len(materials)} 个素材, 自定义标签: {tag_list}[/green]")

    task_id = state.create_batch_task(
        "tag", f"标签处理 {len(materials)} 个素材",
        params={"tags": tag_list, "category": category, "auto": auto}
    )

    def process_material(material, tid):
        try:
            if not material.created_date:
                material.created_date = parse_date_from_filename(material.file_name)

            if category:
                material.category = category
            if title:
                original = Path(material.file_name).stem
                material.title = title.format(original=original, id=material.id)
            if author:
                material.author = author
            if language:
                material.language = language
            if copyright_info:
                material.copyright_info = copyright_info
            if description:
                material.description = description

            final_tags = []
            if not clear:
                sess = state.get_session()
                try:
                    m = sess.query(Material).filter(Material.id == material.id).first()
                    if m:
                        final_tags = [t.name for t in m.tags]
                finally:
                    sess.close()

            if auto:
                auto_tags = auto_infer_tags(material)
                for t in auto_tags:
                    if t not in final_tags:
                        final_tags.append(t)

            for t in tag_list:
                if t not in final_tags:
                    final_tags.append(t)

            if not config.dry_run:
                material.status = ProcessStatus.TAGGED
                save_material(state, material)
                state.add_tags_to_material(material.id, final_tags)
                state.log_process(material_id=material.id, task_id=tid, action="tag", success=True,
                                  message=f"标签: {', '.join(final_tags)}")
            return True
        except Exception as e:
            console.print(f"[red]标签处理失败 {material.file_name}: {e}[/red]")
            return False

    result = run_batch(state, task_id, materials, process_material, description="批量打标签")

    table = Table(title="标签处理结果")
    table.add_column("项目", style="cyan")
    table.add_column("数量", justify="right")
    table.add_row("总数", str(result["total"]))
    table.add_row("成功", str(result["success"]))
    table.add_row("失败", str(result["failed"]))
    console.print(table)
