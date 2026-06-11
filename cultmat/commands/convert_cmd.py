from pathlib import Path
from typing import Optional, Tuple
import click
from rich.console import Console
from rich.table import Table

from ..config import AppConfig
from ..state import AppState, run_batch, save_material
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


def convert_audio(audio_path: str, output_path: str, target_format: str = "mp3", bitrate: str = "192k") -> bool:
    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        import shutil
        if Path(audio_path).suffix.lower() == f".{target_format}":
            shutil.copy2(audio_path, output_path)
            return True
        try:
            from pydub import AudioSegment
            ext = Path(audio_path).suffix.lstrip(".")
            audio = AudioSegment.from_file(audio_path, format=ext if ext else None)
            audio.export(output_path, format=target_format, bitrate=bitrate)
            return True
        except Exception as e:
            console.print(f"[dim]pydub转换失败，复制原文件: {e}[/dim]")
            shutil.copy2(audio_path, output_path)
            return True
    except Exception as e:
        console.print(f"[dim]音频转换失败: {e}[/dim]")
        return False


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
@click.pass_context
def convert_cmd(ctx, thumbnail, preview, watermark, convert_image, convert_audio,
                crop, cover, ocr, transcribe, output_dir, dry_run, resume, material_type):
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

    session = state.get_session()
    try:
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

    task_id = state.create_batch_task(
        "convert", f"转换处理 {len(materials)} 个素材",
        output_path=output_dir,
        params={"thumbnail": thumbnail, "preview": preview, "watermark": watermark,
                "convert_image": convert_image, "convert_audio": convert_audio,
                "ocr": ocr, "transcribe": transcribe}
    )

    def process_material(material, tid):
        filepath = Path(material.current_path)
        if not filepath.exists():
            return False

        try:
            out_base = Path(output_dir)
            out_thumb = out_base / "thumbnails" / f"{material.id}{filepath.suffix if thumbnail else '.jpg'}"
            out_preview = out_base / "previews" / f"{material.id}.jpg"
            out_watermark = out_base / "watermarked" / f"{material.id}.jpg"
            out_converted = out_base / "converted" / f"{material.id}.{conv_cfg.image_format}"

            if material.material_type == MaterialType.IMAGE:
                if thumbnail and not config.dry_run:
                    if create_thumbnail(str(filepath), str(out_thumb), conv_cfg.thumbnail_size):
                        material.thumbnail_path = str(out_thumb)
                if preview and not config.dry_run:
                    if resize_image(str(filepath), str(out_preview), conv_cfg.image_max_size, conv_cfg.image_quality):
                        material.preview_path = str(out_preview)
                if watermark and not config.dry_run:
                    if add_watermark(str(filepath), str(out_watermark), config.watermark):
                        material.watermarked_path = str(out_watermark)
                if convert_image and not config.dry_run:
                    if resize_image(str(filepath), str(out_converted), conv_cfg.image_max_size, conv_cfg.image_quality):
                        material.converted_path = str(out_converted)
                if crop_box and not config.dry_run:
                    crop_out = out_base / "cropped" / f"{material.id}.jpg"
                    if crop_image(str(filepath), str(crop_out), crop_box):
                        material.converted_path = str(crop_out)
                if ocr and not config.dry_run:
                    material.ocr_text = simple_ocr(str(filepath))

            elif material.material_type == MaterialType.AUDIO:
                if convert_audio and not config.dry_run:
                    audio_out = out_base / "audio" / f"{material.id}.{conv_cfg.audio_format}"
                    if convert_audio(str(filepath), str(audio_out), conv_cfg.audio_format, conv_cfg.audio_bitrate):
                        material.converted_path = str(audio_out)
                if transcribe and not config.dry_run:
                    material.transcription = simple_transcribe(str(filepath))

            elif material.material_type == MaterialType.DOCUMENT:
                if cover and not config.dry_run:
                    cover_out = out_base / "covers" / f"{material.id}_cover.txt"
                    if material.file_ext == ".pdf":
                        if extract_pdf_cover(str(filepath), str(cover_out)):
                            material.thumbnail_path = str(cover_out)
                if ocr and not config.dry_run:
                    material.ocr_text = f"[文档OCR] {filepath.name}"

            if not config.dry_run:
                material.status = ProcessStatus.CONVERTED
                save_material(state, material)
                state.log_process(material_id=material.id, task_id=tid, action="convert", success=True)
            return True
        except Exception as e:
            console.print(f"[red]转换失败 {material.file_name}: {e}[/red]")
            return False

    result = run_batch(state, task_id, materials, process_material,
                       description="转换处理", resume=resume)

    table = Table(title="转换处理结果")
    table.add_column("项目", style="cyan")
    table.add_column("数量", justify="right")
    table.add_row("总数", str(result["total"]))
    table.add_row("成功", str(result["success"]))
    table.add_row("失败", str(result["failed"]))
    console.print(table)
