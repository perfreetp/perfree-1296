import os
import csv
import json
from pathlib import Path
from datetime import datetime
from typing import Optional
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from jinja2 import Template

from ..config import AppConfig
from ..state import AppState
from sqlalchemy.orm import joinedload
from ..models import Material, BatchTask, ProcessLog, ProcessStatus, MaterialType
from ..utils import human_readable_size, human_readable_duration

console = Console()

HTML_REPORT_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>数字文化馆素材处理报告</title>
<style>
body { font-family: "Microsoft YaHei", sans-serif; margin: 40px; background: #f5f5f5; }
.container { max-width: 1200px; margin: 0 auto; background: white; padding: 40px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
h1 { color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }
h2 { color: #34495e; margin-top: 30px; }
.summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin: 20px 0; }
.card { background: #ecf0f1; padding: 20px; border-radius: 6px; text-align: center; }
.card .num { font-size: 36px; font-weight: bold; color: #2980b9; }
.card .label { color: #7f8c8d; margin-top: 5px; }
table { width: 100%; border-collapse: collapse; margin: 20px 0; }
th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
th { background: #34495e; color: white; }
tr:hover { background: #f8f9fa; }
.type-image { color: #27ae60; }
.type-audio { color: #e67e22; }
.type-video { color: #8e44ad; }
.type-document { color: #2980b9; }
.status-success { color: #27ae60; font-weight: bold; }
.status-failed { color: #e74c3c; font-weight: bold; }
.status-pending { color: #f39c12; }
.footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid #ddd; color: #7f8c8d; text-align: center; }
</style>
</head>
<body>
<div class="container">
<h1>📚 数字文化馆素材处理报告</h1>
<p>报告生成时间: {{ generated_at }}</p>

<h2>📊 总览统计</h2>
<div class="summary">
    <div class="card"><div class="num">{{ stats.total }}</div><div class="label">素材总数</div></div>
    <div class="card"><div class="num">{{ stats.images }}</div><div class="label">图片</div></div>
    <div class="card"><div class="num">{{ stats.audios }}</div><div class="label">音频</div></div>
    <div class="card"><div class="num">{{ stats.videos }}</div><div class="label">视频</div></div>
    <div class="card"><div class="num">{{ stats.documents }}</div><div class="label">文档</div></div>
    <div class="card"><div class="num">{{ stats.total_size }}</div><div class="label">总大小</div></div>
</div>

<h2>📋 处理状态</h2>
<table>
<tr><th>状态</th><th>数量</th><th>占比</th></tr>
{% for s in status_stats %}
<tr><td>{{ s.label }}</td><td>{{ s.count }}</td><td>{{ s.percent }}%</td></tr>
{% endfor %}
</table>

<h2>🗂️ 素材清单</h2>
<table>
<tr><th>ID</th><th>文件名</th><th>类型</th><th>大小</th><th>尺寸/时长</th><th>标题</th><th>标签</th><th>状态</th></tr>
{% for m in materials %}
<tr>
    <td>{{ m.id }}</td>
    <td>{{ m.file_name }}</td>
    <td class="type-{{ m.material_type }}">{{ m.type_label }}</td>
    <td>{{ m.size_label }}</td>
    <td>{{ m.dim_label }}</td>
    <td>{{ m.title or '-' }}</td>
    <td>{{ m.tags_label }}</td>
    <td class="status-{{ m.status_class }}">{{ m.status_label }}</td>
</tr>
{% endfor %}
</table>

<h2>📝 任务记录</h2>
<table>
<tr><th>任务ID</th><th>类型</th><th>名称</th><th>状态</th><th>总数</th><th>成功</th><th>失败</th><th>创建时间</th></tr>
{% for t in tasks %}
<tr>
    <td>{{ t.id }}</td>
    <td>{{ t.task_type }}</td>
    <td>{{ t.name }}</td>
    <td>{{ t.status }}</td>
    <td>{{ t.total_items }}</td>
    <td>{{ t.processed_items }}</td>
    <td>{{ t.failed_items }}</td>
    <td>{{ t.created_at }}</td>
</tr>
{% endfor %}
</table>

<div class="footer">
    由 数字文化馆素材整理工具 生成 | cultmat v1.0.0
</div>
</div>
</body>
</html>
"""


def collect_report_data(state: AppState):
    session = state.get_session()
    try:
        materials = session.query(Material).options(joinedload(Material.tags)).all()
        for m in materials:
            _ = list(m.tags)
        tasks = session.query(BatchTask).order_by(BatchTask.created_at.desc()).all()

        stats = {
            "total": len(materials),
            "images": len([m for m in materials if m.material_type == MaterialType.IMAGE]),
            "audios": len([m for m in materials if m.material_type == MaterialType.AUDIO]),
            "videos": len([m for m in materials if m.material_type == MaterialType.VIDEO]),
            "documents": len([m for m in materials if m.material_type == MaterialType.DOCUMENT]),
            "total_size": human_readable_size(sum(m.file_size or 0 for m in materials)),
        }

        status_counts = {}
        for m in materials:
            status_counts[m.status] = status_counts.get(m.status, 0) + 1
        status_labels = {
            ProcessStatus.PENDING: ("待处理", "pending"),
            ProcessStatus.IMPORTED: ("已导入", "success"),
            ProcessStatus.INSPECTED: ("已检查", "success"),
            ProcessStatus.RENAMED: ("已重命名", "success"),
            ProcessStatus.TAGGED: ("已打标签", "success"),
            ProcessStatus.CONVERTED: ("已转换", "success"),
            ProcessStatus.PACKAGED: ("已打包", "success"),
            ProcessStatus.FAILED: ("失败", "failed"),
            ProcessStatus.SKIPPED: ("已跳过", "pending"),
        }
        status_stats = []
        for status, count in status_counts.items():
            label, cls = status_labels.get(status, (status, "pending"))
            status_stats.append({
                "label": label,
                "count": count,
                "percent": round(count / len(materials) * 100, 1) if materials else 0,
            })

        type_labels = {
            MaterialType.IMAGE: "🖼️ 图片",
            MaterialType.AUDIO: "🎵 音频",
            MaterialType.VIDEO: "🎬 视频",
            MaterialType.DOCUMENT: "📄 文档",
            MaterialType.UNKNOWN: "❓ 未知",
        }

        material_list = []
        for m in materials:
            dim = ""
            if m.width and m.height:
                dim = f"{m.width}x{m.height}"
            elif m.duration:
                dim = human_readable_duration(m.duration)
            tags_str = ", ".join([t.name for t in m.tags]) if hasattr(m, "tags") else ""
            status_label, status_class = status_labels.get(m.status, (m.status, "pending"))
            material_list.append({
                "id": m.id,
                "file_name": m.file_name,
                "material_type": m.material_type,
                "type_label": type_labels.get(m.material_type, m.material_type),
                "size_label": human_readable_size(m.file_size or 0),
                "dim_label": dim,
                "title": m.title,
                "tags_label": tags_str,
                "status": m.status,
                "status_label": status_label,
                "status_class": status_class,
            })

        task_list = []
        for t in tasks[:20]:
            task_list.append({
                "id": t.id,
                "task_type": t.task_type,
                "name": t.name,
                "status": t.status,
                "total_items": t.total_items,
                "processed_items": t.processed_items,
                "failed_items": t.failed_items,
                "created_at": t.created_at.strftime("%Y-%m-%d %H:%M"),
            })

        return stats, status_stats, material_list, task_list
    finally:
        session.close()


@click.command()
@click.option("--output-dir", "-o", type=click.Path(), help="报告输出目录")
@click.option("--format", "report_format", type=click.Choice(["html", "csv", "json"]), default="html",
              help="报告格式")
@click.option("--preview", is_flag=True, help="仅在终端预览不导出")
@click.pass_context
def report_cmd(ctx, output_dir, report_format, preview):
    """导出处理报告或预览统计"""
    config: AppConfig = ctx.obj["config"]
    state: AppState = ctx.obj["state"]

    if not output_dir:
        output_dir = str(Path(config.output_dir) / "reports")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    stats, status_stats, materials, tasks = collect_report_data(state)

    if preview or not materials:
        panel_content = []
        panel_content.append(f"[bold cyan]素材总数:[/bold cyan] {stats['total']}")
        panel_content.append(f"  🖼️ 图片: {stats['images']}   🎵 音频: {stats['audios']}")
        panel_content.append(f"  🎬 视频: {stats['videos']}   📄 文档: {stats['documents']}")
        panel_content.append(f"  💾 总大小: {stats['total_size']}")
        panel_content.append("")
        panel_content.append("[bold cyan]处理状态:[/bold cyan]")
        for s in status_stats:
            panel_content.append(f"  {s['label']}: {s['count']} ({s['percent']}%)")
        panel_content.append("")
        panel_content.append(f"[bold cyan]历史任务:[/bold cyan] {len(tasks)} 个")
        if tasks:
            for t in tasks[:5]:
                panel_content.append(f"  #{t['id']} [{t['status']}] {t['task_type']} - {t['name']}")

        console.print(Panel("\n".join(panel_content), title="📊 素材处理概览", border_style="blue"))
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir) / f"report_{timestamp}.{report_format}"

    if report_format == "html":
        template = Template(HTML_REPORT_TEMPLATE)
        html = template.render(
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            stats=stats,
            status_stats=status_stats,
            materials=materials,
            tasks=tasks,
        )
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

    elif report_format == "csv":
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["ID", "文件名", "类型", "大小", "尺寸/时长", "标题", "作者", "分类", "标签", "状态"])
            for m in materials:
                writer.writerow([
                    m["id"], m["file_name"], m["type_label"], m["size_label"],
                    m["dim_label"], m["title"] or "", "", "", m["tags_label"], m["status_label"]
                ])

    elif report_format == "json":
        data = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stats": stats,
            "status_stats": status_stats,
            "materials": materials,
            "tasks": tasks,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    console.print(f"[green]报告已导出: {output_path}[/green]")
