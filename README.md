# drama-downloader

红果短剧 Python 下载器，独立运行，无需原 App、Rust 后端、模拟器或 Frida。

支持短剧搜索、作品及剧集信息查询、选集下载、清晰度选择、自动设备注册、设备池轮换、多线程下载和断点续传。加密片源可使用 FFmpeg 处理。

## 安装

要求 Python 3.10+。

```bash
git clone https://github.com/yanbuyu/drama-downloader.git
cd drama-downloader
pip install -r requirements.txt
```

已通过 `requirements.txt` 安装 `imageio-ffmpeg` 时，无需另外配置 FFmpeg。

## 使用

```bash
# 搜索短剧
python drama_downloader.py search "剧名"

# 搜索多页
python drama_downloader.py search "剧名" --pages 3

# 查看作品信息与全集目录
python drama_downloader.py info 作品ID

# 下载全集（最佳可用清晰度）
python drama_downloader.py download 作品ID

# 指定集数、画质和并发数
python drama_downloader.py download 作品ID --episodes 1-10,15,20-30 --quality 1080 --workers 8

# 下载第 20 集至最后一集
python drama_downloader.py download 作品ID --episodes 20- --quality 720

# 保存原始视频数据
python drama_downloader.py download 作品ID --episodes 1-5 --raw

# 查看设备池
python drama_downloader.py devices

# 预先注册设备（可选）
python drama_downloader.py --devices 5 register

# 自定义设备池与保存目录
python drama_downloader.py --pool devices.json --out downloads --devices 5 download 作品ID --episodes all --workers 8
```

## 参数说明

| 参数 | 用途 |
| --- | --- |
| `--episodes` | `all`、`1-10`、`1,3,5`、`1-10,15,20-` |
| `--quality` | `best` 或任意正整数短边分辨率（如 `720`、`1080`、`1440`） |
| `--workers` | 同时下载的剧集数，默认 4 |
| `--devices` | 设备池目标设备数，默认 5，最多 20（同原仓库） |
| `--pool` | 设备池文件，默认 `device_pool.json` |
| `--out` | 输出目录，默认 `downloads` |
| `--raw` | 保存未经处理的原始视频数据 |

设备首次使用时自动注册，信息保存在 `device_pool.json`；无需手动注册。文件统一命名为 `001-第1集[best].mp4`、`002-第2集[best].mp4`，不再把剧情简介写进文件名。旧版超长文件名在 `series.json` 中的视频 ID 核对一致后自动改名并复用，无需重下；错号视频仍保留到 `_wrong_episode_backup/`。下载失败保留 `.part` 缓存，重新执行相同命令即可续传。

## 参考项目与开源许可

- [ch6vip/novel-sound](https://github.com/ch6vip/novel-sound) — 主要移植参考
- [N3urda/hongguoTV](https://github.com/N3urda/hongguoTV) — V5 签名相关实现
- [beilunyang/openhongguo](https://github.com/beilunyang/openhongguo) — V5 签名相关参考

项目使用 [GPL-3.0](LICENSE) 许可证，源码来源详见 [NOTICE.md](NOTICE.md)。

**本工具仅供短剧从业者拉片学习，请在下载视频 24 小时后删除。**
