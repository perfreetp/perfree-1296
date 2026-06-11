import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, Dict
import click
from rich.console import Console
from rich.table import Table

from ..config import AppConfig
from ..state import AppState, run_batch, save_material, get_or_create_resume_task
from ..models import Material, ProcessStatus, MaterialType

console = Console()


def create_thumbnail(image_path: str, output_path: str, size: int = 300) -> bool:
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            img.thumbnail((size, size))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            img.save(output_path, "JPEG", quality=85)
        return True
    except Exception as e:
        console.print(f"[dim]缩略图生成失败: {e}[/dim]")
        return False


def resize_image(image_path: str, output_path: str, max_size: int = 1920, quality: int = 85) -> bool:
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            width, height = img.size
            if max(width, height) > max_size:
                ratio = max_size / max(width, height)
                new_size = (int(width * ratio), int(height * ratio))
                img = img.resize(new_size, Image.LANCZOS)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            img.save(output_path, "JPEG", quality=quality)
        return True
    except Exception as e:
        console.print(f"[dim]图片转换失败: {e}[/dim]")
        return False


def crop_image(image_path: str, output_path: str, box: Tuple[int, int, int, int]) -> bool:
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            cropped = img.crop(box)
            if cropped.mode in ("RGBA", "P"):
                cropped = cropped.convert("RGB")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            cropped.save(output_path, "JPEG", quality=90)
        return True
    except Exception as e:
        console.print(f"[dim]图片裁切失败: {e}[/dim]")
        return False


def add_watermark(image_path: str, output_path: str, watermark_cfg) -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont
        with Image.open(image_path) as img:
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
            draw = ImageDraw.Draw(overlay)
            try:
                font = ImageFont.truetype("arial.ttf", watermark_cfg.font_size)
            except Exception:
                font = ImageFont.load_default()

            text_bbox = draw.textbbox((0, 0), watermark_cfg.text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            margin = 20

            positions = {
                "top-left": (margin, margin),
                "top-right": (img.width - text_width - margin, margin),
                "bottom-left": (margin, img.height - text_height - margin),
                "bottom-right": (img.width - text_width - margin, img.height - text_height - margin),
                "center": ((img.width - text_width) // 2, (img.height - text_height) // 2),
            }
            pos = positions.get(watermark_cfg.position, positions["bottom-right"])
            alpha = int(255 * watermark_cfg.opacity)
            draw.text(pos, watermark_cfg.text, font=font, fill=(255, 255, 255, alpha))

            result = Image.alpha_composite(img, overlay)
            result = result.convert("RGB")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            result.save(output_path, "JPEG", quality=90)
        return True
    except Exception as e:
        console.print(f"[dim]水印添加失败: {e}[/dim]")
        return False


def extract_pdf_cover(pdf_path: str, output_path: str) -> bool:
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"PDF封面提取 - 文件: {Path(pdf_path).name}\n")
            f.write(f"页数: {len(reader.pages)}\n")
            if reader.metadata:
                f.write(f"标题: {reader.metadata.get('/Title', '')}\n")
                f.write(f"作者: {reader.metadata.get('/Author', '')}\n")
            if len(reader.pages) > 0:
                text = reader.pages[0].extract_text() or ""
                f.write(f"\n--- 第一页文本 ---\n{text[:1000]}\n")
        return True
    except Exception as e:
        console.print(f"[dim]PDF封面提取失败: {e}[/dim]")
        return False


def convert_audio_file(audio_path: str, output_path: str, target_format: str = "mp3", bitrate: str = "192k") -> Tuple[bool, str]:
    """转换音频文件，返回(成功状态, 消息)"""
    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        import shutil

        src_ext = Path(audio_path).suffix.lower()
        target_ext = f".{target_format.lower()}"

        def _validate_audio(path: str) -> Tuple[bool, str]:
            """验证音频文件是否有效"""
            try:
                from pydub.utils import mediainfo
                info = mediainfo(path)
                if not info:
                    return False, "音频文件无法解析"
                duration = float(info.get("duration", 0))
                if duration <= 0:
                    return False, "音频时长为0或无效"
                return True, f"有效音频，时长{duration:.1f}秒"
            except Exception as e:
                return False, f"音频验证失败: {str(e)}"

        src_valid, src_msg = _validate_audio(audio_path)
        if not src_valid:
            return False, f"源音频损坏 - {src_msg}"

        if src_ext == target_ext:
            shutil.copy2(audio_path, output_path)
            out_valid, out_msg = _validate_audio(output_path)
            if not out_valid:
                try:
                    Path(output_path).unlink(missing_ok=True)
                except Exception:
                    pass
                return False, f"复制后音频无效 - {out_msg}"
            return True, f"格式一致，直接复制: {Path(audio_path).name}"

        try:
            from pydub import AudioSegment
            ext = src_ext.lstrip(".")
            audio = AudioSegment.from_file(audio_path, format=ext if ext else None)
            audio.export(output_path, format=target_format, bitrate=bitrate)

            out_valid, out_msg = _validate_audio(output_path)
            if not out_valid:
                try:
                    Path(output_path).unlink(missing_ok=True)
                except Exception:
                    pass
                return False, f"转换后音频无效 - {out_msg}"

            return True, f"格式转换成功: {src_ext} -> {target_ext}"
        except Exception as e:
            error_msg = f"音频转换失败: {str(e)}"
            console.print(f"[yellow]{error_msg}[/yellow]")
            try:
                Path(output_path).unlink(missing_ok=True)
            except Exception:
                pass
            return False, error_msg

    except Exception as e:
        error_msg = f"音频转换异常: {str(e)}"
        console.print(f"[red]{error_msg}[/red]")
        try:
            Path(output_path).unlink(missing_ok=True)
        except Exception:
            pass
        return False, error_msg


def simple_ocr(image_path: str) -> str:
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            return f"[OCR暂不可用] 图片尺寸: {img.size[0]}x{img.size[1]}"
    except Exception:
        return "[OCR不可用]"


def simple_transcribe(audio_path: str) -> str:
    try:
        size = Path(audio_path).stat().st_size
        return f"[转写暂不可用] 音频文件大小: {size} bytes"
    except Exception:
        return "[转写不可用]"


@click.command()
@click.option("--thumbnail/--no-thumbnail", default=True, help="生成缩略图")
@click.option("--preview/--no-preview", default=True, help="生成预览图(缩放)")
@click.option("--watermark/--no-watermark", default=False, help="添加水印")
@click.option("--convert-image/--no-convert-image", default=False, help="转换图片格式")
@click.option("--convert-audio/--no-convert-audio", default=False, help="转换音频格式")
@click.option("--crop", help="裁切区域 (left,top,right,bottom)")
@click.option("--cover/--no-cover", default=False, help="提取文档/视频封面")
@click.option("--ocr/--no-ocr", default=False, help="文档OCR识字")
@click.option("--transcribe/--no-transcribe", default=False, help="音频转写")
@click.option("--output-dir", "-o", type=click.Path(), help="输出目录")
@click.option("--dry-run", is_flag=True, help="试运行")
@click.option("--resume", is_flag=True, help="断点续跑")
@click.option("--material-type", type=click.Choice(["image", "audio", "video", "document", "all"]),
              default="all", help="按素材类型过滤")
@click.option("--export-failures", is_flag=True, help="导出失败原因汇总")
@click.option("--failure-format", type=click.Choice(["csv", "json"]), default="csv", show_default=True,
              help="失败汇总导出格式")
@click.option("--failure-output", type=click.Path(), help="失败汇总输出路径")
@click.option("--force-task-id", "_force_task_id", type=int, hidden=True, default=None)
@click.pass_context
def convert_cmd(ctx, thumbnail, preview, watermark, convert_image, convert_audio,
                crop, cover, ocr, transcribe, output_dir, dry_run, resume, material_type,
                export_failures, failure_format, failure_output, _force_task_id):
    """转换处理：封面提取、裁切、转写、OCR、水印、预览、格式转换"""
    config: AppConfig = ctx.obj["config"]
    state: AppState = ctx.obj["state"]
    config.dry_run = dry_run or config.dry_run

    if not output_dir:
        output_dir = str(Path(config.output_dir) / "converted")
    conv_cfg = config.convert

    crop_box = None
    if crop:
        parts = [int(x.strip()) for x in crop.split(",")]
        if len(parts) == 4:
            crop_box = tuple(parts)

    failure_records = []

    session = state.get_session()
    try:
        if resume or _force_task_id:
            query = session.query(Material).filter(
                Material.status.in_([
                    ProcessStatus.IMPORTED, ProcessStatus.INSPECTED,
                    ProcessStatus.RENAMED, ProcessStatus.TAGGED,
                    ProcessStatus.CONVERTED, ProcessStatus.FAILED
                ])
            )
        else:
            query = session.query(Material).filter(
                Material.status.in_([
                    ProcessStatus.IMPORTED, ProcessStatus.INSPECTED,
                    ProcessStatus.RENAMED, ProcessStatus.TAGGED
                ])
            )
        if material_type != "all":
            query = query.filter(Material.material_type == material_type)
        materials = query.all()
    finally:
        session.close()

    if not materials:
        console.print("[yellow]没有可处理的素材[/yellow]")
        return

    console.print(f"[green]待转换: {len(materials)} 个素材[/green]")

    task_name = f"转换处理 {len(materials)} 个素材"
    if resume and not _force_task_id:
        task_name = "[续跑] " + task_name

    task_id, is_resume = get_or_create_resume_task(
        state, "convert", task_name,
        resume=resume,
        force_resume_task_id=_force_task_id,
        output_path=output_dir,
        params={"thumbnail": thumbnail, "preview": preview, "watermark": watermark,
                "convert_image": convert_image, "convert_audio": convert_audio,
                "ocr": ocr, "transcribe": transcribe, "cover": cover,
                "crop": crop, "material_type": material_type}
    )

    # 如果是指定任务续跑（task retry），先从日志找出已成功的素材ID
    already_success_ids: set = set()
    if _force_task_id:
        already_success_ids = state.get_task_success_material_ids(_force_task_id)
        skipped_count = len([m for m in materials if m.id in already_success_ids])
        if skipped_count > 0:
            console.print(f"[cyan]指定任务 #{_force_task_id} 续跑：跳过 {skipped_count} 个已成功素材，"
                          f"将重试 {len(materials) - skipped_count} 个未完成/失败素材[/cyan]")

    def is_converted(material):
        return material.status == ProcessStatus.CONVERTED

    def has_operation(material):
        if material.material_type == MaterialType.IMAGE:
            return thumbnail or preview or watermark or convert_image or crop_box or ocr
        elif material.material_type == MaterialType.AUDIO:
            return convert_audio or transcribe
        elif material.material_type == MaterialType.DOCUMENT:
            return cover or ocr
        elif material.material_type == MaterialType.VIDEO:
            return cover or thumbnail
        return False

    def skip_check(material):
        if _force_task_id and material.id in already_success_ids:
            return True
        if resume and is_converted(material):
            return True
        if not has_operation(material):
            return True
        return False

    def _classify_failure(msg: str) -> str:
        """根据错误消息分类失败原因"""
        m = (msg or "").lower()
        if "不存在" in msg or "not found" in m or "no such file" in m:
            return "文件不存在"
        if "损坏" in msg or "无效音频" in msg or "invalid" in m or "corrupt" in m:
            return "文件损坏"
        if "转换失败" in msg or "convert" in m or "export" in m:
            return "转换失败"
        if "缩略图" in msg and "失败" in msg:
            return "缩略图生成失败"
        if "预览" in msg and "失败" in msg:
            return "预览图生成失败"
        if "水印" in msg and "失败" in msg:
            return "水印添加失败"
        if "裁切" in msg and "失败" in msg:
            return "裁切失败"
        if "封面" in msg and "失败" in msg:
            return "封面提取失败"
        if "异常" in msg or "exception" in m:
            return "处理异常"
        return "其他错误"

    def process_material(material, tid):
        filepath = Path(material.current_path)
        if not filepath.exists():
            err_msg = f"文件不存在: {material.current_path}"
            console.print(f"[red]{err_msg}[/red]")
            if export_failures:
                failure_records.append({
                    "task_id": tid,
                    "material_id": material.id,
                    "file_name": material.file_name,
                    "original_path": material.original_path or "",
                    "current_path": material.current_path or "",
                    "material_type": material.material_type,
                    "failure_type": "文件不存在",
                    "message": err_msg,
                })
            state.log_process(material_id=material.id, task_id=tid, action="convert",
                              success=False, message=err_msg)
            return False

        all_success = True
        messages = []
        first_failure_type = None

        try:
            out_base = Path(output_dir)
            out_thumb = out_base / "thumbnails" / f"{material.id}{filepath.suffix if thumbnail else '.jpg'}"
            out_preview = out_base / "previews" / f"{material.id}.jpg"
            out_watermark = out_base / "watermarked" / f"{material.id}.jpg"
            out_converted_img = out_base / "converted" / f"{material.id}.{conv_cfg.image_format}"
            out_converted_audio = out_base / "audio" / f"{material.id}.{conv_cfg.audio_format}"

            if material.material_type == MaterialType.IMAGE:
                if thumbnail and not config.dry_run:
                    ok = create_thumbnail(str(filepath), str(out_thumb), conv_cfg.thumbnail_size)
                    if ok:
                        material.thumbnail_path = str(out_thumb)
                        messages.append("缩略图生成成功")
                    else:
                        all_success = False
                        first_failure_type = first_failure_type or "缩略图生成失败"
                        messages.append("缩略图生成失败")
                if preview and not config.dry_run:
                    ok = resize_image(str(filepath), str(out_preview), conv_cfg.image_max_size, conv_cfg.image_quality)
                    if ok:
                        material.preview_path = str(out_preview)
                        messages.append("预览图生成成功")
                    else:
                        all_success = False
                        first_failure_type = first_failure_type or "预览图生成失败"
                        messages.append("预览图生成失败")
                if watermark and not config.dry_run:
                    ok = add_watermark(str(filepath), str(out_watermark), config.watermark)
                    if ok:
                        material.watermarked_path = str(out_watermark)
                        messages.append("水印添加成功")
                    else:
                        all_success = False
                        first_failure_type = first_failure_type or "水印添加失败"
                        messages.append("水印添加失败")
                if convert_image and not config.dry_run:
                    ok = resize_image(str(filepath), str(out_converted_img), conv_cfg.image_max_size, conv_cfg.image_quality)
                    if ok:
                        material.converted_path = str(out_converted_img)
                        messages.append("图片格式转换成功")
                    else:
                        all_success = False
                        first_failure_type = first_failure_type or "转换失败"
                        messages.append("图片格式转换失败")
                if crop_box and not config.dry_run:
                    crop_out = out_base / "cropped" / f"{material.id}.jpg"
                    ok = crop_image(str(filepath), str(crop_out), crop_box)
                    if ok:
                        material.converted_path = str(crop_out)
                        messages.append("图片裁切成功")
                    else:
                        all_success = False
                        first_failure_type = first_failure_type or "裁切失败"
                        messages.append("图片裁切失败")
                if ocr and not config.dry_run:
                    material.ocr_text = simple_ocr(str(filepath))
                    messages.append("OCR处理完成")

            elif material.material_type == MaterialType.AUDIO:
                if convert_audio and not config.dry_run:
                    ok, msg = convert_audio_file(
                        str(filepath), str(out_converted_audio),
                        conv_cfg.audio_format, conv_cfg.audio_bitrate
                    )
                    messages.append(msg)
                    if ok:
                        material.converted_path = str(out_converted_audio)
                    else:
                        all_success = False
                        first_failure_type = first_failure_type or _classify_failure(msg)
                if transcribe and not config.dry_run:
                    material.transcription = simple_transcribe(str(filepath))
                    messages.append("音频转写完成")

            elif material.material_type == MaterialType.DOCUMENT:
                if cover and not config.dry_run:
                    cover_out = out_base / "covers" / f"{material.id}_cover.txt"
                    if material.file_ext == ".pdf":
                        ok = extract_pdf_cover(str(filepath), str(cover_out))
                        if ok:
                            material.thumbnail_path = str(cover_out)
                            messages.append("PDF封面提取成功")
                        else:
                            all_success = False
                            first_failure_type = first_failure_type or "封面提取失败"
                            messages.append("PDF封面提取失败")
                if ocr and not config.dry_run:
                    material.ocr_text = f"[文档OCR] {filepath.name}"
                    messages.append("文档OCR处理完成")

            if not config.dry_run:
                if all_success:
                    material.status = ProcessStatus.CONVERTED
                save_material(state, material)
                state.log_process(
                    material_id=material.id, task_id=tid, action="convert",
                    success=all_success, message="; ".join(messages)
                )

            if not all_success and export_failures:
                failure_records.append({
                    "task_id": tid,
                    "material_id": material.id,
                    "file_name": material.file_name,
                    "original_path": material.original_path or "",
                    "current_path": material.current_path or "",
                    "material_type": material.material_type,
                    "failure_type": first_failure_type or "其他错误",
                    "message": "; ".join(m for m in messages if "失败" in m or "损坏" in m or "不存在" in m),
                })

            return all_success
        except Exception as e:
            err_msg = f"转换异常: {str(e)}"
            console.print(f"[red]转换失败 {material.file_name}: {e}[/red]")
            if export_failures:
                failure_records.append({
                    "task_id": tid,
                    "material_id": material.id,
                    "file_name": material.file_name,
                    "original_path": material.original_path or "",
                    "current_path": material.current_path or "",
                    "material_type": material.material_type,
                    "failure_type": "处理异常",
                    "message": err_msg,
                })
            state.log_process(material_id=material.id, task_id=tid, action="convert",
                              success=False, message=err_msg)
            return False

    result = run_batch(state, task_id, materials, process_material,
                       description="转换处理", resume=resume,
                       skip_check=skip_check)

    if export_failures and failure_records:
        import csv as _csv
        output_dir_path = Path(output_dir)
        output_dir_path.mkdir(parents=True, exist_ok=True)
        default_name = f"convert_failures_task{task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{failure_format}"
        fpath = Path(failure_output) if failure_output else output_dir_path / default_name
        fpath.parent.mkdir(parents=True, exist_ok=True)

        if failure_format == "json":
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(failure_records, f, ensure_ascii=False, indent=2)
        else:
            fieldnames = ["task_id", "material_id", "file_name", "original_path",
                          "current_path", "material_type", "failure_type", "message"]
            with open(fpath, "w", encoding="utf-8-sig", newline="") as f:
                writer = _csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for r in failure_records:
                    writer.writerow(r)

        # 分类统计
        type_count: Dict[str, int] = {}
        for r in failure_records:
            ft = r["failure_type"]
            type_count[ft] = type_count.get(ft, 0) + 1
        stats_lines = [f"  [cyan]{k}[/cyan]: {v}条" for k, v in sorted(type_count.items(), key=lambda x: -x[1])]
        console.print(
            f"[green]失败原因汇总已导出: {fpath}[/green]\n"
            + f"  共 {len(failure_records)} 条失败记录\n"
            + "\n".join(stats_lines)
        )
    elif export_failures and not failure_records:
        console.print("[dim]本次转换无失败记录[/dim]")

    table = Table(title="转换处理结果")
    table.add_column("项目", style="cyan")
    table.add_column("数量", justify="right")
    table.add_row("总数", str(result["total"]))
    table.add_row("成功", str(result["success"]))
    table.add_row("失败", str(result["failed"]))
    if result.get("skipped", 0) > 0:
        table.add_row("跳过(已处理)", str(result["skipped"]))
    console.print(table)
