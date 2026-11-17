#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""番茄短剧 Python 直连版：独立搜索、设备注册与池化、V5取流、多线程下载。

基于 ch6vip/novel-sound（GPL-3.0）公开源码移植；不依赖原App、Rust或Frida。
依赖：pip install -r requirements.txt；自动查找系统 ffmpeg 或 imageio-ffmpeg 内置可执行文件。
上游协议随时可能变化，必须实际联机验收；不绕过账号内容权限。
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
                                      int(params.get('count', 10)))
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


def parse_search(data: dict) -> list[dict]:
    inner = data.get("data", data)
    tabs = inner.get("search_tabs", []) if isinstance(inner, dict) else []
    found = {}
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        if str(tab.get("tab_type")) != "11" and tab.get("title") not in ("短剧", "漫剧"):
            continue
        for cell in tab.get("data", []):
            if not isinstance(cell, dict):
                continue
            children = cell.get("video_data")
            if not isinstance(children, list) or not children:
                children = [cell]
            for child in children:
                if not isinstance(child, dict):
                    continue
                merged = dict(cell)
                merged.update(child)
                details = merged.get("video_detail")
                if isinstance(details, dict):
                    merged.update(details)
                sid = first(merged, ("series_id_str", "pseries_id", "series_id", "book_id"))
                if not sid:
                    continue
                title = first(merged, ("series_title", "title", "name", "book_name"))
                if not title:
                    continue
                found[sid] = {
                    "series_id": sid,
                    "title": title,
                    "episodes": first(merged, ("episode_cnt", "episode_count", "ep")),
                    "intro": first(merged, ("series_intro", "intro")),
                }
    return list(found.values())


def parse_series_info(result: dict, fallback_id: str) -> dict:
    data = result.get("data", {})
    video = data.get("video_data", {}) if isinstance(data, dict) else {}
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
        if quality != "best":
            raise DramaError("上游仅提供单流，无法确认指定清晰度")
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
        raise DramaError("视频接口未给清晰度信息，无法按指定画质下载")
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
        info = {}
        try:
            info = parse_series_info(self.pool.request(f"/api/v1/series/{series_id}"), series_id)
        except DramaError as e:
            self.log(f"[信息] 详情接口暂不可用：{e}")
        episodes = []
        errors = []
        for path in (f"/api/v1/books/{series_id}/directory", f"/api/v1/manga/videos/{series_id}"):
            try:
                episodes = parse_episodes(self.pool.request(path))
                if episodes:
                    break
            except DramaError as e:
                errors.append(str(e))
        if not episodes:
            raise DramaError("无法获取短剧目录：" + "; ".join(errors))
        info.setdefault("series_id", series_id)
        info.setdefault("title", series_id)
        info["episodes"] = [asdict(ep) for ep in episodes]
        info["episode_count_directory"] = len(episodes)
        return info, episodes

    def save_metadata(self, info: dict) -> Path:
        folder = self.out / safe_name(info.get("title", ""))
        atomic_json(folder / "series.json", info)
        return folder

    def one(self, ep: Episode, folder: Path):
        name = safe_name(f"{ep.number:03d}-{ep.title}") + f"[{self.quality}]"
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

    def download(self, series_id: str, spec: str, *, include_disabled: bool = False):
        info, episodes = self.details(series_id)
        folder = self.save_metadata(info)
        numbers = parse_range(spec, len(episodes))
        selected = [episodes[n - 1] for n in numbers]
        omitted = [x.number for x in selected if x.disabled and not include_disabled]
        selected = [x for x in selected if include_disabled or not x.disabled]
        self.log(f"剧名：{info.get('title')} | 全 {len(episodes)} 集 | 下载 {len(selected)} 集 | 线程 {self.workers} | 清晰度 {self.quality}")
        self.log(f"保存目录：{folder}")
        if omitted:
            self.log(f"跳过平台标为不可播放的剧集：{omitted}")
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
    parser = argparse.ArgumentParser(description="独立 Python 番茄短剧下载器（不依赖Rust/Frida/原App）")
    parser.add_argument("--pool", default="device_pool.json", help="设备池JSON（默认 device_pool.json；可导入原项目未加密的池）")
    parser.add_argument("--out", default="downloads", help="视频保存目录")
    parser.add_argument("--devices", type=int, default=2, help="自动维护的有效设备数量，默认2")
    parser.add_argument("--mock-origin", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("selftest", help="离线运行协议签名/加密固定向量测试")
    sub.add_parser("devices", help="查看当前已注册设备池")
    reg = sub.add_parser("register", help="直接向上游注册设备并保存到池中")
    search = sub.add_parser("search", help="按名称搜索短剧")
    search.add_argument("keyword")
    search.add_argument("--pages", type=int, default=1)
    info = sub.add_parser("info", help="获取详细信息、演员、剧集目录，保存JSON")
    info.add_argument("series_id")
    dl = sub.add_parser("download", help="指定集数范围和画质，多线程下载MP4")
    dl.add_argument("series_id")
    dl.add_argument("--episodes", default="all", help="集数范围：1-10,12,20- 或 all")
    dl.add_argument("--quality", choices=("best", "360", "480", "540", "720", "1080", "1440", "2160"), default="best")
    dl.add_argument("--workers", type=int, default=4, help="并发下载集数，默认4")
    dl.add_argument("--raw", action="store_true", help="保存未经处理的原始缓存（可能为CENC加密）")
    dl.add_argument("--include-disabled", action="store_true", help="尝试下载目录标为不可播的剧集（不解除访问限制）")
    args = parser.parse_args(argv)
    if getattr(args, "workers", 4) < 1:
        parser.error("--workers 必须大于 0")
    if getattr(args, "pages", 1) < 1:
        parser.error("--pages 必须大于 0")
    if args.devices < 1:
        parser.error("--devices 必须大于 0")
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
        devices.ensure(args.devices)
        print(f"设备注册完成，池内 {len(devices.status())} 个设备：{args.pool}")
        return 0
    devices.ensure(args.devices)
    pool = DirectPoolAdapter(devices, mock_origin=args.mock_origin)
    app = Downloader(pool, Path(args.out), getattr(args, "workers", 4), getattr(args, "quality", "best"), getattr(args, "raw", False))
    if args.command == "search":
        results = {}
        for page in range(args.pages):
            data = pool.request("/api/v1/search", {"query": args.keyword, "offset": page * 10, "count": 10, "tab_type": 11})
            for item in parse_search(data):
                results[item["series_id"]] = item
        if not results:
            print("无搜索结果（可能是该页没有短剧，或上游接口发生变化）")
            return 0
        for i, item in enumerate(results.values(), 1):
            print(f"{i:02d}. {item['title']}  ID={item['series_id']}  集数={item['episodes'] or '?'}")
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
    return app.download(args.series_id, args.episodes, include_disabled=args.include_disabled)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (DramaError, DirectError, OSError, ValueError, requests.RequestException) as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)
