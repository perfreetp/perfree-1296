# 数字文化馆素材整理命令行工具 (cultmat)

数字文化馆素材整理命令行工具，供馆员在电脑上批量处理图片、音频、视频和文档素材。

## 功能特性

工具提供 **七组核心命令**：

| 命令 | 功能 |
|------|------|
| `import` | 扫描目录、识别重复文件、导入素材库 |
| `inspect` | 检查格式与清晰度、提取元数据、质量评分 |
| `rename` | 按模板规则批量重命名（支持序号、日期、分类等占位符） |
| `tag` | 补全基础信息（标题、作者、分类等）、自动推断标签、批量打标签 |
| `convert` | 提取封面、图片裁切、加水印、生成预览图/缩略图、格式转换、音频转写、文档OCR |
| `package` | 生成交付清单（CSV/JSON）、打包为ZIP、支持断点续跑 |
| `report` | 终端预览统计、导出HTML/CSV/JSON格式处理报告 |

### 通用能力
- **试运行预览**：所有命令支持 `--dry-run` / `--preview`，不实际修改文件
- **断点续跑**：支持 `--resume` 从中断处继续执行
- **进度显示**：实时进度条 + 详细日志
- **SQLite数据库**：所有素材元数据持久化存储
- **多种素材类型**：图片、音频、视频、文档全覆盖

## 安装

```bash
# 进入项目目录
cd cultmat

# 安装依赖
pip install -e .

# 或使用 pip install
pip install click rich tqdm SQLAlchemy Pillow filetype pydub PyPDF2 python-docx openpyxl mutagen python-slugify jinja2
```

安装完成后，可以使用 `cultmat` 命令。

## 快速开始

```bash
# 查看帮助
cultmat --help

# 1. 导入素材目录（扫描+去重，试运行预览）
cultmat import /path/to/materials --dry-run

# 2. 实际导入并复制到输出目录
cultmat import /path/to/materials --copy -o ./output/imported

# 3. 检查素材质量（自动补充元数据）
cultmat inspect --show-details

# 4. 按规则批量重命名（先预览）
cultmat rename --pattern "{category}_{year}_{index}_{title}" --preview

# 5. 实际重命名
cultmat rename --pattern "{category}_{year}_{index}_{title}" --category 民俗活动

# 6. 批量打标签（自动推断+自定义）
cultmat tag -t "春节" -t "民俗" --auto --category "传统节日"

# 7. 格式转换处理（水印+预览图+缩略图）
cultmat convert --thumbnail --preview --watermark

# 8. 打包交付（生成清单+ZIP）
cultmat package --name "2024春节活动素材" --zip

# 9. 导出处理报告
cultmat report --format html
```

## 命令详解

### import - 导入素材

```bash
cultmat import SOURCE [OPTIONS]
```

| 选项 | 说明 |
|------|------|
| `--recursive/--no-recursive` | 是否递归子目录（默认是） |
| `--copy/--no-copy` | 复制到输出目录 |
| `--move/--no-move` | 移动到输出目录 |
| `-o, --output-dir` | 输出目录 |
| `--deduplicate/--no-deduplicate` | 检测重复文件（默认是） |
| `--dry-run` | 试运行 |
| `--resume` | 断点续跑 |

### inspect - 检查素材

```bash
cultmat inspect [OPTIONS]
```

| 选项 | 说明 |
|------|------|
| `-t, --material-type` | 类型过滤: image/audio/video/document/all |
| `--min-quality` | 最低质量分数阈值 |
| `--show-details` | 显示详细信息 |
| `--fix-missing` | 自动补充缺失元数据 |
| `--dry-run` | 试运行 |

### rename - 批量重命名

```bash
cultmat rename [OPTIONS]
```

**命名模板占位符**：
- `{id}` - 素材ID
- `{index}` - 序号（可配合 `--padding` 补零）
- `{original}` - 原文件名
- `{category}` - 分类
- `{title}` - 标题
- `{author}` - 作者
- `{year}`, `{month}`, `{day}` - 日期
- `{type}` - 素材类型

| 选项 | 说明 |
|------|------|
| `-p, --pattern` | 命名模板，默认 `{category}_{year}_{month}_{original}` |
| `-c, --category` | 分类名称 |
| `--start-index` | 起始序号，默认1 |
| `--padding` | 序号补零位数，默认4 |
| `--in-place/--copy` | 原地重命名或复制后重命名 |
| `--preview` | 预览不执行 |
| `--dry-run` | 试运行 |

### tag - 标签与元数据

```bash
cultmat tag [OPTIONS]
```

| 选项 | 说明 |
|------|------|
| `-t, --tags` | 添加标签，可多次使用 |
| `--from-file` | 从文本文件读取标签（每行一个） |
| `-c, --category` | 设置分类 |
| `--title` | 设置标题模板 |
| `--author` | 设置作者 |
| `--language` | 设置语言 |
| `--copyright` | 设置版权信息 |
| `--auto/--no-auto` | 自动推断标签（默认是） |
| `--clear/--no-clear` | 清除已有标签 |
| `-t, --material-type` | 类型过滤 |
| `--dry-run` | 试运行 |

### convert - 格式转换与处理

```bash
cultmat convert [OPTIONS]
```

| 选项 | 说明 |
|------|------|
| `--thumbnail` | 生成缩略图（默认开启） |
| `--preview` | 生成预览图（默认开启） |
| `--watermark` | 添加水印 |
| `--convert-image` | 转换图片格式 |
| `--convert-audio` | 转换音频格式 |
| `--crop` | 裁切区域，格式 `left,top,right,bottom` |
| `--cover` | 提取文档/视频封面 |
| `--ocr` | 图片/文档OCR识字 |
| `--transcribe` | 音频转写 |
| `-o, --output-dir` | 输出目录 |
| `--dry-run` | 试运行 |
| `--resume` | 断点续跑 |

### package - 打包交付

```bash
cultmat package [OPTIONS]
```

| 选项 | 说明 |
|------|------|
| `-o, --output-dir` | 输出目录 |
| `-n, --name` | 交付包名称 |
| `--format` | 清单格式：csv/json |
| `--zip/--no-zip` | 打包为ZIP（默认是） |
| `--include-originals` | 包含原始文件 |
| `--include-previews` | 包含预览图 |
| `--include-thumbnails` | 包含缩略图 |
| `--include-watermarked` | 包含水印版 |
| `--include-converted` | 包含转换后文件 |
| `--dry-run` | 试运行 |
| `--resume` | 断点续跑 |

### report - 报告导出

```bash
cultmat report [OPTIONS]
```

| 选项 | 说明 |
|------|------|
| `-o, --output-dir` | 报告输出目录 |
| `--format` | 报告格式：html/csv/json |
| `--preview` | 仅在终端预览，不导出文件 |

## 配置文件

默认配置文件位于 `~/.cultmat/config.json`，可自定义：

```json
{
  "db_path": "~/.cultmat/materials.db",
  "preview_dir": "~/.cultmat/previews",
  "output_dir": "./output",
  "workers": 4,
  "rename": {
    "pattern": "{category}_{year}_{month}_{original}",
    "use_slug": true,
    "padding": 4,
    "separator": "_"
  },
  "watermark": {
    "text": "数字文化馆",
    "position": "bottom-right",
    "opacity": 0.5,
    "font_size": 36
  },
  "convert": {
    "image_format": "jpg",
    "image_quality": 85,
    "image_max_size": 1920,
    "thumbnail_size": 300,
    "audio_format": "mp3",
    "audio_bitrate": "192k"
  }
}
```

## 支持的文件格式

| 类型 | 扩展名 |
|------|--------|
| 图片 | `.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.tiff`, `.webp`, `.raw`, `.heic` |
| 音频 | `.mp3`, `.wav`, `.flac`, `.aac`, `.ogg`, `.m4a`, `.wma`, `.opus` |
| 视频 | `.mp4`, `.avi`, `.mov`, `.mkv`, `.wmv`, `.flv`, `.webm`, `.m4v` |
| 文档 | `.pdf`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.ppt`, `.pptx`, `.txt`, `.md`, `.csv`, `.rtf` |

## 项目结构

```
cultmat/
├── __init__.py           # 包初始化
├── cli.py                # CLI入口
├── config.py             # 配置管理
├── models.py             # 数据库模型
├── state.py              # 状态管理与批处理
├── utils.py              # 工具函数
└── commands/
    ├── __init__.py
    ├── import_cmd.py     # 导入命令
    ├── inspect_cmd.py    # 检查命令
    ├── rename_cmd.py     # 重命名命令
    ├── tag_cmd.py        # 标签命令
    ├── convert_cmd.py    # 转换命令
    ├── package_cmd.py    # 打包命令
    └── report_cmd.py     # 报告命令
```

## 典型工作流

```bash
# 完整的素材整理流程
cultmat import ./raw_materials --copy -o ./output/imported
cultmat inspect --show-details
cultmat rename -c "2024春节活动" -p "{category}_{year}_{index}_{title}"
cultmat tag -t "春节" -t "民俗" -t "2024" --auto
cultmat convert --thumbnail --preview --watermark --convert-image
cultmat package -n "2024春节活动素材" --zip
cultmat report --format html
```
