import os
import subprocess
from loguru import logger
from app.utils import utils


def escape_text_for_ffmpeg(text: str) -> str:
    """
    转义 ffmpeg drawtext 中用到的特殊字符
    """
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def extract_first_frame(
        video_path: str,
        title: str,
        thumbnail_path: str = None,
        max_chars_per_line: int = 6,
        fontsize: int = 160,
        fontfile: str = None,
) -> str:
    """
    1) 从 video_path 中提取第一帧，保存为 thumbnail_path。
    2) 在 thumbnail_path 中间叠加 title 文本。

    参数:
      - title: 要叠加的文字
      - max_chars_per_line: 每行最多字符数，超出则换行
      - fontsize: 文本大小
      - fontfile: 字体文件路径，不传则从 utils 中查找默认字体

    返回实际保存的 thumbnail_path，失败则返回空字符串。
    """
    # --- 第一步：提取首帧 ---
    if thumbnail_path is None:
        base, _ = os.path.splitext(video_path)
        thumbnail_path = f"{base}-thumbnail.jpg"

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i", video_path,
                "-frames:v", "1",
                thumbnail_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        logger.error(f"❌ 缩略图提取失败 ({video_path})：{e}")
        return ""

    # --- 第二步：在图片上叠加文字 ---
    # 拆分文字、自动换行
    chars = list(title)
    lines = [
        "".join(chars[i: i + max_chars_per_line])
        for i in range(0, len(chars), max_chars_per_line)
    ]
    text = "\n".join(lines)
    text = escape_text_for_ffmpeg(text)

    # 字体文件
    if fontfile is None:
        fontfile = os.path.join(utils.resource_dir("fonts"), "ZiHunBianTaoTi-2.ttf")

    vf = (
        f"drawtext=fontfile='{fontfile}':"
        f"text='{text}':"
        f"fontcolor=#FFFF66:bordercolor=black:borderw=10:"
        f"fontsize={fontsize}:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=60"
    )

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i", thumbnail_path,
                "-vf", vf,
                thumbnail_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.success(f"🎨 缩略图叠加标题成功：{thumbnail_path}")
        return thumbnail_path

    except Exception as e:
        logger.error(f"❌ 缩略图叠加标题失败：{e}")
        return thumbnail_path