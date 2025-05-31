import math
import os
import os.path
import re
from os import path
import subprocess

from loguru import logger

from app.config import config
from app.models import const
from app.models.schema import VideoConcatMode, VideoParams
from app.services import llm, material, subtitle, video, voice
from app.services.thumbnail import overlay_title_on_first_frame
from app.services import state as sm
from app.utils import utils


def generate_script(task_id, params):
    logger.info("\n\n## generating video script")
    video_script = (params.video_script or "").strip()
    if not video_script:
        video_script = llm.generate_script(
            video_subject=params.video_subject,
            language=params.video_language,
            paragraph_number=params.paragraph_number,
        )
    else:
        logger.debug(f"video script: \n{video_script}")

    if not video_script:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        logger.error("failed to generate video script.")
        return None

    return video_script


def generate_terms(task_id, params, video_script):
    logger.info("\n\n## generating video terms")
    video_terms = params.video_terms
    if not video_terms:
        video_terms = llm.generate_terms(
            video_subject=params.video_subject, video_script=video_script, amount=5
        )
    else:
        if isinstance(video_terms, str):
            video_terms = [term.strip() for term in re.split(r"[,，]", video_terms)]
        elif isinstance(video_terms, list):
            video_terms = [term.strip() for term in video_terms]
        else:
            raise ValueError("video_terms must be a string or a list of strings.")
        logger.debug(f"video terms: {utils.to_json(video_terms)}")

    if not video_terms:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        logger.error("failed to generate video terms.")
        return None

    return video_terms


def save_script_data(task_id, video_script, video_terms, params):
    script_file = path.join(utils.task_dir(task_id), "script.json")
    script_data = {
        "script": video_script,
        "search_terms": video_terms,
        "params": params,
    }
    with open(script_file, "w", encoding="utf-8") as f:
        f.write(utils.to_json(script_data))


def generate_audio(task_id, params, video_script):
    logger.info("\n\n## generating audio")
    audio_file = path.join(utils.task_dir(task_id), "audio.mp3")
    sub_maker = voice.tts(
        text=video_script,
        voice_name=voice.parse_voice_name(params.voice_name),
        voice_rate=params.voice_rate,
        voice_file=audio_file,
    )
    if sub_maker is None:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        logger.error(
            """failed to generate audio:
1. check if the language of the voice matches the language of the video script.
2. check if the network is available. If you are in China, it is recommended to use a VPN and enable the global traffic mode.
        """.strip()
        )
        return None, None, None

    audio_duration = math.ceil(voice.get_audio_duration(sub_maker))
    return audio_file, audio_duration, sub_maker


def generate_subtitle(task_id, params, video_script, sub_maker, audio_file):
    if not params.subtitle_enabled:
        return ""

    subtitle_path = path.join(utils.task_dir(task_id), "subtitle.srt")
    subtitle_provider = config.app.get("subtitle_provider", "edge").strip().lower()
    logger.info(f"\n\n## generating subtitle, provider: {subtitle_provider}")

    subtitle_fallback = False
    if subtitle_provider == "edge":
        voice.create_subtitle(
            text=video_script, sub_maker=sub_maker, subtitle_file=subtitle_path
        )
        if not os.path.exists(subtitle_path):
            subtitle_fallback = True
            logger.warning("subtitle file not found, fallback to whisper")

    if subtitle_provider == "whisper" or subtitle_fallback:
        subtitle.create(audio_file=audio_file, subtitle_file=subtitle_path)
        logger.info("\n\n## correcting subtitle")
        subtitle.correct(subtitle_file=subtitle_path, video_script=video_script)

    subtitle_lines = subtitle.file_to_subtitles(subtitle_path)
    if not subtitle_lines:
        logger.warning(f"subtitle file is invalid: {subtitle_path}")
        return ""

    return subtitle_path


def get_video_materials(task_id, params, video_terms, audio_duration):
    if params.video_source == "local":
        logger.info("\n\n## preprocess local materials")
        materials = video.preprocess_video(
            materials=params.video_materials, clip_duration=params.video_clip_duration
        )
        if not materials:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            logger.error(
                "no valid materials found, please check the materials and try again."
            )
            return None
        return [mat.url for mat in materials]
    else:
        logger.info(f"\n\n## downloading videos from {params.video_source}")
        downloaded_videos = material.download_videos(
            task_id=task_id,
            search_terms=video_terms,
            source=params.video_source,
            video_aspect=params.video_aspect,
            video_contact_mode=params.video_concat_mode,
            audio_duration=audio_duration * params.video_count,
            max_clip_duration=params.video_clip_duration,
        )
        if not downloaded_videos:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            logger.error(
                "failed to download videos, maybe the network is not available. if you are in China, please use a VPN."
            )
            return None
        return downloaded_videos


def generate_final_videos(task_id, params, downloaded_videos, audio_file, subtitle_path):
    final_video_paths = []
    combined_video_paths = []
    thumbnail_paths = []

    video_concat_mode = (
        params.video_concat_mode if params.video_count == 1 else VideoConcatMode.random
    )
    video_transition_mode = params.video_transition_mode

    _progress = 50
    for i in range(params.video_count):
        idx = i + 1
        # 1) 合并视频片段
        combined_video = path.join(utils.task_dir(task_id), f"combined-{idx}.mp4")
        logger.info(f"\n\n## combining video: {idx} => {combined_video}")
        video.combine_videos(
            combined_video_path=combined_video,
            video_paths=downloaded_videos,
            audio_file=audio_file,
            video_aspect=params.video_aspect,
            video_concat_mode=video_concat_mode,
            video_transition_mode=video_transition_mode,
            max_clip_duration=params.video_clip_duration,
            threads=params.n_threads,
        )
        _progress += 50 / params.video_count / 2
        sm.state.update_task(task_id, progress=_progress)

        # 2) 渲染最终视频
        safe_subject = re.sub(r"[^\w\-]", "_", params.video_subject.strip())
        final_video = path.join(utils.task_dir(task_id), f"{safe_subject}-{idx}.mp4")
        logger.info(f"\n\n## generating video: {idx} => {final_video}")
        video.generate_video(
            video_path=combined_video,
            audio_path=audio_file,
            subtitle_path=subtitle_path,
            output_file=final_video,
            params=params,
        )
        _progress += 50 / params.video_count / 2
        sm.state.update_task(task_id, progress=_progress)

        final_video_paths.append(final_video)
        combined_video_paths.append(combined_video)

        # 3) 提取首帧生成缩略图
        thumb = overlay_title_on_first_frame(final_video, title=params.video_subject)
        if thumb:
            thumbnail_paths.append(thumb)

    return final_video_paths, combined_video_paths, thumbnail_paths


def start(task_id, params: VideoParams, stop_at: str = "video"):
    logger.info(f"start task: {task_id}, stop_at: {stop_at}")
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=5)

    if isinstance(params.video_concat_mode, str):
        params.video_concat_mode = VideoConcatMode(params.video_concat_mode)

    # 1. 脚本
    video_script = generate_script(task_id, params)
    if not video_script or "Error:" in video_script:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        return
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=10)
    if stop_at == "script":
        sm.state.update_task(task_id, state=const.TASK_STATE_COMPLETE, progress=100, script=video_script)
        return {"script": video_script}

    # 2. 关键词
    video_terms = ""
    if params.video_source != "local":
        video_terms = generate_terms(task_id, params, video_script)
        if not video_terms:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return
    save_script_data(task_id, video_script, video_terms, params)
    if stop_at == "terms":
        sm.state.update_task(task_id, state=const.TASK_STATE_COMPLETE, progress=100, terms=video_terms)
        return {"script": video_script, "terms": video_terms}

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=20)

    # 3. 音频
    audio_file, audio_duration, sub_maker = generate_audio(task_id, params, video_script)
    if not audio_file:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        return
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=30)
    if stop_at == "audio":
        sm.state.update_task(task_id, state=const.TASK_STATE_COMPLETE, progress=100,
                             audio_file=audio_file)
        return {"audio_file": audio_file, "audio_duration": audio_duration}

    # 4. 字幕
    subtitle_path = generate_subtitle(task_id, params, video_script, sub_maker, audio_file)
    if stop_at == "subtitle":
        sm.state.update_task(task_id, state=const.TASK_STATE_COMPLETE, progress=100, subtitle_path=subtitle_path)
        return {"subtitle_path": subtitle_path}

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=40)

    # 5. 素材
    downloaded_videos = get_video_materials(task_id, params, video_terms, audio_duration)
    if not downloaded_videos:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        return
    if stop_at == "materials":
        sm.state.update_task(task_id, state=const.TASK_STATE_COMPLETE, progress=100, materials=downloaded_videos)
        return {"materials": downloaded_videos}

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=50)

    # 6. 生成最终视频 + 缩略图
    final_videos, combined_videos, thumbnails = generate_final_videos(
        task_id, params, downloaded_videos, audio_file, subtitle_path
    )
    if not final_videos:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        return

    logger.success(f"task {task_id} finished, generated {len(final_videos)} videos.")

    # 返回结果中新增 thumbnails 字段
    result = {
        "videos": final_videos,
        "combined_videos": combined_videos,
        "thumbnails": thumbnails,
        "script": video_script,
        "terms": video_terms,
        "audio_file": audio_file,
        "audio_duration": audio_duration,
        "subtitle_path": subtitle_path,
        "materials": downloaded_videos,
    }
    sm.state.update_task(task_id, state=const.TASK_STATE_COMPLETE, progress=100, **result)
    return result


if __name__ == "__main__":
    task_id = "task_id"
    params = VideoParams(
        video_subject="金钱的作用",
        voice_name="zh-CN-XiaoyiNeural-Female",
        voice_rate=1.0,
    )
    start(task_id, params, stop_at="video")