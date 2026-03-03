# Screen Recorder

Windows 屏幕录制工具，提供 **Python** 和 **Rust** 两种实现，均支持 WebUI 图形界面和 CLI 命令行两种使用方式。

## 版本对比

| 特性 | Python 版 | Rust 版 |
|------|-----------|---------|
| WebUI 图形界面 | ✅ | ✅ |
| CLI 命令行 | ✅ | ✅ |
| 屏幕/窗口录制 | ✅ | ✅ |
| 多音频设备混录 | ✅ | ✅ |
| 摄像头画中画 | ✅ | ✅ |
| GPU 硬件编码 (NVENC) | ✅ | ✅ |
| 定时录制 (起止时间) | 仅起点 | ✅ 起点+终点 |
| 端口自动检测 | ❌ | ✅ |
| 进程终止自动保存 | ❌ | ✅ |
| 嵌入 FFmpeg 单文件部署 | ❌ | ✅ |
| 双击静默启动 | ❌ | ✅ |
| 音频方案 | PyAudio | FFmpeg dshow |
| 产物大小 | ~20MB + Python 环境 | ~87MB 单文件 (含 FFmpeg) |

## 快速开始

### Rust 版（推荐）

```bash
cd rust
cargo build --release
```

将 `ffmpeg.exe` 放在 `rust/` 目录下即可编译时嵌入。

```bash
# 双击 screen-recorder.exe → 静默启动 WebUI（不弹浏览器、不弹控制台）
# 通过浏览器访问 http://127.0.0.1:5000

# 显式启动 WebUI 并打开浏览器
screen-recorder.exe --open

# CLI 模式：录制 60 秒
screen-recorder.exe --duration 60 -v

# 定时录制：23:00 开始，次日 06:00 停止
screen-recorder.exe --schedule 23:00-06:00 -v
```

### Python 版

```bash
cd python
pip install -r requirements.txt
python app.py          # WebUI 模式
python cli.py --help   # CLI 模式
```

需要将 `ffmpeg.exe` 放在 `python/` 目录下。

## 功能详解

### WebUI 模式

启动后通过浏览器控制录制，支持手机局域网远程操控。

- 实时录制状态监控 (SSE)
- 录制参数调节（帧率、编码器、音频设备等）
- 录制文件管理（预览、下载、删除）
- 自动生成文件名

**Rust 版特性**：
- 双击 exe 即可静默启动，无控制台窗口
- 端口被占用时自动切换可用端口
- 进程关闭时自动保存正在录制的文件

### CLI 模式

零参数即可开始录制，Ctrl+C 停止。

```bash
# 全屏录制
screen-recorder.exe --fps 30

# 录制指定窗口
screen-recorder.exe --window "Google Chrome"

# GPU 编码 + 无音频
screen-recorder.exe --encoder h264_nvenc --no-audio

# 指定音频设备
screen-recorder.exe --audio-devices "麦克风 (Realtek Audio)"

# 摄像头画中画
screen-recorder.exe --webcam --webcam-device "Integrated Camera"

# 文件大小限制
screen-recorder.exe --max-size 2G

# 定时录制 (仅设开始时间)
screen-recorder.exe --schedule 14:30

# 定时录制 (设置起止时间，跨天自动处理)
screen-recorder.exe --schedule 23:00-06:00

# 指定输出
screen-recorder.exe -o meeting.mp4 --output-dir D:\recordings

# 列出可用设备
screen-recorder.exe --list-devices
```

### CLI 参数一览

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--open` | 启动 WebUI 后自动打开浏览器 | 不打开 |
| `--port PORT` | WebUI 端口，0=自动 | 0 |
| `--host ADDR` | WebUI 绑定地址 | 0.0.0.0 |
| `--fps N` | 帧率 (1-120) | 30 |
| `--encoder` | `mpeg4` (CPU) / `h264_nvenc` (GPU) | mpeg4 |
| `--no-mouse` | 不绘制鼠标光标 | 绘制 |
| `--window TITLE` | 录制指定窗口 | 全屏 |
| `--webcam` | 启用摄像头叠加 | 关闭 |
| `--webcam-device NAME` | 摄像头设备名 | - |
| `--no-audio` | 不录制音频 | 录制 |
| `--audio-devices NAME...` | 指定音频设备名称 | 系统默认 |
| `-o, --output FILE` | 输出文件名 | 自动时间戳 |
| `--output-dir DIR` | 输出目录 | ScreenCaptures/ |
| `--duration SEC` | 录制时长（秒） | 0 (无限) |
| `--max-size SIZE` | 文件大小上限（如 500M、2G） | 无限制 |
| `--schedule TIME` | 定时录制：`HH:MM` 或 `HH:MM-HH:MM` | 立即 |
| `-v, --verbose` | 打印录制状态 | 静默 |
| `--log-file PATH` | 写入日志文件 | - |
| `--config FILE` | JSON 配置文件 | 内置默认 |
| `--list-devices` | 列出可用设备 | - |
| `--ffmpeg-path PATH` | 指定 ffmpeg 路径 | 自动查找 |

### 停止录制

- **Ctrl+C** — 正常停止，等待文件保存
- **再按一次 Ctrl+C** — 强制退出
- **--duration** — 到达时长自动停止
- **--max-size** — 文件达到上限自动停止
- **--schedule HH:MM-HH:MM** — 到达结束时间自动停止
- **关闭进程 (Rust 版)** — 自动保存录制文件

### 配置文件

WebUI 设置会自动保存为 `settings.json`，CLI 可复用：

```json
{
  "fps": 30,
  "encoder": "mpeg4",
  "draw_mouse": true,
  "audio_mode": "default",
  "audio_devices": []
}
```

`audio_mode` 取值：`default` (系统默认设备)、`selected` (指定设备)、`disabled` (禁用)。

### API 接口

WebUI 模式提供 REST API，可供外部程序调用：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/status` | GET | 当前录制状态 |
| `/api/record/start` | POST | 开始录制 |
| `/api/record/stop` | POST | 停止录制 |
| `/api/files` | GET | 录制文件列表 |
| `/api/files/{name}` | DELETE | 删除文件 |
| `/api/files/{name}/download` | GET | 下载文件 |
| `/api/settings` | GET/PUT | 读取/更新设置 |
| `/api/devices` | GET | 音频及摄像头设备列表 |
| `/api/events` | GET | SSE 实时状态推送 |
| `/api/filename/next` | GET | 生成下一个文件名 |

## 项目结构

```
screen-record/
├── python/                 # Python 实现
│   ├── app.py              # WebUI 入口
│   ├── cli.py              # CLI 入口
│   ├── recorder/           # 录制引擎核心
│   │   ├── engine.py       # 状态机 + 进程管理
│   │   ├── cmd_builder.py  # FFmpeg 命令构建
│   │   ├── audio.py        # PyAudio 音频录制
│   │   ├── webcam.py       # 摄像头 + 设备枚举
│   │   └── settings_manager.py
│   ├── web/                # Flask 路由 + API
│   ├── templates/          # HTML
│   ├── static/             # CSS / JS / favicon
│   └── start.bat           # 一键启动脚本
├── rust/                   # Rust 实现
│   ├── src/main.rs         # 全部代码 (~1900 行)
│   ├── assets/             # 编译时嵌入的前端资源
│   ├── build.rs            # FFmpeg 嵌入检测
│   └── Cargo.toml          # 依赖配置
├── LICENSE                 # GPLv3
└── README.md               # 本文件
```

## 构建与部署

### Rust 版构建

```bash
cd rust

# 嵌入 FFmpeg（推荐）：将 ffmpeg.exe 放在 rust/ 或项目根目录
cp /path/to/ffmpeg.exe .

# 构建
cargo build --release

# 产物位于 rust/target/release/screen-recorder.exe (~87MB)
# 包含 FFmpeg + 前端资源，复制到任意位置即可运行
```

如果不放 `ffmpeg.exe`，构建产物约 3MB，运行时从 exe 同目录或系统 PATH 查找 FFmpeg。

FFmpeg 查找优先级：`--ffmpeg-path` → exe 同目录 → 嵌入提取 → PATH

### Python 版安装

```bash
cd python
pip install flask pyaudio
# 将 ffmpeg.exe 放在 python/ 目录下
```

## 无人值守示例

```bash
# 每天凌晨录制监控，23:00 开始 06:00 停止，最大 10GB
screen-recorder.exe --schedule 23:00-06:00 --max-size 10G --log-file rec.log

# Windows 任务计划程序：每天 9:00 录制 8 小时
screen-recorder.exe --duration 28800 --max-size 10G --log-file daily.log
```

CLI 模式默认静默，录制完成后输出文件路径到 stdout，适合脚本集成。

## 系统要求

- Windows 7/8/10/11
- 支持 DirectShow 的显卡和声卡
- GPU 编码需要 NVIDIA 显卡 + 驱动支持 NVENC
- Python 版额外需要 Python 3.10+
- Rust 版构建需要 Rust 1.70+

## 故障排除

**FFmpeg 找不到**
将 `ffmpeg.exe` 放在 exe 同目录，或用 `--ffmpeg-path` 指定。

**音频录制失败**
运行 `--list-devices` 确认设备名称，使用 `--audio-devices "设备全名"` 指定。

**录制卡顿/性能差**
降低帧率 (`--fps 15`)，启用 GPU 编码 (`--encoder h264_nvenc`)，关闭鼠标绘制 (`--no-mouse`)。

**端口被占用 (Rust 版)**
默认自动选择可用端口。指定端口时如被占用会自动 fallback。

**WebUI 无法访问**
检查防火墙是否放行对应端口。日志中会输出实际绑定的地址和端口。

## 许可证

[GNU General Public License v3.0](LICENSE)

## 致谢

- [FFmpeg](https://ffmpeg.org/) — 多媒体处理核心
- [Axum](https://github.com/tokio-rs/axum) — Rust Web 框架
- [Flask](https://flask.palletsprojects.com/) — Python Web 框架
