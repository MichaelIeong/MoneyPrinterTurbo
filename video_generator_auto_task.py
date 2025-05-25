import os
import time
from uuid import uuid4
from loguru import logger
import subprocess
from PIL import Image, ImageDraw, ImageFont

from app.config import config
from app.services import llm, task as tm
from app.models.schema import VideoParams, VideoAspect, VideoConcatMode, VideoTransitionMode

TASK_FILE = "tasks.txt"
COMPLETED_FILE = "completed.txt"

# ========== 初始化日志 ==========
logger.remove()
logger.add("video_generator_auto_task.log", level="INFO")


# ========== 读取任务 ==========
def load_tasks():
    if not os.path.exists(TASK_FILE):
        logger.error(f"任务文件不存在：{TASK_FILE}")
        return []

    with open(TASK_FILE, "r", encoding="utf-8") as f:
        tasks = [line.strip() for line in f if line.strip()]
    return tasks


# ========== 读取已完成任务 ==========
def load_completed():
    if not os.path.exists(COMPLETED_FILE):
        return set()
    with open(COMPLETED_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


# ========== 保存完成任務 ==========
def save_completed(subject: str):
    with open(COMPLETED_FILE, "a", encoding="utf-8") as f:
        f.write(subject + "\n")


# ========== 生成封面图像 ==========
def generate_cover_image(title: str, output_path: str):
    width, height = 1080, 1920
    image = Image.new("RGB", (width, height), color=(0, 0, 0))
    draw = ImageDraw.Draw(image)

    font_path = os.path.join("resource", "fonts", "MicrosoftYaHeiBold.ttc")
    if not os.path.exists(font_path):
        logger.error(f"字体文件未找到：{font_path}")
        return

    try:
        font = ImageFont.truetype(font_path, size=80)
    except Exception as e:
        logger.error(f"加载字体失败：{e}")
        return

    lines = []
    max_width = width * 0.9
    words = title.replace("？", "?").split()
    line = ""
    for word in words:
        test_line = f"{line} {word}".strip()
        if draw.textlength(test_line, font=font) <= max_width:
            line = test_line
        else:
            lines.append(line)
            line = word
    lines.append(line)

    total_height = len(lines) * 100
    y = (height - total_height) // 2
    for line in lines:
        text_width = draw.textlength(line, font=font)
        x = (width - text_width) // 2
        draw.text((x, y), line, font=font, fill=(255, 255, 255))
        y += 100

    image.save(output_path)
    logger.info(f"封面已保存：{output_path}")


# ========== 添加封面到视频 ==========
def add_cover_to_video(cover_image_path: str, video_path: str):
    output_path = video_path.replace(".mp4", "_with_cover.mp4")

    temp_frame_video = video_path.replace(".mp4", "_cover_frame.mp4")

    try:
        # 1. 封面图转 1 帧视频（保持视频分辨率）
        cmd1 = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", cover_image_path,
            "-t", "0.04",  # 1帧 (25fps -> 0.04s)
            "-vf", "format=yuv420p",
            "-r", "25",
            "-an",
            temp_frame_video
        ]
        subprocess.run(cmd1, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # 2. 合并封面帧 + 原视频
        cmd2 = [
            "ffmpeg", "-y",
            "-i", f"concat:{temp_frame_video}|{video_path}",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            output_path
        ]
        subprocess.run(cmd2, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if os.path.exists(output_path):
            logger.info(f"✅ 封面帧已插入视频起始位置：{output_path}")
            os.remove(temp_frame_video)
            return output_path
        else:
            logger.error("❌ 合成输出文件未生成")
            return None

    except subprocess.CalledProcessError as e:
        logger.error(f"❌ ffmpeg 插入帧失败：\n{e.stderr.decode(errors='ignore')}")
        return None


# ========== 构建视频参数 ==========
def build_video_params(subject: str) -> VideoParams:
    logger.info(f"正在生成脚本：{subject}")
    script = llm.generate_script(subject)
    terms = llm.generate_terms(subject, script)

    return VideoParams(
        video_subject=subject,
        video_script=script,
        video_terms=", ".join(terms),
        video_source="pexels",
        video_concat_mode=VideoConcatMode.random,
        video_transition_mode=VideoTransitionMode.none,
        video_aspect=VideoAspect.portrait,
        video_clip_duration=3,
        video_count=1,
        voice_name="zh-TW-HsiaoChenNeural-Female",
        voice_volume=1.0,
        voice_rate=1.2,
        bgm_type="random",
        bgm_volume=0.2,
        subtitle_enabled=True,
        font_name="MicrosoftYaHeiBold.ttc",
        font_size=55,
        text_fore_color="#FFFF00",
        stroke_color="#000000",
        stroke_width=1.5,
        subtitle_position="center",
    )


# ========== 主流程 ==========
def main():
    all_tasks = load_tasks()
    completed = load_completed()

    if not all_tasks:
        logger.warning("没有需要执行的任务")
        return

    logger.info("=== 开始批量生成短视频任务 ===")

    for idx, subject in enumerate(all_tasks, 1):
        if subject in completed:
            logger.info(f"[{idx}] 🚫 已完成，跳过：{subject}")
            continue

        logger.info(f"[{idx}] 当前主题：{subject}")
        task_id = str(uuid4())
        try:
            params = build_video_params(subject)
            result = tm.start(task_id=task_id, params=params)

            if result and "videos" in result:
                for video_path in result["videos"]:
                    cover_path = video_path.replace(".mp4", "_cover.jpg")
                    generate_cover_image(subject, cover_path)
                    final_video = add_cover_to_video(cover_path, video_path)
                    if final_video:
                        logger.success(f"[{idx}] ✅ 完成：{final_video}")
                        save_completed(subject)
                    else:
                        logger.error(f"[{idx}] ❌ 封面合成失败：{subject}")
            else:
                logger.error(f"[{idx}] ❌ 视频生成失败：{subject}")

        except Exception as e:
            logger.exception(f"[{idx}] ❌ 出现错误：{e}")
        time.sleep(1)

    logger.info("=== 所有任务完成 ✅ ===")


if __name__ == "__main__":
    main()