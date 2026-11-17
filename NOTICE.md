# 来源、署名与许可声明 / Attribution & Licensing

本项目 `drama-downloader` 是由公开开源项目移植、改写形成的 Python 实现，不代表任何平台官方。

## 原始源码与引用关系

1. **ch6vip/novel-sound** — https://github.com/ch6vip/novel-sound  
   开源许可：GNU General Public License v3.0（GPL-3.0）。本项目参考并移植其设备注册、设备池、HTTP 请求签名（Argus 551 / Ladon / Simon / SM3）、短剧视频 V5 签名（Medusa / Helios）、短剧搜索、剧集目录、Spade 密钥和视频解码相关实现。
2. **N3urda/hongguoTV** — https://github.com/N3urda/hongguoTV  
   GPL-3.0 项目。`novel-sound/rust/src/sign/v5.rs` 与 `v5_tables.rs` 明确注明其协议常量、变换结构引用该项目（上游所述提交 `18c37c2afd0567520135abcae312352542f09083`）。
3. **beilunyang/openhongguo** — https://github.com/beilunyang/openhongguo  
   `novel-sound` 的 V5 源码注释注明对照该项目 PR 13（上游所述提交 `788103f6`）。

本项目有修改：以 Python 替代原 Flutter/Rust 结构，加入独立 CLI、缓存恢复、质量选择、多线程下载、`imageio-ffmpeg` 自动定位及离线测试。原作者对本移植版不承担维护或技术支持义务。

## 授权

本仓库包含基于 GPL-3.0 源码的移植和派生内容，故**整体以 GPL-3.0 许可分发**。完整许可证详见仓库根目录 `LICENSE`。修改、分享及再分发时请依 GPL-3.0 履行相应义务，并保留现有版权与来源说明。

GPL-3.0 只授权本项目源码的使用及再分发，不授予任何短剧、音视频、用户账号、商标或平台内容的知识产权。第三方内容及商标归各自权利人所有。本项目不代表番茄、红果或其他平台官方。

## 免责声明

软件按“现状”（AS IS）提供，不提供任何形式保证。公开协议可能变化；本地模拟测试不代表已完成实际平台联网验收。使用者应自行评估技术风险、版权许可及适用法律要求。
