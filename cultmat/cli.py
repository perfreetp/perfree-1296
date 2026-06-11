import os
import sys
import click
from pathlib import Path
from rich.console import Console
from rich.panel import Panel

from .config import AppConfig
from .state import AppState
from . import __version__

from .commands.import_cmd import import_cmd
from .commands.inspect_cmd import inspect_cmd
from .commands.rename_cmd import rename_cmd
from .commands.tag_cmd import tag_cmd
from .commands.convert_cmd import convert_cmd
from .commands.package_cmd import package_cmd
from .commands.report_cmd import report_cmd
from .commands.task_cmd import task_cmd

console = Console()


@click.group(invoke_without_command=True)
@click.version_option(__version__, "-V", "--version", prog_name="cultmat")
@click.option("--config", "config_path", type=click.Path(exists=False), help="配置文件路径")
@click.option("--db-path", type=click.Path(), help="数据库路径")
@click.option("--output-dir", type=click.Path(), help="默认输出目录")
@click.option("--verbose/--quiet", default=False, help="显示详细输出")
@click.option("--dry-run", is_flag=True, help="全局试运行模式")
@click.pass_context
def main(ctx, config_path, db_path, output_dir, verbose, dry_run):
    """数字文化馆素材整理命令行工具

    \b
    提供素材的批量导入、检查、重命名、打标签、转换、打包和报告功能。
    import / convert / package 命令支持 --resume 断点续跑。
    """
    config = AppConfig.load(config_path)
    if db_path:
        config.db_path = db_path
    if output_dir:
        config.output_dir = output_dir
    if verbose:
        config.verbose = True
    if dry_run:
        config.dry_run = True

    state = AppState(config)
    ctx.ensure_object(dict)
    ctx.obj["config"] = config
    ctx.obj["state"] = state

    if ctx.invoked_subcommand is None:
        banner = f"""
[bold cyan]数字文化馆素材整理工具[/bold cyan] v{__version__}
[dim]Digital Culture Center Material Organizer[/dim]

支持素材类型: 🖼️ 图片  🎵 音频  🎬 视频  📄 文档

[bold]八组命令:[/bold]
  [cyan]import[/cyan]    扫描目录、识别重复文件、导入素材
  [cyan]inspect[/cyan]   检查格式与清晰度、提取元数据
  [cyan]rename[/cyan]    按规则批量重命名
  [cyan]tag[/cyan]       补全基础信息、批量打标签
  [cyan]convert[/cyan]   提取封面、裁切、加水印、生成预览、格式转换、OCR、转写
  [cyan]package[/cyan]   打包交付清单、断点续跑
  [cyan]report[/cyan]    试运行预览、导出处理报告
  [cyan]task[/cyan]      查看任务历史、重试失败任务、导出失败记录

[dim]使用 cultmat <command> --help 查看各命令详情[/dim]
"""
        console.print(Panel(banner.strip(), border_style="cyan"))


main.add_command(import_cmd, name="import")
main.add_command(inspect_cmd, name="inspect")
main.add_command(rename_cmd, name="rename")
main.add_command(tag_cmd, name="tag")
main.add_command(convert_cmd, name="convert")
main.add_command(package_cmd, name="package")
main.add_command(report_cmd, name="report")
main.add_command(task_cmd, name="task")


if __name__ == "__main__":
    main()
