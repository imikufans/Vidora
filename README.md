<div align="center">

# 澜播 · Lanbo

**一个多平台直播源解析工具 —— 观澜知源，一播即达。**

[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-Web-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Electron](https://img.shields.io/badge/Electron-Desktop-47848F?logo=electron&logoColor=white)](https://www.electronjs.org/)
[![License](https://img.shields.io/badge/License-GPL--2.0-blue.svg)](./LICENSE)

[简体中文](./README.md) · [Quick Start](#快速开始) · [Features](#功能特性) · [FAQ](#常见问题)

</div>

---

## 项目简介

**澜播（Lanbo）** 是一个开源的直播源解析工具，支持抖音、哔哩哔哩、虎牙、斗鱼等主流直播平台。它把直播间地址解析为可直接播放的 HLS（m3u8）/ FLV 流，提供本地 Web 界面与跨平台桌面应用两种使用方式。

项目在 [wbt5/real-url](https://github.com/wbt5/real-url) 的基础上，集成了持续维护的 [DouyinLiveRecorder](https://github.com/ihmily/DouyinLiveRecorder) 解析引擎作为首选，旧引擎作为回退，兼顾解析成功率与兼容性。

> ⚠️ **重要提示**：本项目仅供个人学习、技术研究与合规场景使用。请遵守所在国家/地区法律及各平台服务条款，不得用于任何侵犯版权、盗播或商业盈利用途。详见 [免责声明](#免责声明)。

---

## 功能特性

- 🎯 **多平台支持**：抖音、哔哩哔哩、虎牙、斗鱼等数十个直播平台
- 🎬 **多清晰度**：自动提取原画 / 超清 / 高清 / 标清等多档线路，按画质排序
- ⚡ **自适应播放**：根据网络带宽自动选档，播放卡顿时自动降档，弱网也能流畅观看
- 🖥️ **双端形态**：
  - **Web 版**：Flask 后端 + 浏览器界面，双击 bat 一键启动
  - **桌面版**：Electron 封装，提供 Windows 安装包与便携版
- 📺 **PotPlayer 集成**：检测到本机 PotPlayer 时，一键用本地播放器打开直播流，解码更稳、延迟更低
- 🔐 **抖音原画解锁**：内置扫码 / 浏览器登录获取 Cookie，解锁抖音原画线路（详情见 [抖音登录](#抖音原画与登录)）
- 🌐 **Cloudflare Pages 方案**：附带静态前端 + Pages Function 代理模板，可部署到边缘网络
- 🔄 **双引擎架构**：新引擎优先、旧引擎回退，健康检查与流代理保证播放稳定

---

## 支持平台

支持平台及输入格式见 [`web/platforms.py`](./web/platforms.py)。常见的包括：

| 平台 | 输入示例 |
|---|---|
| 抖音 | 直播间链接 或 `https://v.douyin.com/xxx` 短链 |
| 哔哩哔哩 | 房间号，如 `6` |
| 虎牙 | 房间号，如 `123456` |
| 斗鱼 | 房间号，如 `12345` |

> 平台接口可能随时变更，若某平台解析失败，请更新解析器或提交 Issue。

---

## 快速开始

### 方式一：Web 版（源码运行）

需要 **Windows + Python 3.9+**（推荐 3.11）。

```bash
# 1. 克隆仓库
git clone https://github.com/imikufans/lanbo-live-parser.git
cd lanbo-live-parser

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 3. 启动（方式 A）：直接运行
python web/app.py

# 或（方式 B）：双击根目录下的「一键启动.bat」，它会自动建环境、装依赖、开浏览器
```

浏览器访问 <http://127.0.0.1:8899> 即可使用。

### 方式二：桌面版

从 [Releases](https://github.com/imikufans/lanbo-live-parser/releases) 下载对应产物：

- `直播解析.exe` —— NSIS 安装包
- `直播解析Portable.zip` —— 免安装便携版

解压 / 安装后直接运行即可，无需 Python 环境。

### 方式三：Cloudflare Pages

参见 [`cloudflare-pages/部署说明.md`](./cloudflare-pages/部署说明.md)。静态前端 + Pages Function 会把 API 请求转发到你自己的后端服务，需要一个可公网访问的后端。

---

## 使用说明

1. 选择平台，输入房间号或直播间链接
2. 点击「开始解析」，列表中会列出所有可用线路，按清晰度从高到低排列
3. 每条线路可以这样播放：
   - **在线播放**：点「播放」，在内置播放器中观看，支持清晰度切换与自动选档
   - **PotPlayer**：若已安装 PotPlayer，点橙色的「PotPlayer」按钮直接用本地播放器打开
   - **复制 / 下载**：复制 m3u8 地址，或下载 `.m3u8` 文件用 VLC、PotPlayer 等本地播放器打开

### 抖音原画与登录

抖音的**原画线路仅在登录态下返回**。未登录时只能拿到较低清晰度。为此澜播内置了登录功能：

1. 点击页面右上角「抖音登录」
2. **桌面版**会打开一个内置的真实浏览器窗口；**Web 版**会启动独立的浏览器实例
3. 用手机抖音 App 扫码并确认登录
4. 程序自动读取登录态并保存，右上角徽标显示「已登录」即生效

Cookie 仅保存在本地 `web/data/douyin_cookie.txt`，不会上传。若扫码失败（例如网络被风控），可用「手动粘贴 Cookie」兜底。

---

## 项目结构

```
lanbo-live-parser/
├── web/                      # Web 应用
│   ├── app.py                # Flask 后端与 API 路由
│   ├── runner.py             # 解析调度（子进程隔离）
│   ├── new_engine.py         # DouyinLiveRecorder 引擎适配 + 清晰度提取
│   ├── douyin_login.py       # 抖音二维码登录与 Cookie 持久化
│   ├── cdp_login.py          # Web 版浏览器登录（CDP，纯标准库）
│   ├── browser_cookie.py     # （备用）从本机浏览器会话提取 Cookie
│   ├── potplayer.py          # 检测并调用本机 PotPlayer
│   ├── platforms.py          # 平台清单
│   ├── adapters.py           # 特殊平台适配
│   ├── templates/index.html  # 前端页面
│   ├── static/               # 本地化 hls.js / flv.js
│   └── data/                 # 运行时生成：douyin_cookie.txt（请勿外泄）
├── real-url/                 # 上游 wbt5/real-url 解析引擎（GPL-2.0）
├── DLR/                      # 上游 DouyinLiveRecorder 引擎（MIT）
├── cloudflare-pages/         # Cloudflare Pages 部署模板
├── requirements.txt          # Python 依赖
├── 一键启动.bat              # Windows 一键启动脚本
└── LICENSE                   # GPL-2.0 许可证
```

---

## 技术栈

- **后端**：Python 3.9+ / Flask / Requests
- **解析**：DouyinLiveRecorder（新引擎）+ real-url（回退引擎）
- **前端**：原生 HTML / CSS / JavaScript，hls.js + flv.js（本地化）
- **桌面端**：Electron 28 + electron-builder（NSIS / Portable）
- **流媒体**：HLS（m3u8）/ FLV，同源流代理解决跨域与鉴权

---

## 从源码构建桌面版

桌面端的构建产物（嵌入式 Python、Node、Electron 依赖）体积较大，未纳入本仓库源码。如需自行构建：

```bash
cd _electron_build/app   # 构建工程（若随发布包提供）
npm install
npx electron-builder --win nsis --x64
npx electron-builder --win portable --x64
```

产物位于 `dist/` 目录。构建需要 Windows 环境。

---

## 常见问题（FAQ）

<details>
<summary><b>解析失败 / 提示未获取到直播地址？</b></summary>

可能原因：主播未开播、房间号错误、平台接口变更或网络不通。可先确认直播间在浏览器中能正常观看，再更新到最新版本或提交 Issue。
</details>

<details>
<summary><b>抖音解析不到原画？</b></summary>

原画需要登录态。请先按「抖音原画与登录」扫码登录；若提示 Cookie 失效，重新登录或手动粘贴 Cookie。
</details>

<details>
<summary><b>扫码显示 4031 风控？</b></summary>

软件内生成的二维码可能被抖音风控拦截。请使用登录弹窗里的「浏览器登录」方式：桌面版打开内置真实浏览器窗口，Web 版启动独立浏览器实例扫码；若仍失败则用「手动粘贴 Cookie」。
</details>

<details>
<summary><b>浏览器里 m3u8 无法播放？</b></summary>

直播源通常有跨域 / 鉴权限制，本项目通过同源流代理（`/api/stream`）转发解决。若仍失败，可点「下载 .m3u8」用 PotPlayer / VLC 等本地播放器打开。
</details>

<details>
<summary><b>网页版提示"无法连接到解析服务"？</b></summary>

请先双击运行「一键启动.bat」并确保黑色命令窗口保持打开；若已运行，刷新页面重试。
</details>

---

## 免责声明

1. 本项目**仅供个人学习、技术研究与合规测试使用**，不得用于任何商业用途或非法用途。
2. 本项目不托管、存储或转发任何直播内容，仅提供地址解析与播放辅助能力，所有内容版权归原平台及权利人所有。
3. 使用者应自行确保其使用行为符合所在国家/地区法律法规及目标平台的服务条款。因不当使用产生的任何责任由使用者自行承担。
4. 本项目开发者不对解析结果的可用性、准确性或适用性作任何明示或暗示的保证。

---

## 许可证

本项目采用 [GPL-2.0](./LICENSE) 许可证开源。

本项目内含以下第三方代码，其许可协议随附在对应目录中：

- [wbt5/real-url](https://github.com/wbt5/real-url) —— GPL-2.0，见 [`real-url/LICENSE`](./real-url/LICENSE)
- [DouyinLiveRecorder](https://github.com/ihmily/DouyinLiveRecorder) —— MIT，见 [`DLR/LICENSE`](./DLR/LICENSE)

感谢这些优秀的上游项目为本项目提供的基础能力。

---

<div align="center">

如果这个项目对你有帮助，欢迎点一个 ⭐ Star。

Made with ❤️ by [imikufans](https://github.com/imikufans)

</div>
