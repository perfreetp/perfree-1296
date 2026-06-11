"""任务管理命令：查看历史任务、按类型筛选、重试失败任务、清理旧任务"""
import json
import csv
import re
import click
from pathlib import Path
from datetime import datetime, timedelta
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from typing import Dict, List, Tuple

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


def _compute_task_stats(state: AppState, task: BatchTask) -> Dict:
    """从日志计算真实的成功/失败/跳过数量（优先从日志，总数以 total_items 为准）"""
    stats = state.get_task_stats_from_logs(task.id)
    # 如果数据库中 total_items 有值，以它为准
    if task.total_items and task.total_items > stats["total"]:
        stats["total"] = task.total_items
        stats["skipped"] = max(0, stats["total"] - stats["processed"])
    return stats


def _build_task_table(state, tasks, show_detail=False):
    table = Table(title="任务历史", expand=True)
    table.add_column("ID", justify="right", style="cyan", no_wrap=True)
    table.add_column("类型", no_wrap=True)
    table.add_column("名称")
    table.add_column("状态", no_wrap=True)
    table.add_column("总数", justify="right")
    table.add_column("✓成功", justify="right", style="green")
    table.add_column("✗失败", justify="right", style="red")
    table.add_column("⏭跳过", justify="right", style="dim")
    table.add_column("Checkpoint", no_wrap=True)
    table.add_column("创建时间", no_wrap=True)

    for t in tasks:
        status_color = TASK_STATUS_STYLE.get(t.status, "white")
        stats = _compute_task_stats(state, t)

        total = stats["total"] or 0
        success = stats["success"]
        failed = stats["failed"]
        skipped = stats["skipped"]

        ckpt_time = "-"
        ckpt_data = state.get_task_checkpoint(t.id)
        if ckpt_data:
            last_update = t.completed_at or t.started_at or t.created_at
            if last_update:
                ckpt_time = last_update.strftime("%m-%d %H:%M")
            else:
                ckpt_time = f"idx={ckpt_data.get('index', '?')}"

        emoji = TASK_TYPE_EMOJI.get(t.task_type, "📋")

        table.add_row(
            str(t.id),
            f"{emoji} {t.task_type}",
            t.name or "-",
            f"[{status_color}]{t.status}[/{status_color}]",
            str(total) if total > 0 else "?",
            str(success),
            str(failed),
            str(skipped),
            ckpt_time,
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

    table = _build_task_table(state, tasks)
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

    # 从日志统计真实数字，确保与导出的失败记录、日志对得上
    stats = _compute_task_stats(state, task)
    total = stats["total"]
    success = stats["success"]
    failed = stats["failed"]
    skipped = stats["skipped"]
    processed = stats["processed"]
    pct = (processed / total * 100) if total > 0 else 0

    ckpt_time = task.completed_at or task.started_at or task.created_at

    info_lines = [
        f"[bold cyan]任务 #{task.id}[/bold cyan]  {TASK_TYPE_EMOJI.get(task.task_type, '📋')} [bold]{task.name}[/bold]",
        "",
        f"  [cyan]类型:[/cyan]        {task.task_type}",
        f"  [cyan]状态:[/cyan]        [{status_color}]{task.status}[/{status_color}]",
        f"  [cyan]进度:[/cyan]        {processed}/{total} ({pct:.1f}%)",
        f"  [cyan]✓ 成功:[/cyan]      [green]{success}[/green]",
        f"  [cyan]✗ 失败:[/cyan]      [red]{failed}[/red]",
        f"  [cyan]⏭ 跳过:[/cyan]      [dim]{skipped}[/dim]",
        f"  [cyan]创建:[/cyan]        {_format_time(task.created_at)}",
        f"  [cyan]开始:[/cyan]        {_format_time(task.started_at)}",
        f"  [cyan]完成/最近:[/cyan]  {_format_time(ckpt_time)}",
        f"  [cyan]来源目录:[/cyan]    {task.source_path or '-'}",
        f"  [cyan]输出目录:[/cyan]    {task.output_path or '-'}",
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

    # 从日志统计真实进度（和task show一致）
    stats = _compute_task_stats(state, task)
    total = stats["total"]
    success = stats["success"]
    failed = stats["failed"]
    skipped = stats["skipped"]
    retry_count = failed + skipped  # 将重试的数量

    status_color = TASK_STATUS_STYLE.get(task.status, "white")
    console.print(Panel(
        f"[bold]准备继续执行任务 #{task.id}[/bold]\n"
        f"类型: {TASK_TYPE_EMOJI.get(task_type, '📋')} {task_type}\n"
        f"名称: {task.name}\n"
        f"状态: [{status_color}]{task.status}[/{status_color}]\n"
        f"来源: {task.source_path or '-'}\n"
        f"输出: {task.output_path or '-'}\n"
        f"进度: 共{total} [green]✓{success}[/green] [red]✗{failed}[/red] [dim]⏭{skipped}[/dim]\n"
        f"[yellow]将重试: {retry_count} 个未完成/失败素材[/yellow]",
        title=f"task retry #{task.id}",
        border_style="yellow"
    ))

    if not yes:
        if not click.confirm("确认继续执行？", default=True):
            console.print("[yellow]已取消[/yellow]")
            return

    console.print(f"[cyan]开始继续执行任务 #{task.id}，沿用原输入输出配置...[/cyan]")

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
        # 完整还原 convert 所有原命令选项（含material_type、cover、crop）
        ctx.invoke(
            convert_cmd,
            material_type=params.get("material_type", "all"),
            thumbnail=params.get("thumbnail", False),
            preview=params.get("preview", False),
            watermark=params.get("watermark", False),
            convert_image=params.get("convert_image", False),
            convert_audio=params.get("convert_audio", False),
            cover=params.get("cover", False),
            crop=params.get("crop", None),
            ocr=params.get("ocr", False),
            transcribe=params.get("transcribe", False),
            output_dir=task.output_path,
            dry_run=config.dry_run,
            resume=True,
            export_failures=True,
            failure_format="csv",
            failure_output=None,
            _force_task_id=task_id,
        )

    elif task_type == "package":
        # 完整还原 package 所有选项（含6个include_*、material_type）
        pkg_output = Path(task.output_path).parent if task.output_path else None
        package_name = task.name.replace("[续跑]", "").strip().replace("打包 ", "")
        # 去掉时间戳后缀
        base_name = re.sub(r"_\d{8}_\d{6}$", "", package_name)
        # 优先用 params 里保存的原名称
        if params.get("name"):
            base_name = params["name"]
        ctx.invoke(
            package_cmd,
            name=base_name,
            manifest_format=params.get("manifest_format", "csv"),
            zip=params.get("zip", True),
            include_originals=params.get("include_originals", True),
            include_previews=params.get("include_previews", True),
            include_thumbnails=params.get("include_thumbnails", True),
            include_watermarked=params.get("include_watermarked", False),
            include_converted=params.get("include_converted", True),
            output_dir=str(pkg_output) if pkg_output else None,
            dry_run=config.dry_run,
            resume=True,
            material_type=params.get("material_type", "all"),
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


@task_cmd.command("prune")
@click.option("--days", "-d", default=30, type=int, show_default=True,
              help="清理多少天前的任务（按创建时间）")
@click.option("--status", "filter_status",
              type=click.Choice(["completed", "failed", "finished", "all"]),
              default="finished", show_default=True,
              help="按状态筛选：completed=仅已完成 / failed=仅失败 / finished=完成+失败 / all=全部")
@click.option("--type", "-t", "task_type",
              type=click.Choice(["import", "inspect", "rename", "tag", "convert", "package", "report", "all"]),
              default="all", show_default=True,
              help="按任务类型筛选")
@click.option("--keep", "-k", default=10, type=int, show_default=True,
              help="至少保留最近多少个任务（防止全部被清）")
@click.option("--dry-run", "-n", is_flag=True, help="预览模式：只列出将删除的任务，不实际执行")
@click.option("--yes", "-y", is_flag=True, help="不确认直接执行")
@click.pass_context
def task_prune(ctx, days, filter_status, task_type, keep, dry_run, yes):
    """清理旧任务和日志（保留素材记录）

    \b
    说明:
      - 只会删除 batch_tasks 表和 process_logs 表的记录
      - 不会删除 materials 表的素材记录，也不会删除磁盘上的文件
      - --keep 参数保证无论多旧，都会保留最近的 N 个任务

    \b
    示例:
      cultmat task prune --dry-run                 # 预览：30天前的完成/失败任务
      cultmat task prune -d 90 --status completed  # 清理90天前的已完成任务
      cultmat task prune -d 7 -t convert -y        # 清理7天前的convert任务（无需确认）
    """
    state: AppState = ctx.obj["state"]

    cutoff_date = datetime.now() - timedelta(days=days)
    console.print(f"[cyan]清理条件:[/cyan]")
    console.print(f"  创建早于: {_format_time(cutoff_date)} ({days}天前)")
    console.print(f"  状态筛选: {filter_status}")
    console.print(f"  类型筛选: {task_type}")
    console.print(f"  至少保留: {keep} 个最近任务")

    # 查符合条件的任务
    session = state.get_session()
    try:
        query = session.query(BatchTask)

        # 状态过滤
        if filter_status == "completed":
            query = query.filter(BatchTask.status == TaskStatus.COMPLETED)
        elif filter_status == "failed":
            query = query.filter(BatchTask.status == TaskStatus.FAILED)
        elif filter_status == "finished":
            query = query.filter(BatchTask.status.in_([TaskStatus.COMPLETED, TaskStatus.FAILED]))

        # 类型过滤
        if task_type != "all":
            query = query.filter(BatchTask.task_type == task_type)

        # 日期过滤
        query = query.filter(BatchTask.created_at < cutoff_date)

        # 按创建时间倒序
        query = query.order_by(BatchTask.created_at.desc())

        candidates = query.all()

        # 扣除保留最近 N 个
        to_delete = candidates[keep:] if keep > 0 else candidates
        to_keep = candidates[:keep] if keep > 0 else []

        if not to_delete:
            console.print("\n[green]没有符合条件的旧任务需要清理[/green]")
            if to_keep:
                console.print(f"[dim]仅保留最近 {len(to_keep)} 个符合条件的任务（--keep={keep}）[/dim]")
            return

        # 统计要删除的日志数量
        from sqlalchemy import func
        from ..models import ProcessLog
        task_ids_to_delete = [t.id for t in to_delete]
        log_count = session.query(func.count(ProcessLog.id)).filter(
            ProcessLog.task_id.in_(task_ids_to_delete)
        ).scalar() or 0

    finally:
        session.close()

    # 展示要删除的列表
    console.print(f"\n[yellow]--- 待删除预览 ({len(to_delete)} 个任务, {log_count} 条日志) ---[/yellow]")

    preview_table = Table(expand=True, title="待删除任务列表" + (" [预览]" if dry_run else ""))
    preview_table.add_column("ID", justify="right", style="cyan")
    preview_table.add_column("类型", no_wrap=True)
    preview_table.add_column("名称")
    preview_table.add_column("状态", no_wrap=True)
    preview_table.add_column("创建时间", no_wrap=True)

    for t in to_delete[:50]:  # 最多显示50行
        status_color = TASK_STATUS_STYLE.get(t.status, "white")
        emoji = TASK_TYPE_EMOJI.get(t.task_type, "📋")
        preview_table.add_row(
            str(t.id),
            f"{emoji} {t.task_type}",
            t.name or "-",
            f"[{status_color}]{t.status}[/{status_color}]",
            _format_time(t.created_at),
        )
    console.print(preview_table)

    if len(to_delete) > 50:
        console.print(f"[dim]... 另外还有 {len(to_delete) - 50} 个任务未显示[/dim]")

    if dry_run:
        console.print(f"\n[cyan][预览模式][/cyan] 将删除 [red]{len(to_delete)}[/red] 个任务和 [red]{log_count}[/red] 条日志")
        console.print("[dim]去掉 --dry-run 并添加 -y 可实际执行删除[/dim]")
        return

    if not yes:
        if not click.confirm(
            f"\n确认删除 {len(to_delete)} 个任务和 {log_count} 条日志？（素材记录不会删）",
            default=False
        ):
            console.print("[yellow]已取消[/yellow]")
            return

    # 实际执行
    task_count, logs_deleted = state.delete_tasks(task_ids_to_delete, delete_logs=True)
    console.print(f"\n[green]清理完成！[/green]")
    console.print(f"  删除任务: {task_count} 个")
    console.print(f"  删除日志: {logs_deleted} 条")
    console.print(f"[dim]素材记录和磁盘文件未受影响[/dim]")


@task_cmd.command("clean")
@click.pass_context
@click.option("--days", "-d", default=30, type=int, show_default=True, help="清理多少天前的任务")
@click.option("--dry-run", "-n", is_flag=True, help="预览模式")
@click.option("--yes", "-y", is_flag=True, help="不确认")
def task_clean(ctx, days, dry_run, yes):
    """清理旧任务（prune 的简化别名，默认清理30天前的完成+失败任务）"""
    ctx.invoke(task_prune, days=days, filter_status="finished",
               task_type="all", keep=10, dry_run=dry_run, yes=yes)
