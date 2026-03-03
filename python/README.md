# Screen Recorder

基于 Flask 的屏幕录制工具，提供两种使用方式：

- **Web UI** — 通过浏览器控制录制，支持手机远程操控
- **CLI** — 纯命令行无人值守静默录屏，适合定时任务和自动化场景

## 功能

- 屏幕录制（基于 ffmpeg gdigrab，支持全屏或指定窗口）
- 音频录制（系统音频 / 麦克风，支持多设备混录）
- 摄像头叠加
- 帧率、编码器（CPU / GPU）等参数配置
- Web 界面控制，支持局域网内手机访问
- 命令行模式，支持定时录制、时长限制、文件大小限制等

## 依赖

- Python 3.10+
- ffmpeg.exe（放置在项目根目录）

## 安装

```bash
pip install -r requirements.txt
```

从 [ffmpeg.org](https://ffmpeg.org/download.html#build-windows) 下载 Windows 版本，将 `ffmpeg.exe` 放到项目根目录。

## 使用

### Web UI 模式

```bash
python app.py
```

或双击 `start.bat`。

浏览器会自动打开 `http://127.0.0.1:5000`，局域网设备可通过 `http://<你的电脑IP>:5000` 访问。

### 命令行模式

所有参数均可选，零参数即可启动录制：

```bash
# 全屏录制，Ctrl+C 停止
python cli.py

# 录制 60 秒后自动停止
python cli.py --duration 60

# 完整参数示例
python cli.py --duration 3600 --fps 30 --encoder h264_nvenc -o rec.mp4 --verbose

# 定时开始录制（23:00 开始，录 2 小时）
python cli.py --schedule 23:00 --duration 7200

# 文件大小达到 2GB 自动停止
python cli.py --max-size 2G

# 录制指定窗口，不录音频
python cli.py --window "Google Chrome" --no-audio

# 列出可用的音频和摄像头设备
python cli.py --list-devices

# 复用 Web UI 的配置文件
python cli.py --config settings.json --duration 120
```

#### CLI 参数一览

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--fps` | 帧率 (1-120) | 30 |
| `--encoder` | 编码器: `mpeg4` (CPU) / `h264_nvenc` (GPU) | mpeg4 |
| `--no-mouse` | 不绘制鼠标光标 | 绘制 |
| `--window TITLE` | 录制指定窗口 | 全屏 |
| `--webcam` | 启用摄像头叠加 | 不启用 |
| `--webcam-device NAME` | 摄像头设备名 | - |
| `--no-audio` | 不录制音频 | 录制 |
| `--audio-devices ID ...` | 指定音频设备 ID | 系统默认 |
| `-o, --output FILE` | 输出文件名 | 自动时间戳 |
| `--output-dir DIR` | 输出目录 | ScreenCaptures/ |
| `--duration SEC` | 录制时长（秒），0=无限 | 0 |
| `--max-size SIZE` | 文件大小上限（如 500M、2G） | 无限制 |
| `--schedule HH:MM` | 定时开始录制 | 立即 |
| `-v, --verbose` | 打印录制状态 | 静默 |
| `--log-file PATH` | 写入日志文件 | - |
| `--config FILE` | 从 JSON 配置文件读取设置 | 内置默认值 |
| `--list-devices` | 列出可用设备后退出 | - |

#### 停止录制

- **Ctrl+C** — 随时可手动停止（文件会正常保存）
- **--duration** — 到达指定时长自动停止
- **--max-size** — 文件大小达到上限自动停止
- 按两次 Ctrl+C 强制退出

#### 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 正常完成 |
| 1 | 录制出错 |
| 2 | 参数错误 |

#### 无人值守示例

配合 Windows 任务计划程序，实现每天自动录屏：

```bash
# 每天 9:00 自动录制 8 小时，最大 10GB
python cli.py --duration 28800 --max-size 10G --log-file recording.log
```

CLI 模式默认完全静默（无终端输出），录制完成后将输出文件路径打印到 stdout，适合脚本调用。

## 项目结构

```
app.py              # Web UI 入口
cli.py              # 命令行入口
recorder/           # 录制引擎核心
  engine.py         # 录制控制
  cmd_builder.py    # ffmpeg 命令构建
  audio.py          # 音频设备管理
  webcam.py         # 摄像头管理
  settings_manager.py  # 配置管理
web/                # Web 层
  routes.py         # 页面路由
  api.py            # API 接口
templates/          # HTML 模板
static/             # 静态资源 (CSS/JS/favicon)
```
