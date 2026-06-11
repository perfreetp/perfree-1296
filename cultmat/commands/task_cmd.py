"""任务管理命令：查看历史任务、按类型筛选、重试失败任务"""
import json
import csv
import click
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from typing import Dict

from ..config import AppConfig
from ..state import (
    AppState, get_or_create_resume_task,
    find_material_by_path
)
from ..models import TaskStatus, BatchTask, Material, ProcessStatus
from ..utils import human_readable_size, json_loads
from .import_cmd import import_cmd
from .convert_cmd import convert_cmd
from .package_cmd import package_cmd

console = Console()


TASK_STATUS_STYLE = {
    TaskStatus.PENDING: "yellow",
    TaskStatus.RUNNING: "blue",
    TaskStatus.PAUSED: "magenta",
    TaskStatus.COMPLETED: "green",
    TaskStatus.FAILED: "red",
}

TASK_TYPE_EMOJI = {
    "import": "📥",
    "inspect": "🔍",
    "rename": "✏️",
    "tag": "🏷️",
    "convert": "🔄",
    "package": "📦",
    "report": "📊",
}


def _format_time(dt):
    if not dt:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_params(params_json: str) -> Dict:
    if not params_json:
        return {}
    try:
        return json_loads(params_json)
    except Exception:
        return {}


def _build_task_table(tasks, show_detail=False):
    table = Table(title="任务历史", expand=True)
    table.add_column("ID", justify="right", style="cyan", no_wrap=True)
    table.add_column("类型", no_wrap=True)
    table.add_column("名称")
    table.add_column("状态", no_wrap=True)
    table.add_column("进度", justify="right")
    table.add_column("来源目录")
    table.add_column("输出目录")
    table.add_column("创建时间", no_wrap=True)

    for t in tasks:
        status_color = TASK_STATUS_STYLE.get(t.status, "white")
        total = t.total_items or 0
        processed = t.processed_items or 0
        failed = t.failed_items or 0
        success = max(0, processed - failed)
        skipped = max(0, total - processed) if total > processed else 0
        progress_text = f"{processed}/{total}" if total > 0 else f"{processed}/0"
        if failed > 0:
            progress_text += f" [red]✗{failed}[/red]"
        if skipped > 0:
            progress_text += f" [dim]⏭{skipped}[/dim]"

        emoji = TASK_TYPE_EMOJI.get(t.task_type, "📋")
        src_short = str(Path(t.source_path).name) if t.source_path else "-"
        out_short = str(Path(t.output_path).name) if t.output_path else "-"

        table.add_row(
            str(t.id),
            f"{emoji} {t.task_type}",
            t.name or "-",
            f"[{status_color}]{t.status}[/{status_color}]",
            progress_text,
            src_short,
            out_short,
            _format_time(t.created_at),
        )
    return table


@click.group(help="任务管理：查看历史任务、重试失败任务")
@click.pass_context
def task_cmd(ctx):
    """任务管理命令组"""
    pass


@task_cmd.command("list")
@click.option("--type", "-t", "task_type",
              type=click.Choice(["import", "inspect", "rename", "tag", "convert", "package", "report", "all"]),
              default="all", help="按任务类型筛选")
@click.option("--status", "-s",
              type=click.Choice(["pending", "running", "paused", "completed", "failed", "all"]),
              default="all", help="按任务状态筛选")
@click.option("--limit", "-n", default=20, show_default=True, help="显示最近的任务数量")
@click.pass_context
def task_list(ctx, task_type, status, limit):
    """查看历史任务列表"""
    state: AppState = ctx.obj["state"]

    filter_type = None if task_type == "all" else task_type
    filter_status = None if status == "all" else status

    tasks = state.list_tasks_filtered(
        task_type=filter_type,
        status=filter_status,
        limit=limit
    )

    if not tasks:
        console.print("[yellow]暂无任务记录[/yellow]")
        return

    table = _build_task_table(tasks)
    console.print(table)
    console.print(f"[dim]共 {len(tasks)} 条任务记录[/dim]")


@task_cmd.command("show")
@click.argument("task_id", type=int)
@click.option("--checkpoint/--no-checkpoint", default=True, help="显示最近checkpoint")
@click.option("--logs", is_flag=True, help="显示处理日志")
@click.option("--only-failed", is_flag=True, help="只显示失败日志（配合--logs使用）")
@click.pass_context
def task_show(ctx, task_id, checkpoint, logs, only_failed):
    """查看任务详情"""
    state: AppState = ctx.obj["state"]

    task = state.get_task_by_id(task_id)
    if not task:
        console.print(f"[red]未找到任务 #{task_id}[/red]")
        return

    status_color = TASK_STATUS_STYLE.get(task.status, "white")
    params = _parse_params(task.params)
    checkpoint_data = state.get_task_checkpoint(task_id) if checkpoint else None

    total = task.total_items or (checkpoint_data.get("total", 0) if checkpoint_data else 0)
    processed = task.processed_items or 0
    failed = task.failed_items or 0
    success = max(0, processed - failed)
    pct = (processed / total * 100) if total > 0 else 0

    info_lines = [
        f"[bold cyan]任务 #{task.id}[/bold cyan]  {TASK_TYPE_EMOJI.get(task.task_type, '📋')} [bold]{task.name}[/bold]",
        "",
        f"  [cyan]类型:[/cyan]      {task.task_type}",
        f"  [cyan]状态:[/cyan]      [{status_color}]{task.status}[/{status_color}]",
        f"  [cyan]进度:[/cyan]      {processed}/{total} ({pct:.1f}%)  [green]✓{success}[/green] [red]✗{failed}[/red]",
        f"  [cyan]创建:[/cyan]      {_format_time(task.created_at)}",
        f"  [cyan]开始:[/cyan]      {_format_time(task.started_at)}",
        f"  [cyan]完成:[/cyan]      {_format_time(task.completed_at)}",
        f"  [cyan]来源目录:[/cyan]  {task.source_path or '-'}",
        f"  [cyan]输出目录:[/cyan]  {task.output_path or '-'}",
    ]

    if params:
        info_lines.append("")
        info_lines.append("  [cyan]任务参数:[/cyan]")
        for k, v in params.items():
            info_lines.append(f"    {k}: {v}")

    if checkpoint_data:
        info_lines.append("")
        info_lines.append("  [cyan]最近 Checkpoint:[/cyan]")
        for k, v in checkpoint_data.items():
            v_str = str(v)
            if len(v_str) > 80:
                v_str = v_str[:77] + "..."
            info_lines.append(f"    {k}: {v_str}")
    elif checkpoint:
        info_lines.append("")
        info_lines.append("  [dim]无 checkpoint 数据[/dim]")

    console.print(Panel("\n".join(info_lines), border_style=status_color))

    if logs:
        task_logs = state.get_task_logs(task_id, only_failed=only_failed, limit=500)
        if not task_logs:
            console.print("[yellow]无处理日志[/yellow]")
            return

        log_table = Table(title=f"处理日志 (共{len(task_logs)}条)" + (" [仅失败]" if only_failed else ""))
        log_table.add_column("#", justify="right")
        log_table.add_column("动作", no_wrap=True)
        log_table.add_column("结果", no_wrap=True)
        log_table.add_column("素材ID", justify="right")
        log_table.add_column("消息")

        for i, log in enumerate(task_logs, 1):
            result = "[green]✓[/green]" if log.success else "[red]✗[/red]"
            log_table.add_row(
                str(i),
                log.action or "-",
                result,
                str(log.material_id) if log.material_id else "-",
                log.message or "-",
            )
        console.print(log_table)


@task_cmd.command("retry")
@click.argument("task_id", type=int)
@click.option("--yes", "-y", is_flag=True, help="不确认直接执行")
@click.pass_context
def task_retry(ctx, task_id, yes):
    """指定任务ID继续执行（沿用原配置）

    \b
    示例:
      cultmat task retry 3          # 重试任务 #3
      cultmat task retry 3 -y       # 不确认直接执行
    """
    state: AppState = ctx.obj["state"]
    config: AppConfig = ctx.obj["config"]

    task = state.get_task_by_id(task_id)
    if not task:
        console.print(f"[red]未找到任务 #{task_id}[/red]")
        return

    if task.status == TaskStatus.COMPLETED:
        if not yes:
            if not click.confirm(f"任务 #{task_id} 已完成，仍然要重新执行吗？", default=False):
                return

    params = _parse_params(task.params)
    task_type = task.task_type

    status_color = TASK_STATUS_STYLE.get(task.status, "white")
    console.print(Panel(
        f"[bold]准备继续执行任务 #{task.id}[/bold]\n"
        f"类型: {TASK_TYPE_EMOJI.get(task_type, '📋')} {task_type}\n"
        f"名称: {task.name}\n"
        f"状态: [{status_color}]{task.status}[/{status_color}]\n"
        f"来源: {task.source_path or '-'}\n"
        f"输出: {task.output_path or '-'}\n"
        f"进度: {task.processed_items or 0}/{task.total_items or 0} "
        f"[green]✓{max(0, (task.processed_items or 0) - (task.failed_items or 0))}[/green] "
        f"[red]✗{task.failed_items or 0}[/red]",
        title="task retry",
        border_style="yellow"
    ))

    if not yes:
        if not click.confirm("确认继续执行？", default=True):
            console.print("[yellow]已取消[/yellow]")
            return

    if task_type == "import":
        source = task.source_path
        if not source or not Path(source).exists():
            console.print(f"[red]来源目录不存在: {source}[/red]")
            return

        ctx.invoke(
            import_cmd,
            source=source,
            recursive=params.get("recursive", True),
            copy=params.get("copy", False),
            move=params.get("move", False),
            output_dir=task.output_path,
            deduplicate=params.get("deduplicate", True),
            dry_run=config.dry_run,
            resume=True,
            _force_task_id=task_id,
        )

    elif task_type == "convert":
        ctx.invoke(
            convert_cmd,
            material_type="all",
            thumbnail=params.get("thumbnail", False),
            preview=params.get("preview", False),
            watermark=params.get("watermark", False),
            convert_image=params.get("convert_image", False),
            convert_audio=params.get("convert_audio", False),
            cover=False,
            crop=None,
            ocr=params.get("ocr", False),
            transcribe=params.get("transcribe", False),
            output_dir=task.output_path,
            dry_run=config.dry_run,
            resume=True,
            export_failures=False,
            failure_format="csv",
            failure_output=None,
            _force_task_id=task_id,
        )

    elif task_type == "package":
        pkg_output = Path(task.output_path).parent if task.output_path else None
        package_name = task.name.replace("[续跑]", "").strip().replace("打包 ", "")
        # 去掉时间戳后缀
        import re
        base_name = re.sub(r"_\d{8}_\d{6}$", "", package_name)
        ctx.invoke(
            package_cmd,
            name=base_name,
            manifest_format=params.get("manifest_format", "csv"),
            zip=params.get("zip", False),
            include_originals=True,
            include_previews=True,
            include_thumbnails=True,
            include_watermarked=True,
            include_converted=True,
            output_dir=str(pkg_output) if pkg_output else None,
            dry_run=config.dry_run,
            resume=True,
            material_type="all",
            _force_task_id=task_id,
        )

    else:
        console.print(f"[red]暂不支持重试 {task_type} 类型任务[/red]")
        return


@task_cmd.command("export-failures")
@click.argument("task_id", type=int)
@click.option("--format", "-f", "fmt",
              type=click.Choice(["csv", "json"]),
              default="csv", show_default=True, help="导出格式")
@click.option("--output", "-o", type=click.Path(), help="输出文件路径")
@click.pass_context
def task_export_failures(ctx, task_id, fmt, output):
    """导出任务失败日志

    \b
    示例:
      cultmat task export-failures 2 -f csv -o failures.csv
      cultmat task export-failures 2 -f json
    """
    state: AppState = ctx.obj["state"]

    task = state.get_task_by_id(task_id)
    if not task:
        console.print(f"[red]未找到任务 #{task_id}[/red]")
        return

    task_logs = state.get_task_logs(task_id, only_failed=True, limit=5000)
    if not task_logs:
        console.print("[yellow]没有失败记录[/yellow]")
        return

    failure_records = []
    for log in task_logs:
        mat_name = ""
        mat_path = ""
        if log.material_id:
            mat = find_material_by_path(state, "")
            if not mat:
                session = state.get_session()
                try:
                    from ..models import Material as M
                    m = session.query(M).filter(M.id == log.material_id).first()
                    if m:
                        mat_name = m.file_name
                        mat_path = m.current_path or m.original_path
                finally:
                    session.close()

        failure_records.append({
            "log_id": log.id,
            "task_id": log.task_id,
            "material_id": log.material_id or "",
            "material_name": mat_name,
            "material_path": mat_path,
            "action": log.action or "",
            "message": log.message or "",
            "created_at": _format_time(log.created_at),
        })

    default_output = f"failures_task_{task_id}.{fmt}"
    output_path = Path(output or default_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(failure_records, f, ensure_ascii=False, indent=2)
    else:
        fieldnames = ["log_id", "task_id", "material_id", "material_name",
                      "material_path", "action", "message", "created_at"]
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in failure_records:
                writer.writerow(r)

    console.print(f"[green]失败日志已导出: {output_path}[/green] ({len(failure_records)}条)")
