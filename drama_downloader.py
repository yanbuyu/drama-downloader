#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""红果短剧 Python 直连版：独立搜索、设备注册与池化、V5取流、多线程下载。

基于 ch6vip/novel-sound（GPL-3.0）公开源码移植；不依赖原App、Rust或Frida。
依赖：pip install -r requirements.txt；自动查找系统 ffmpeg 或 imageio-ffmpeg 内置可执行文件。
按上游接口实际返回的数据处理，不增加针对付费或非付费内容的客户端筛选。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from drama_direct import DevicePool, DirectClient, DirectError

import requests

TIMEOUT = (8, 45)
VIDEO_TIMEOUT = (15, 90)
CHUNK = 512 * 1024
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


class DramaError(RuntimeError):
    pass


def first(record: dict, keys: tuple[str, ...], default="") -> str:
    for key in keys:
        v = record.get(key)
        if v is not None and str(v).strip() and str(v).strip() != "0":
            return str(v).strip()
    return default


def valid_url(url: str, *, local: bool = False) -> bool:
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
            return False
        if local and u.scheme != "http":
            return False
        return True
    except ValueError:
        return False


def safe_name(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")
    return (name[:100].strip(" .") or "未命名")

def episode_file_stem(number: int, quality: str) -> str:
    """Stable filename based on the authoritative vid_index, not API description."""
    return f"{number:03d}-第{number}集[{quality}]"


def atomic_json(path: Path, data: dict | list):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp, path)


class DirectPoolAdapter:
    """复用原CLI的接口形状，全部请求直接发往上游，非Rust本地服务。"""
    def __init__(self, devices: DevicePool, *, mock_origin=None):
        self.devices = devices
        self.client = DirectClient(devices, mock_origin=mock_origin)

    def request(self, path: str, params=None, *, attempts=2):
        params = params or {}
        if path == "/api/v1/search":
            return self.client.search(str(params.get('query', '')), int(params.get('offset', 0)),
                                      int(params.get('count', 10)), params.get('tab_type', 11))
        m = re.fullmatch(r'/api/v1/series/(\d+)/episodes', path)
        if m:
            return self.client.video_series(m.group(1))
        m = re.fullmatch(r'/api/v1/series/(\d+)', path)
        if m:
            return self.client.info(m.group(1))
        m = re.fullmatch(r'/api/v1/(?:books/(\d+)/directory|manga/videos/(\d+))', path)
        if m:
            return self.client.directory(m.group(1) or m.group(2))
        m = re.fullmatch(r'/api/v1/videos/(\d+)', path)
        if m:
            return self.client.source(m.group(1))
        raise DramaError(f'未知接口路径：{path}')

    def search_heats(self, ids):
        """Fill missing heat using the upstream player detail, 10 IDs per call."""
        ids = list(dict.fromkeys(str(x) for x in ids if str(x).isdigit()))
        resolved = {}
        if not ids:
            return resolved
        for start in range(0, len(ids), 10):
            chunk = ids[start:start + 10]
            try:
                details = self.client.series_heat_batch(chunk)
            except (requests.RequestException, DirectError, ValueError, TypeError):
                details = {}
            for sid in chunk:
                heat = heat_from_series_detail(details.get(sid))
                if heat:
                    resolved[sid] = heat
        missing = [sid for sid in ids if sid not in resolved]
        if missing:
            def fetch_one(sid):
                try:
                    return sid, heat_from_series_detail(self.client.info(sid))
                except (requests.RequestException, DirectError, ValueError, TypeError):
                    return sid, ""
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(missing))) as executor:
                for sid, heat in executor.map(fetch_one, missing):
                    if heat:
                        resolved[sid] = heat
        return resolved

    def health(self):
        count = len(self.devices.status())
        return [{'name': 'Python直接连接上游', 'status': 'ok' if count else 'empty',
                 'devices': count}]


@dataclass(frozen=True)
class Episode:
    number: int
    id: str
    title: str
    disabled: bool = False
    trailer: bool = False
    duration: int = 0


def parse_episodes(data: Any) -> list[Episode]:
    """支持旧版桥接目录和新版 reading 原始 item_data_list/episodes。"""
    def locate(obj: Any, depth=0):
        if depth > 7:
            return None
        if isinstance(obj, list):
            if obj and all(isinstance(x, dict) for x in obj):
                if any(first(x, ("item_id", "itemId", "video_id", "episode_id", "vid")) for x in obj):
                    return obj
            for child in obj:
                found = locate(child, depth + 1)
                if found is not None:
                    return found
        if isinstance(obj, dict):
            volumes = obj.get("chapterListWithVolume")
            if isinstance(volumes, list) and volumes:
                flattened = []
                for vol in volumes:
                    if isinstance(vol, list):
                        flattened += vol
                    elif isinstance(vol, dict):
                        flattened += vol.get("chapterList", [])
                if flattened:
                    return flattened
            for key in ("episodes", "item_data_list", "chapterList", "lists", "item_list"):
                v = obj.get(key)
                if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                    return v
            for child in obj.values():
                found = locate(child, depth + 1)
                if found is not None:
                    return found
        return None

    raw = locate(data)
    if not raw:
        return []
    result = []
    seen = set()
    for i, m in enumerate(raw, start=1):
        if not isinstance(m, dict):
            continue
        vid = first(m, ("itemId", "item_id", "chapter_id", "video_id", "vid", "episode_id", "id"))
        if not vid or vid in seen:
            continue
        seen.add(vid)
        title = first(m, ("title", "episode_title", "chapter_title", "name", "item_title"), f"第{i}集")
        disabled = str(m.get("disable_play", m.get("disablePlay", ""))).lower() in ("true", "1")
        disabled |= str(m.get("item_status", "0")) not in ("0", "", "none")
        trailer = str(m.get("is_preview_material", m.get("is_trailer", False))).lower() in ("true", "1")
        try:
            duration = int(m.get("duration", m.get("duration_seconds", 0)))
        except (ValueError, TypeError):
            duration = 0
        result.append(Episode(len(result) + 1, vid, title, disabled, trailer, duration))
    return result



def parse_short_drama_episodes(payload: dict, series_id: str) -> list[Episode]:
    """Parse the short-drama video_list using real vid_index, never list position."""
    data = payload.get('data') if isinstance(payload, dict) else None
    series = data.get(str(series_id)) if isinstance(data, dict) else None
    if not isinstance(series, dict) and isinstance(data, dict):
        series = data  # single video_detail response: data.video_data
    video = series.get('video_data') if isinstance(series, dict) else None
    rows = video.get('video_list') if isinstance(video, dict) else None
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list) or not rows:
        raise DramaError('短剧接口没有返回 video_list，无法确定真实集号')
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        vid = first(row, ('vid', 'video_id', 'item_id'))
        raw_index = row.get('vid_index')
        if not vid or raw_index is None:
            continue
        try:
            index = int(raw_index)
        except (ValueError, TypeError):
            continue
        title = first(row, ('title', 'episode_title', 'name'), '')
        try:
            duration = int(row.get('duration') or 0)
        except (ValueError, TypeError):
            duration = 0
        parsed.append((index, str(vid), title, duration, row))
    if not parsed:
        raise DramaError('短剧接口没有有效的 vid / vid_index 对应关系')
    indices = [x[0] for x in parsed]
    if min(indices) == 0 and set(indices) == set(range(len(indices))):
        offset = 1   # Only accept an unambiguous, fully zero-based index sequence.
    else:
        offset = 0
    episodes = []
    seen_numbers = set()
    seen_videos = set()
    for index, vid, title, duration, row in sorted(parsed, key=lambda x: x[0]):
        number = index + offset
        if number < 1 or number in seen_numbers or vid in seen_videos:
            raise DramaError('短剧集号重复或不合法，拒绝错位下载')
        seen_numbers.add(number)
        seen_videos.add(vid)
        # vid_index is authoritative. A generic numbering title must not
        # contradict it; retain actual descriptive episode titles as-is.
        title_match = re.fullmatch(r'第\s*(\d+)\s*集', title)
        if title_match and int(title_match.group(1)) != number:
            title = f'第{number}集'
        if not title:
            title = f'第{number}集'
        trailer = str(row.get('is_preview_material', row.get('is_trailer', False))).lower() in ('true', '1')
        disabled = str(row.get('disable_play', row.get('disablePlay', False))).lower() in ('true', '1')
        episodes.append(Episode(number, vid, title, disabled, trailer, duration))
    return episodes

def search_metric(item: dict, parent: dict, kind: str) -> str:
    """Use upstream heat labels/numbers only; play counts are NOT heat."""
    sources = [item, parent]
    # The source app keeps official "9320万热度" text in secondary_info_list.
    for obj in sources:
        for key in ("secondary_info_list", "secondary_infos", "recommend_reason_list"):
            entries = obj.get(key)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                value = entry.get("content")
                if isinstance(value, dict):
                    value = value.get("text")
                if isinstance(value, str) and "热度" in value and value.strip():
                    text = value.strip()
                    return text if kind == "heat" else ""
    if kind != "heat":
        return ""
    for obj in sources:
        for field in ("heat", "heat_value", "heat_score", "hot_value", "hot_score",
                      "hotness", "popularity", "popularity_score", "heat_index", "hot_index"):
            value = obj.get(field)
            if value is None or value == "":
                continue
            if isinstance(value, dict):
                value = value.get("text") or value.get("value")
            if value is None or value == "":
                continue
            return str(value).strip()
    return ""



def heat_from_series_detail(payload: dict) -> str:
    """Use official heat text / hot_score, never play counts."""
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data", payload)
    sources = []
    if isinstance(data, dict):
        sources.append(data)
        sources.extend(value for value in data.values() if isinstance(value, dict))
    for source in sources:
        video = source.get("video_data")
        if not isinstance(video, dict):
            continue
        captions = video.get("series_sub_title_list")
        if isinstance(captions, list):
            for caption in captions:
                if isinstance(caption, str) and "热度" in caption and caption.strip():
                    return caption.strip()
        raw = video.get("hot_score")
        if raw is None or raw == "":
            continue
        if isinstance(raw, str) and "热度" in raw:
            return raw.strip()
        try:
            number = int(raw)
        except (ValueError, TypeError):
            continue
        if number < 0:
            continue
        if number >= 10000:
            return f"{number / 10000:.2f}".rstrip("0").rstrip(".") + "万热度"
        return f"{number}热度"
    return ""

def search_display_heat(item: dict) -> str:
    """Always include a heat slot; do not substitute a play count."""
    heat = str(item.get("heat") or "").strip()
    if not heat:
        return "热度：未返回"
    return heat if "热度" in heat else f"热度：{heat}"


def parse_search(data: dict) -> list[dict]:
    """Parse media items from ALL returned tabs, without a VIP/paywall filter."""
    inner = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(inner, dict):
        return []
    tabs = inner.get("search_tabs")
    if not isinstance(tabs, list) or not tabs:
        tabs = [inner]
    found = {}
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        tab_label = str(tab.get("title") or tab.get("tab_type") or "综合")
        cells = tab.get("data", tab.get("items", []))
        if not isinstance(cells, list):
            continue
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            # The upstream returns both drama video_data and general book_data
            # media lists, including film cards. Keep the other shapes too.
            children = []
            for key in ("video_data", "book_data"):
                values = cell.get(key)
                if isinstance(values, list) and values:
                    children.extend(x for x in values if isinstance(x, dict))
            if not children:
                children = [cell]
            for child in children:
                merged = dict(cell)
                merged.update(child)
                detail = merged.get("video_detail")
                if isinstance(detail, dict):
                    merged.update(detail)
                book = merged.get("book_data")
                if isinstance(book, dict):
                    merged.update(book)
                sid = first(merged, ("series_id_str", "pseries_id", "video_series_id",
                                     "series_id", "movie_id", "film_id", "video_id",
                                     "vid", "book_id", "item_id", "id", "cell_id"))
                if not sid:
                    continue
                title = ""
                highlight = merged.get("search_high_light")
                if isinstance(highlight, dict):
                    label = highlight.get("title")
                    if isinstance(label, dict):
                        title = str(label.get("text") or "").strip()
                if not title:
                    for field in ("series_title", "title", "name", "movie_title",
                                  "film_name", "raw_book_name", "book_name", "cell_name"):
                        label = merged.get(field)
                        if isinstance(label, dict):
                            label = label.get("text")
                        if label is not None and str(label).strip():
                            title = str(label).strip()
                            break
                if not title:
                    continue
                heat = search_metric(merged, cell, "heat")
                entry = {
                    "series_id": sid, "title": title,
                    "episodes": first(merged, ("episode_cnt", "episode_count", "ep", "serial_count")),
                    "intro": first(merged, ("series_intro", "intro", "abstract")),
                    "heat": heat, "tab": tab_label,
                }
                old = found.get(sid)
                if old is None:
                    found[sid] = entry
                else:
                    # General and drama search may return the same work:
                    # preserve whichever contains more actual metadata.
                    for field in ("title", "episodes", "intro", "heat"):
                        if not old.get(field) and entry.get(field):
                            old[field] = entry[field]
    return list(found.values())


def parse_series_info(result: dict, fallback_id: str) -> dict:
    data = result.get("data", {})
    video = ((data.get(str(fallback_id)) or {}).get("video_data")
             if isinstance(data, dict) and isinstance(data.get(str(fallback_id)), dict)
             else data.get("video_data", {}) if isinstance(data, dict) else {})
    if not isinstance(video, dict):
        video = {}
    casts = []
    for actor in video.get("celebrities", []):
        if isinstance(actor, dict):
            casts.append({"actor": str(actor.get("nickname") or ""), "role": str(actor.get("role_name") or "")})
    return {
        "series_id": first(video, ("series_id_str", "series_id"), fallback_id),
        "title": first(video, ("series_title",), fallback_id),
        "intro": first(video, ("series_intro",)),
        "cover": first(video, ("series_cover",)),
        "episode_count": video.get("episode_cnt", 0),
        "play_count": video.get("series_play_cnt", 0),
        "follower_count": video.get("followed_cnt", 0),
        "status": video.get("series_status"),
        "cast": casts,
    }


def parse_range(spec: str, total: int) -> list[int]:
    if total < 1:
        raise DramaError("剧集目录为空")
    if spec.strip().lower() == "all":
        return list(range(1, total + 1))
    selected = set()
    for piece in spec.split(","):
        piece = piece.strip()
        match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d*)\s*)?", piece)
        if not match:
            raise DramaError(f"不合法的集数范围：{piece!r}，例如 1-10,12,20-")
        lo = int(match.group(1))
        hi = int(match.group(2)) if match.group(2) else (total if "-" in piece else lo)
        if lo < 1 or hi < lo or hi > total:
            raise DramaError(f"集数范围 {piece} 超出 1-{total}")
        selected.update(range(lo, hi + 1))
    return sorted(selected)


def choose_variant(source: dict, quality: str) -> dict:
    variants = [v for v in source.get("variants", []) if isinstance(v, dict) and v.get("url")]
    if not variants:
        u = source.get("video_url", "")
        if not u:
            raise DramaError("视频接口没有返回可用地址")
        return {"url": u, "key_hex": source.get("key_hex", ""), "name": "单流", "quality_height": 0}
    for v in variants:
        try:
            v["_quality"] = int(v.get("quality_height", 0))
        except (ValueError, TypeError):
            v["_quality"] = 0
    known = [v for v in variants if v["_quality"] > 0]
    if quality == "best":
        return max(known, key=lambda v: v["_quality"]) if known else variants[0]
    goal = int(quality)
    if not known:
        return variants[0]
    eligible = [v for v in known if v["_quality"] <= goal]
    return max(eligible, key=lambda v: v["_quality"]) if eligible else min(known, key=lambda v: v["_quality"])


def looks_cenc(path: Path) -> bool:
    """快速检查 MP4 CENC 常用 box；真实验证由 ffmpeg 解封装执行。"""
    with path.open("rb") as f:
        head = f.read(1024 * 1024)
    return any(flag in head for flag in (b"encv", b"enca", b"senc", b"pssh", b"tenc"))


def find_ffmpeg() -> str | None:
    """优先使用 PATH，未安装系统 FFmpeg 时使用 imageio-ffmpeg 随 wheel 分发的程序。"""
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and Path(path).is_file():
            return path
    except (ImportError, RuntimeError, OSError):
        pass
    return None


def completed_video_cache(part: Path) -> bool:
    """旧版已完成下载但解密出错：仅文件长度有明确记录时才可安全复用。"""
    if not part.is_file() or part.stat().st_size < 64:
        return False
    sidecar = Path(str(part) + ".json")
    try:
        saved = json.loads(sidecar.read_text("utf-8"))
        total = saved.get("total")
        if not isinstance(total, int) or total < 64 or part.stat().st_size != total:
            return False
        with part.open("rb") as stream:
            return b"ftyp" in stream.read(32)
    except (OSError, ValueError, TypeError):
        return False


def ffmpeg_decrypt(src: Path, dst: Path, key: str):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise DramaError("未找到 FFmpeg。请执行 python -m pip install imageio-ffmpeg；.part 缓存已保留")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", key):
        raise DramaError("加密视频缺少有效的 16 字节内容密钥，不能导出为可播放 MP4")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-decryption_key", key,
           "-i", str(src), "-map", "0", "-c", "copy", "-movflags", "+faststart", str(dst)]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired as e:
        dst.unlink(missing_ok=True)
        raise DramaError("FFmpeg 解密超时，原始 .part 缓存已保留") from e
    except OSError as e:
        dst.unlink(missing_ok=True)
        raise DramaError(f"FFmpeg 无法启动：{e}；原始 .part 缓存已保留") from e
    if done.returncode != 0:
        dst.unlink(missing_ok=True)
        raise DramaError("FFmpeg 解封装失败：" + done.stderr.strip()[-400:])


def verify_mp4(path: Path):
    if not path.is_file() or path.stat().st_size < 64:
        raise DramaError("文件为空或异常小")
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            p = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                               capture_output=True, text=True, timeout=25)
        except subprocess.TimeoutExpired as e:
            raise DramaError("ffprobe 校验超时") from e
        if p.returncode != 0 or not p.stdout.strip():
            raise DramaError("下载文件无法识别视频流")
    else:
        with path.open("rb") as f:
            if b"ftyp" not in f.read(32):
                raise DramaError("文件头不是有效 MP4（建议安装 ffprobe 验证）")


def transfer(url: str, dest: Path, retries: int = 3) -> int:
    if not valid_url(url):
        raise DramaError("下载地址不是 HTTP(S) URL")
    dest.parent.mkdir(parents=True, exist_ok=True)
    state_path = Path(str(dest) + ".json")
    fingerprint = hashlib.sha256(url.encode("utf-8")).hexdigest()
    existing = json.loads(state_path.read_text("utf-8")) if state_path.exists() else {}
    if existing.get("url_hash") != fingerprint:
        dest.unlink(missing_ok=True)
        existing = {}
    last_error = None
    for attempt in range(retries):
        offset = dest.stat().st_size if dest.exists() else 0
        headers = {"User-Agent": UA}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            with requests.get(url, headers=headers, stream=True, timeout=VIDEO_TIMEOUT) as response:
                if response.status_code == 416 and offset:
                    expected = existing.get("total")
                    if isinstance(expected, int) and offset == expected:
                        return offset
                    dest.unlink(missing_ok=True)
                    continue
                response.raise_for_status()
                if response.status_code not in (200, 206):
                    raise DramaError(f"HTTP {response.status_code}")
                if offset and response.status_code == 206:
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        raise DramaError("服务端返回的 Range 起点不匹配，拒绝拼接")
                elif offset and response.status_code == 200:
                    offset = 0  # 不支持 Range 的 CDN，从头下载
                content_type = response.headers.get("Content-Type", "").lower()
                if "application/json" in content_type or "text/html" in content_type:
                    raise DramaError("CDN 返回的不是视频文件，可能已过期或鉴权失败")
                length = response.headers.get("Content-Length")
                total = offset + int(length) if length and length.isdigit() else None
                atomic_json(state_path, {"url_hash": fingerprint, "total": total})
                with dest.open("ab" if offset else "wb") as f:
                    for chunk in response.iter_content(CHUNK):
                        if chunk:
                            f.write(chunk)
                received = dest.stat().st_size
                if total is not None and received != total:
                    raise DramaError(f"视频长度不完整：{received}/{total}")
                return received
        except (requests.RequestException, OSError, ValueError, DramaError) as e:
            last_error = e
            if attempt + 1 < retries:
                time.sleep(min(2 ** attempt, 3))
    raise DramaError(f"下载失败：{last_error}")


class Downloader:
    def __init__(self, pool: DirectPoolAdapter, out: Path, workers: int, quality: str, raw: bool = False):
        self.pool = pool
        self.out = out
        self.workers = workers
        self.quality = quality
        self.raw = raw
        self.log_lock = threading.Lock()

    def log(self, message):
        with self.log_lock:
            print(message, flush=True)

    def details(self, series_id: str):
        # The reading/bookapi/directory endpoint is for novel chapters.
        # Its positional list is not trustworthy for short-drama episodes.
        payload = self.pool.request(f"/api/v1/series/{series_id}/episodes")
        episodes = parse_short_drama_episodes(payload, series_id)
        info = parse_series_info(payload, series_id)
        info.setdefault("series_id", series_id)
        info.setdefault("title", series_id)
        info["episodes"] = [asdict(ep) for ep in episodes]
        info["episode_count_directory"] = len(episodes)
        return info, episodes

    def backup_mismatched_legacy_files(self, folder: Path, numbers: set[int]):
        """Preserve previous index/title-mismatched MP4s without treating as valid episodes."""
        for file in folder.glob('*.mp4'):
            match = re.fullmatch(r'(\d+)-第(\d+)集\[[^\]]+\]\.mp4', file.name)
            if not match:
                continue
            index, title_number = map(int, match.groups())
            if index not in numbers or index == title_number:
                continue
            backup = folder / '_wrong_episode_backup'
            backup.mkdir(exist_ok=True)
            dest = backup / file.name
            n = 1
            while dest.exists():
                dest = backup / f'{file.stem}-{n}{file.suffix}'
                n += 1
            file.replace(dest)
            self.log(f'[目录纠错] 旧文件 {file.name} 的集号不一致，已保留到 {backup.name}/')

    def migrate_legacy_filenames(self, folder: Path, episodes: list[Episode]):
        """Rename old synopsis-based files only when the saved video ID matches.

        The previous release saved ``episodes`` (number, id, title) to series.json.
        This evidence is necessary to avoid re-labeling a wrongly downloaded
        episode from earlier directory-order bugs as an actual episode.
        """
        metadata = folder / "series.json"
        if not metadata.is_file():
            return
        try:
            old = json.loads(metadata.read_text(encoding="utf-8"))
            rows = old.get("episodes", []) if isinstance(old, dict) else []
            previous = {
                (int(row["number"]), str(row["id"])): str(row["title"])
                for row in rows
                if isinstance(row, dict) and all(k in row for k in ("number", "id", "title"))
            }
        except (OSError, ValueError, TypeError, KeyError):
            return
        for episode in episodes:
            old_title = previous.get((episode.number, episode.id))
            if not old_title:
                continue
            old_stem = safe_name(f"{episode.number:03d}-{old_title}") + f"[{self.quality}]"
            new_stem = episode_file_stem(episode.number, self.quality)
            if old_stem == new_stem:
                continue
            # Finished files, raw CENC exports, and matching partial downloads.
            # Never replace an existing destination, and never touch an entry
            # whose saved video ID differs from the current episode's ID.
            for suffix in (".mp4", ".cenc.mp4", ".mp4.part", ".mp4.part.json", ".decrypted.tmp.mp4"):
                source = folder / (old_stem + suffix)
                target = folder / (new_stem + suffix)
                if not source.is_file() or target.exists():
                    continue
                if suffix == ".mp4.part.json":
                    # A sidecar only belongs with its successfully moved part.
                    if (folder / (old_stem + ".mp4.part")).exists():
                        continue
                    if not (folder / (new_stem + ".mp4.part")).exists():
                        continue
                source.rename(target)
                self.log(f"[文件整理] {source.name} → {target.name}")

    def save_metadata(self, info: dict) -> Path:
        folder = self.out / safe_name(info.get("title", ""))
        atomic_json(folder / "series.json", info)
        return folder

    def one(self, ep: Episode, folder: Path):
        name = episode_file_stem(ep.number, self.quality)
        output = folder / (name + ".mp4")
        if output.exists():
            try:
                verify_mp4(output)
                return f"[{ep.number}] 已存在：{output.name}"
            except DramaError:
                output.rename(output.with_suffix(".invalid"))
        source = self.pool.request(f"/api/v1/videos/{ep.id}", {"mode": "stream"})
        video = source.get("data", source)
        if not isinstance(video, dict):
            raise DramaError("取流结果格式异常")
        variant = choose_variant(video, self.quality)
        stream_url = str(variant.get("url", ""))
        key_hex = str(variant.get("key_hex") or video.get("key_hex") or "")
        resolution = variant.get("quality_height") or variant.get("name") or "单流"
        # 始终先保存 .part，完整校验后再原子提交到最终路径。
        part = folder / (name + ".mp4.part")
        # 如果上次已经完成传输、只在 FFmpeg 解密阶段报错，即使 CDN 链接
        # 换了临时签名也先复用整份 .part；绝不拿旧文件和新 Range 拼接。
        if completed_video_cache(part):
            transferred = part.stat().st_size
            self.log(f"[{ep.number}] 复用已下载的完整 .part，直接进行后处理")
        else:
            transferred = transfer(stream_url, part)
        encrypted = looks_cenc(part)
        if self.raw:
            suffix = ".cenc.mp4" if encrypted else ".mp4"
            saved = folder / (name + suffix)
            os.replace(part, saved)
            Path(str(part) + ".json").unlink(missing_ok=True)
            if not encrypted:
                verify_mp4(saved)
            return f"[{ep.number}] 原始视频已保存：{saved.name} ({transferred / 1048576:.1f}MiB / {resolution})"
        if encrypted:
            decrypted = folder / (name + ".decrypted.tmp.mp4")
            ffmpeg_decrypt(part, decrypted, key_hex)
            if looks_cenc(decrypted):
                decrypted.unlink(missing_ok=True)
                raise DramaError("解密后仍包含加密轨道，已保留原始分片")
            verify_mp4(decrypted)
            os.replace(decrypted, output)
            part.unlink(missing_ok=True)
        else:
            verify_mp4(part)
            os.replace(part, output)
        Path(str(part) + ".json").unlink(missing_ok=True)
        return f"[{ep.number}] 完成：{output.name} ({transferred / 1048576:.1f}MiB / {resolution})"

    def download(self, series_id: str, spec: str):
        info, episodes = self.details(series_id)
        folder = self.out / safe_name(info.get("title", ""))
        by_number = {ep.number: ep for ep in episodes}
        total = max(len(episodes), max(by_number))
        try:
            total = max(total, int(info.get('episode_count') or 0))
        except (ValueError, TypeError):
            pass
        numbers = parse_range(spec, total)
        missing = [number for number in numbers if number not in by_number]
        if missing:
            raise DramaError(f'剧集目录缺少真实集号：{missing[:20]}，拒绝以其他集错位替代')
        selected = [by_number[number] for number in numbers]
        self.backup_mismatched_legacy_files(folder, set(numbers))
        self.migrate_legacy_filenames(folder, selected)
        self.save_metadata(info)
        self.log(f"剧名：{info.get('title')} | 全 {len(episodes)} 集 | 下载 {len(selected)} 集 | 线程 {self.workers} | 清晰度 {self.quality}")
        self.log(f"保存目录：{folder}")
        failed = []
        finished = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(self.one, ep, folder): ep for ep in selected}
            for future in concurrent.futures.as_completed(futures):
                ep = futures[future]
                try:
                    self.log(future.result())
                except Exception as e:
                    self.log(f"[{ep.number}] 失败：{e}")
                    failed.append({"number": ep.number, "id": ep.id, "error": str(e)})
                finished += 1
                self.log(f"[进度] {finished}/{len(selected)} ({finished / max(1,len(selected)):.0%}) · 失败 {len(failed)}")
        if failed:
            atomic_json(folder / "failed.json", failed)
            self.log(f"完成：成功 {len(selected) - len(failed)}，失败 {len(failed)}；失败清单：failed.json")
            return 1
        (folder / "failed.json").unlink(missing_ok=True)
        self.log(f"下载完成：{len(selected)} 集")
        return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="独立 Python 红果短剧下载器（不依赖Rust/Frida/原App）")
    parser.add_argument("--pool", default=str(Path(__file__).resolve().with_name("device_pool.json")), help="设备池JSON（默认脚本所在项目的 device_pool.json；可导入旧设备池）")
    parser.add_argument("--out", default="downloads", help="视频保存目录")
    parser.add_argument("--devices", type=int, default=5, help="首次注册红果播放设备数量，默认5（1–20；已有注册缓存优先复用）")
    parser.add_argument("--mock-origin", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("selftest", help="离线运行协议签名/加密固定向量测试")
    sub.add_parser("devices", help="查看当前已注册设备池")
    reg = sub.add_parser("register", help="注册指定数量的真实设备并缓存（默认红果播放设备）")
    reg.add_argument("--app", choices=("8662","1967"), default="8662", help="设备应用类型：8662红果播放（默认）；1967原有番茄设备")
    search = sub.add_parser("search", help="按名称搜索短剧")
    search.add_argument("keyword")
    search.add_argument("--pages", type=int, default=1)
    search.add_argument("--tab", default="all", help="搜索标签：all（综合+短剧）、1（综合）、11（短剧）或上游其他类型")
    info = sub.add_parser("info", help="获取详细信息、演员、剧集目录，保存JSON")
    info.add_argument("series_id")
    dl = sub.add_parser("download", help="指定集数范围和画质，多线程下载MP4")
    dl.add_argument("series_id")
    dl.add_argument("--episodes", default="all", help="集数范围：1-10,12,20- 或 all")
    dl.add_argument("--quality", default="best", help="best 或目标短边像素数，例如 720、1080、1440")
    dl.add_argument("--workers", type=int, default=4, help="并发下载集数，默认4")
    dl.add_argument("--raw", action="store_true", help="保存未经处理的原始缓存（可能为CENC加密）")
    dl.add_argument("--include-disabled", action="store_true", help=argparse.SUPPRESS)  # 旧参数兼容；不再预过滤剧集
    args = parser.parse_args(argv)
    if getattr(args, "workers", 4) < 1:
        parser.error("--workers 必须大于 0")
    if getattr(args, "pages", 1) < 1:
        parser.error("--pages 必须大于 0")
    if not 1 <= args.devices <= 20:
        parser.error("--devices 必须在 1–20 之间（同原仓库上限）")
    if args.command == "download" and args.quality != "best":
        if not args.quality.isascii() or not args.quality.isdecimal() or int(args.quality) <= 0:
            parser.error("--quality 必须为 best 或正整数（目标短边像素数）")
    devices = DevicePool(args.pool, desired=args.devices, mock_origin=args.mock_origin)
    if args.command == "selftest":
        from drama_sign import sm3, simon_enc, ladon, _sm3_python
        from drama_direct import tt_encrypt, derive_spade_key
        from drama_v5 import sign_v5
        import struct, gzip
        assert sm3(b'abc').hex() == '66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0'
        assert _sm3_python(b'abc') == sm3(b'abc')
        assert ladon(1700000000, 1611921764, 1967, bytes.fromhex('01020304')) == 'AQIDBKM7Y7bWnOIUXmTybD6/MpGbuge4sBwUJsyHVivhtMsH'
        pt=struct.pack('<QQ',1234567890123456789,9876543210987654321)
        key=struct.pack('<QQQQ',1111111111111111111,2222222222222222222,3333333333333333333,4444444444444444444)
        assert struct.unpack('<QQ',simon_enc(pt,key)) == (6071150433487837324,2027321285990159935)
        assert derive_spade_key('kbwf80+1N+V9nwHQSp431GWvLeBlqizTYJ0o5FOrHuZhqimysg==')=='4990a92de837e29e18031a370ab744e6'
        assert tt_encrypt(b'hello-tt-encrypt-payload',bytes(range(32))).hex() == ('746305100000000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f9c2938fa4a012faf0ecd9d31020e00f9748841c73838f76c10a9f9084bcfc717bc3cb6085a81a491f6e9b3d47a574b96bd818707ca911fc89bfad9a68edc174aa2f2dbc4ea60415c1e06b7525320a6451dbebd0a552028b5e70653d538c39842')
        # V5 此处校验请求结构与本地算法运算，不代表线上鉴权已通过。
        url,headers=sign_v5('https://example.invalid/player',gzip.compress(b'test'),{'device_id':'123','install_id':'456','cdid':'abc'},now_ms=1725000000000)
        assert 'x-medusa' in headers and 'x-helios' in headers and '_rticket=' in url
        print('通过：SM3、纯Python SM3、Simon、Ladon、TT注册加密、Spade内容密钥（仓库金标准向量）；V5结构自测。')
        return 0
    if args.command == "devices":
        print(json.dumps(devices.status(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "register":
        count = devices.ensure(args.devices, app_id=args.app)
        counts = [d for d in devices.status() if d['app_id']==args.app]
        registered = sum(d['origin']=='server_registered' for d in counts)
        verified = sum(d['server_verified'] for d in counts)
        print(f"{args.app}设备池：当前 {count} 个；服务器注册 {registered} 个，已取流验证 {verified} 个；缓存：{args.pool}")
        return 0
    # Playback (8662) automatically fills a missing device pool with real,
    # server-registered identities; existing cached registrations are reused.
    # The established Actions workflow pre-registers one Phoenix device and
    # continues to use it without additional registrations.
    pool = DirectPoolAdapter(devices, mock_origin=args.mock_origin)
    app = Downloader(pool, Path(args.out), getattr(args, "workers", 4), getattr(args, "quality", "best"), getattr(args, "raw", False))
    if args.command == "search":
        results = {}
        tabs = ("1", "11") if args.tab == "all" else (args.tab,)
        errors = []
        for tab in tabs:
            for page in range(args.pages):
                try:
                    data = pool.request("/api/v1/search", {
                        "query": args.keyword, "offset": page * 10, "count": 10, "tab_type": tab,
                    })
                except (DramaError, DirectError, requests.RequestException) as exc:
                    errors.append(f"tab={tab},page={page + 1}: {exc}")
                    break
                for item in parse_search(data):
                    old = results.get(item["series_id"])
                    if old is None:
                        results[item["series_id"]] = item
                    else:
                        for field in ("title", "episodes", "intro", "heat"):
                            if not old.get(field) and item.get(field):
                                old[field] = item[field]
        if not results:
            if errors:
                raise DramaError("搜索失败：" + "; ".join(errors))
            print("无搜索结果")
            return 0
        if errors:
            for error in errors:
                print(f"[搜索提醒] {error}")
        ids_missing_heat = [sid for sid, row in results.items() if not row.get("heat")]
        if ids_missing_heat:
            for sid, heat in pool.search_heats(ids_missing_heat).items():
                if sid in results and heat:
                    results[sid]["heat"] = heat
        for i, item in enumerate(results.values(), 1):
            print(f"{i:02d}. {item['title']}  ID={item['series_id']}"
                  f"  集数={item['episodes'] or '?'}  {search_display_heat(item)}")
        return 0
    if args.command == "info":
        details, episodes = app.details(args.series_id)
        loc = app.save_metadata(details)
        print(f"剧名：{details['title']}\nID：{details['series_id']}\n集数：{len(episodes)}\n简介：{details.get('intro', '')}")
        if details.get("cast"):
            print("演员：" + "，".join(f"{c['actor']} 饰 {c['role']}" for c in details["cast"]))
        for ep in episodes:
            print(f"{ep.number:03d}. {ep.title} ({ep.id}){' [不可播]' if ep.disabled else ''}")
        print("元数据：" + str(loc / "series.json"))
        return 0
    return app.download(args.series_id, args.episodes)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (DramaError, DirectError, OSError, ValueError, requests.RequestException) as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)
