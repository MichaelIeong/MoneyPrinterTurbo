import os
import subprocess
from loguru import logger
from app.utils import utils


def escape_text_for_ffmpeg(text: str) -> str:
    """
    转义 ffmpeg drawtext 中的特殊字符
    """
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def overlay_title_on_first_frame(
    video_path: str,
    title: str,
    output_path: str = None,
    max_chars_per_line: int = 6,
    fontsize: int = 160,
    fontfile: str = None,
) -> str:
    """
    将标题文字嵌入视频前 0.1 秒画面中，居中显示，适合作为视觉开场。

    参数：
    - video_path: 输入视频路径
    - title: 要叠加的文字
    - output_path: 输出视频路径（默认为原路径 + -with-title）
    - max_chars_per_line: 每行最大字符数
    - fontsize: 字体大小
    - fontfile: 字体路径（默认使用资源字体）
    """
    if not os.path.exists(video_path):
        logger.error(f"❌ 输入视频不存在：{video_path}")
        return ""

    if output_path is None:
        base, ext = os.path.splitext(video_path)
        output_path = f"{base}-with-title{ext}"

    # 自动换行文本
    lines = ["".join(title[i:i + max_chars_per_line]) for i in range(0, len(title), max_chars_per_line)]
    text = "\n".join(lines)
    text = escape_text_for_ffmpeg(text)

    if fontfile is None:
        fontfile = os.path.join(utils.resource_dir("fonts"), "ZiHunBianTaoTi-2.ttf")

    vf = (
        f"drawtext=fontfile='{fontfile}':"
        f"text='{text}':"
        f"fontcolor=#FFCC00:bordercolor=black:borderw=10:"
        f"fontsize={fontsize}:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=60:"
        f"enable='lt(t,0.1)'"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-hwaccel", "cuda",  # 启用 CUDA 解码加速（可选）
        "-i", video_path,
        "-vf", vf,
        "-c:v", "h264_nvenc",  # 使用 NVIDIA NVENC GPU 编码器
        "-preset", "p6",  # 编码速度（p1=最快，p7=最慢最清晰），可调整
        "-c:a", "copy",
        output_path
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.success(f"✅ 成功将标题叠加至视频前 0.1 秒：{output_path}")
        return output_path
    except Exception as e:
        logger.error(f"❌ 标题嵌入失败：{e}")
        return video_path