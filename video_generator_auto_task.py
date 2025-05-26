import os
import time
from uuid import uuid4
from loguru import logger
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.config import config
from app.services import llm, task as tm
from app.models.schema import VideoParams, VideoAspect, VideoConcatMode, VideoTransitionMode

TASK_FILE = "tasks.txt"
COMPLETED_FILE = "completed.txt"

logger.remove()
logger.add("video_generator_auto_task.log", level="INFO")


def load_tasks():
    if not os.path.exists(TASK_FILE):
        logger.error(f"任务文件不存在：{TASK_FILE}")
        return []

    with open(TASK_FILE, "r", encoding="utf-8") as f:
        tasks = [line.strip() for line in f if line.strip()]
    return tasks


def load_completed():
    if not os.path.exists(COMPLETED_FILE):
        return set()
    with open(COMPLETED_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_completed(subject: str):
    with open(COMPLETED_FILE, "a", encoding="utf-8") as f:
        f.write(subject + "\n")


def build_video_params(subject: str) -> VideoParams:
    logger.info(f"正在生成脚本：{subject}")
    script = llm.generate_script(subject)
    terms = llm.generate_terms(subject, script)

    return VideoParams(
        video_subject=subject,
        video_script=script,
        video_terms=", ".join(terms),
        video_source="pexels",
        video_concat_mode=VideoConcatMode.sequential,
        video_transition_mode=VideoTransitionMode.none,
        video_aspect=VideoAspect.landscape,
        video_clip_duration=10,
        video_count=1,
        voice_name="zh-CN-XiaoxiaoNeural",
        voice_volume=1.0,
        voice_rate=1.4,
        bgm_type="random",
        bgm_volume=0.2,
        subtitle_enabled=True,
        font_name="ZiHunBianTaoTi-2.ttf",
        font_size=75,
        text_fore_color="#FFFF00",
        stroke_color="#000000",
        stroke_width=1.5,
        subtitle_position="center",
    )


def process_task(subject: str, idx: int):
    logger.info(f"[{idx}] 当前主题：{subject}")
    task_id = str(uuid4())
    try:
        params = build_video_params(subject)
        result = tm.start(task_id=task_id, params=params)

        if result and "videos" in result:
            for video_path in result["videos"]:
                logger.success(f"[{idx}] ✅ 完成：{video_path}")
                save_completed(subject)
        else:
            logger.error(f"[{idx}] ❌ 视频生成失败：{subject}")

    except Exception as e:
        logger.exception(f"[{idx}] ❌ 出现错误：{e}")
    time.sleep(1)


def main():
    all_tasks = load_tasks()
    completed = load_completed()

    if not all_tasks:
        logger.warning("没有需要执行的任务")
        return

    logger.info("=== 开始批量生成短视频任务 ===")
    pending_tasks = [(idx, subject) for idx, subject in enumerate(all_tasks, 1) if subject not in completed]

    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = [executor.submit(process_task, subject, idx) for idx, subject in pending_tasks]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error(f"线程执行出错：{e}")

    logger.info("=== 所有任务完成 ✅ ===")


if __name__ == "__main__":
    main()