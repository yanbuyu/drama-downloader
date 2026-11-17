# 来源与开源许可

本项目是 Python 移植项目，主要参考：

- **[ch6vip/novel-sound](https://github.com/ch6vip/novel-sound)**（GPL-3.0）：设备注册、设备池、Argus / Ladon / Simon / SM3 签名、短剧搜索、目录、播放协议及视频处理。
- **[N3urda/hongguoTV](https://github.com/N3urda/hongguoTV)**（GPL-3.0）：原项目 V5 播放签名实现所引用的算法和常量。
- **[beilunyang/openhongguo](https://github.com/beilunyang/openhongguo)**：原项目 V5 播放签名的对照参考。

相较原项目，本仓库采用独立 Python CLI，实现设备池持久化、多线程、选集选画质、缓存恢复以及 FFmpeg 自动发现。

本项目基于 GPL-3.0 代码移植，按 **GPL-3.0** 分发，完整条款见 [LICENSE](LICENSE)。原作者与各参考项目的署名及授权义务应予保留。
