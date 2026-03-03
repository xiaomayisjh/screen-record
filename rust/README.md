# Screen Recorder — Rust 版

单文件屏幕录制工具，支持 WebUI 和 CLI 双模式。编译时嵌入前端资源和 FFmpeg，复制 exe 即可运行。

## 特性

- **双击静默启动**：无控制台窗口，后台运行 WebUI 服务
- **端口自动检测**：默认端口被占用时自动切换
- **进程终止自动保存**：关闭程序时自动完成录制文件合并
- **定时录制**：支持设置开始和结束时间 (`--schedule 23:00-06:00`)
- **屏幕/窗口录制**：FFmpeg gdigrab 捕获桌面或指定窗口
- **多音频设备**：FFmpeg dshow 捕获，支持多设备混录
- **摄像头画中画**：叠加摄像头画面到录制视频
- **GPU 编码**：支持 NVIDIA h264_nvenc 硬件编码
- **嵌入式 FFmpeg**：build.rs 编译时嵌入，首次运行自动提取
- **SSE 实时推送**：WebUI 录制状态实时更新

## 构建

```bash
# 嵌入 FFmpeg（可选，推荐）
# 将 ffmpeg.exe 放在 rust/ 或项目根目录
cp /path/to/ffmpeg.exe .

cargo build --release
# 产物: target/release/screen-recorder.exe
# 嵌入 FFmpeg 时约 87MB，不嵌入约 3MB
```

Release 配置已开启 LTO + strip 优化 Rust 代码部分。

## 使用

### WebUI 模式

```bash
# 双击 exe 或直接运行 → 静默启动 WebUI
screen-recorder.exe

# 启动并打开浏览器
screen-recorder.exe --open

# 指定端口
screen-recorder.exe --port 8080 --open
```

访问 `http://127.0.0.1:<port>` 控制录制。局域网设备访问 `http://<电脑IP>:<port>`。

### CLI 模式

携带录制参数时自动进入 CLI 模式：

```bash
# 全屏录制 60 秒
screen-recorder.exe --duration 60 -v

# 录制窗口 + GPU 编码
screen-recorder.exe --window "Chrome" --encoder h264_nvenc --fps 60

# 定时录制：23:00 开始，06:00 停止
screen-recorder.exe --schedule 23:00-06:00 -v

# 定时录制：14:30 开始，手动停止
screen-recorder.exe --schedule 14:30

# 摄像头 + 指定音频设备
screen-recorder.exe --webcam --webcam-device "Integrated Camera" \
    --audio-devices "麦克风 (Realtek Audio)"

# 文件大小限制
screen-recorder.exe --max-size 2G -o meeting.mp4

# 列出设备
screen-recorder.exe --list-devices
```

### 参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `--open` | 启动后打开浏览器 | 不打开 |
| `--port PORT` | WebUI 端口，0=自动 | 0 |
| `--host ADDR` | 绑定地址 | 0.0.0.0 |
| `--fps N` | 帧率 1-120 | 30 |
| `--encoder` | `mpeg4` / `h264_nvenc` | mpeg4 |
| `--no-mouse` | 不绘制鼠标 | 绘制 |
| `--window TITLE` | 录制指定窗口 | 全屏 |
| `--webcam` | 摄像头叠加 | 关闭 |
| `--webcam-device NAME` | 摄像头设备名 | - |
| `--no-audio` | 禁用音频 | 录制 |
| `--audio-devices NAME...` | 音频设备名称 | 默认设备 |
| `-o, --output FILE` | 输出文件名 | 自动时间戳 |
| `--output-dir DIR` | 输出目录 | ScreenCaptures/ |
| `--duration SEC` | 录制秒数 | 0 (无限) |
| `--max-size SIZE` | 文件上限 (500M/2G) | 无限 |
| `--schedule TIME` | `HH:MM` 或 `HH:MM-HH:MM` | 立即 |
| `-v, --verbose` | 输出录制状态 | 静默 |
| `--log-file PATH` | 日志文件 | - |
| `--config FILE` | JSON 配置文件 | 内置默认 |
| `--list-devices` | 列出设备 | - |
| `--ffmpeg-path PATH` | FFmpeg 路径 | 自动查找 |

### 模式判定

- 无参数 / `--web` / `--open` / `--port` / `--host` → WebUI 模式
- 携带 `--fps`、`--duration`、`--output` 等录制参数 → CLI 模式

### FFmpeg 查找顺序

1. `--ffmpeg-path` 指定的路径
2. exe 同目录下的 `ffmpeg.exe`
3. 编译时嵌入的 FFmpeg（首次自动提取到 exe 同目录）
4. 系统 PATH

## 架构

单文件 `main.rs` (~1900 行)，按模块组织：

| 模块 | 职责 |
|------|------|
| Embedded Assets | `include_str!`/`include_bytes!` 嵌入前端资源 |
| Data Types | 状态枚举、设置、配置等类型定义 |
| SettingsManager | JSON 配置加载/保存/校验 |
| FFmpeg Locator | 嵌入提取 + 多路径查找 |
| Command Builder | FFmpeg 命令构建 (capture/merge/audio/webcam) |
| DeviceEnumerator | dshow 设备枚举 + 缓存 |
| RecordingEngine | 状态机 `Idle→Recording→Merging→Idle` |
| EngineRef | 轻量引用，用于 monitor/merge 异步任务 |
| CLI Runner | 参数解析、调度、停止条件、信号处理 |
| API Handlers | REST + SSE 端点 |
| Web Server | Axum Router + 静态资源 |
| Main Entry | 模式检测、端口绑定、graceful shutdown |

状态管理：`Arc<Mutex<EngineInner>>` + `watch::channel` 广播状态变更。

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | WebUI 页面 |
| `/api/status` | GET | 录制状态 |
| `/api/record/start` | POST | 开始录制 |
| `/api/record/stop` | POST | 停止录制 |
| `/api/files` | GET | 文件列表 |
| `/api/files/{name}` | DELETE | 删除文件 |
| `/api/files/{name}/download` | GET | 下载文件 |
| `/api/settings` | GET/PUT | 设置读写 |
| `/api/devices` | GET | 设备列表 |
| `/api/events` | GET | SSE 实时推送 |
| `/api/filename/next` | GET | 下一个文件名 |

## 依赖

| 库 | 版本 | 用途 |
|----|------|------|
| axum | 0.8 | HTTP 服务器 + SSE |
| tokio | 1 (full) | 异步运行时 |
| clap | 4 (derive) | CLI 参数解析 |
| serde + serde_json | 1 | JSON 序列化 |
| chrono | 0.4 | 时间处理 |
| tracing | 0.1 | 日志 |
| open | 5 | 打开浏览器 |
| regex | 1 | 设备名解析 |

## 许可证

[GPLv3](../LICENSE)
