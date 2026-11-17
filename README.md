# 番茄短剧 Python 独立下载器

纯 Python 协议层移植，基于 [ch6vip/novel-sound](https://github.com/ch6vip/novel-sound) 的 GPL-3.0 源码（2026-11-17）。不需要什锦铺 APK、Rust 核心、本地 HTTP 服务、模拟器或 Frida。HTTP 协议请求直连第三方平台；需要联网以及实际可访问的短剧内容。

**验收边界**：本项目通过原仓库固定算法向量测试与本地模拟 HTTP 上游全链路测试；**尚未通过真实番茄短剧在线下载实测**。上游注册及视频 V5 签名可能随平台升级变化。不能据此保证当前所有线上作品均可成功下载；模拟服务验收不等于真实平台协议验收。

## 安装

Python 3.10+（建议 3.11+），Windows / Linux / macOS：

```bash
pip install -r requirements.txt
python drama_downloader.py selftest
```

加密 CENC 视频导出使用 FFmpeg：优先查找系统 `ffmpeg`，未安装时自动使用 `imageio-ffmpeg` Python wheel 自带的可执行文件（`pip install -r requirements.txt` 即可，无需手动添加 PATH）。`ffprobe` 仅用于增强成品校验，有则使用，无则检查 MP4 文件头。若沿用旧环境，执行 `python -m pip install imageio-ffmpeg`。**不是所有流都可以解密/解封装为通用 MP4**；软件不会把失败的密文缓存冒充成功文件。已经授权观看/保存的内容才应使用此工具，不提供付费或受限权限绕过。

## 操作命令

```bash
# 注册并保留 3 个设备（默认自动注册并维护 2 个）
python drama_downloader.py --devices 3 register

# 查看池中设备（不会启动服务、不会联网）
python drama_downloader.py devices

# 搜索短剧与获取作品 ID
python drama_downloader.py search "皇后" --pages 2

# 查看剧名、简介、演员、全集目录/每集视频 ID，输出 series.json
python drama_downloader.py info 作品ID

# 只下载 1-10,15,20-30 集，取不高于 1080P 的可用档，8 集并发
python drama_downloader.py download 作品ID --episodes 1-10,15,20-30 --quality 1080 --workers 8

# 继续下载未完成的全集
python drama_downloader.py download 作品ID --episodes all --quality best --workers 8

# 仅保存原始流（遇 CENC 可能不可直接播放）
python drama_downloader.py download 作品ID --episodes 1-5 --raw

# 自定义独立设备池 JSON 和输出路径
python drama_downloader.py --pool saved_devices.json --out output --devices 4 download 作品ID --episodes 1-30 --workers 4
```

`--quality` 支持 `best`、`360`、`480`、`540`、`720`、`1080`、`1440`、`2160`。实际档位以视频播放 API 为准，工具取不高于目标的最高可用档；当没有不高于目标的档位时取最接近的最低档。**不伪造指定分辨率**。编码为 bytevc2 的档位默认排除，因为普通 FFmpeg/播放器无法通用解码它。

`--workers` 可自定义并发集数（正整数），每个集采用 HTTP 流式写入，并有 `.part` 断点记录、HTTP Range 校验和失败重试。上游变更视频 URL 后，为防止不同片源拼接，重新下载该集。出错不会记作成功。

视频缓存修复：如某集因缺少 FFmpeg 解密失败，原 `.part` 与 `.part.json` 不要删除。安装依赖后**重复执行原来的下载命令**；当记录中保存了完整 Content-Length 且缓存大小吻合，会优先复用完整缓存，直接解密而不是重新下载。对无法证明完整的缓存仍采用正常 Range 续传与源指纹检查。

视频输出：`downloads/<剧名>/<三位集号>-<集名>[画质].mp4`。`series.json` 包含简介、演员和目录，`failed.json` 留下失败详情。已有成品会先校验，再选择跳过。

## 独立设备池

默认 `device_pool.json` 自动持久化成功注册的 `device_id`、`install_id`、`cdid` 与 openudid。支持导入原项目**明文**格式的 `{"android": [...]}` 池，不依赖原程序。使用设备轮询，HTTP 401/403 会停用当前设备；空池会自动注册。建议保管设备池文件，不要公开上传。`--devices N` 设置目标有效设备数（正整数），不是模拟器数量，也不是对接多个 Rust 节点。

注册实现目前覆盖 `device_register`（获得有效 device_id/install_id），未复制小说正文使用的 `registerkey` 密钥获取完整流程：**短剧搜索/目录/V5 播放路径使用的签名与取流不需要该小说正文密钥**。如果后续平台需要该密钥，需补充注册链而非伪造成功。

## 组成文件

- `drama_downloader.py`：CLI、元数据归一化、下载队列、续传与 MP4 校验。
- `drama_direct.py`：请求上游、设备注册/池、搜索/目录/详情/视频源。
- `drama_sign.py`：原生 Argus 551 / Ladon / Simon / SM3 / TT 加密核心。
- `drama_v5.py`：V5 播放请求签名、Medusa/Helios。
- `drama_v5_tables.py`：V5 查找表常量。
- `tests/test_standalone.py`：本地模拟上游全链路测试（运行时自动生成测试 MP4）。
- `LICENSE`：GPL-3.0。

## 本地测试

```bash
python drama_downloader.py selftest
python tests/test_standalone.py
python -m unittest discover -s tests -p test_ffmpeg_fallback.py -v
```

测试视频运行时生成，不含第三方短剧视频样本；模拟上游测试不需要原应用、Rust 后端或真实设备。

## 已测试与未测试

**已通过**：Python 编译；原仓库 SM3、Simon、Ladon、TT 注册加密、Spade 密钥向量；Spade URL 自造加密/解密往返；V5 请求结构的本地运算；模拟上游执行设备注册→搜索→作品信息→完整目录→视频模型→多线程 MP4→重复运行；先前的 FFmpeg CENC 样本解封装测试。

**尚未测试**：对真实字节短剧服务注册、真实视频 V5 签名是否接受，以及在真实 CDN 上下载/解密的完整链路。尤其对 `fallback_api` 的实际返回结构、上游限流及 DRM/编解码兼容性不提供实测保证。测试不能代替线上验收。

## 版权与责任

衍生移植自 `ch6vip/novel-sound`（GPL-3.0），签名 V5 表与实现其项目另引用 GPL-3.0 源码。此包依照 GPL-3.0 分发，保留相关来源说明与许可证。只对用户有合法访问权的内容使用，不绕过付费权限。

## 项目来源与开源许可

本仓库是对已有公开源代码的 Python 移植与修改，不声称相关协议或加密算法由本项目原创。

- 原始参考项目：[ch6vip/novel-sound](https://github.com/ch6vip/novel-sound) — Flutter + Rust 实现，原项目以 **GPL-3.0** 开源；本项目移植了设备注册、签名、短剧播放协议与视频源处理的相关思路和算法。
- V5 播放签名算法/查找表：原项目 `rust/src/sign/v5.rs`、`v5_tables.rs` 明确标注参考了 [N3urda/hongguoTV](https://github.com/N3urda/hongguoTV)（GPL-3.0）及 [beilunyang/openhongguo](https://github.com/beilunyang/openhongguo) 的相关实现。
- 更多来源及版权说明见 [NOTICE.md](NOTICE.md)，完整许可文本见 [LICENSE](LICENSE)。

**许可证：GPL-3.0。** 允许按照 GPL-3.0 的条件使用、复制、修改和重新发布源代码。该许可证是源代码的开源授权，并不意味着获得第三方平台内容的版权许可。本项目与番茄小说、红果短剧、字节跳动及上述开源作者不存在官方隶属关系。功能仅为技术研究与个人管理；请自行确保使用行为符合适用法律和相关权利人的授权要求。

**功能策略**：本项目不设置登录付费、集数配额、每日任务额度或其他人为下载限制；请求并发及设备池规模由用户设置，实际受网络、设备、服务器协议、可用视频源及 Python/操作系统资源约束。工具不保证取得用户未获授权的付费内容。
