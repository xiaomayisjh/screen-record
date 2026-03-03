# CLI 静默录屏功能设计

## 概述

新增独立命令行入口 `cli.py`，提供纯命令行的完整屏幕录制功能，支持无人值守静默录屏。不启动 Flask 服务和浏览器，直接复用现有的 `RecordingEngine`。

## 用法示例

```bash
# 最简用法：零参数，全屏+默认音频，Ctrl+C 停止
python cli.py

# 录制 60 秒后自动停止
python cli.py --duration 60

# 完整参数
python cli.py --duration 3600 --fps 30 --encoder h264_nvenc --output my_rec.mp4 --max-size 2G --verbose

# 从现有 settings.json 读取配置
python cli.py --config settings.json --duration 120

# 定时开始录制
python cli.py --schedule 23:00 --duration 7200

# 录制指定窗口，不录音频
python cli.py --window "Google Chrome" --no-audio
```

## CLI 参数

所有参数均为可选，零参数即可启动录制。

### 视频参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--fps` | 帧率 (1-120) | 30 |
| `--encoder` | 编码器: `mpeg4` (CPU) / `h264_nvenc` (GPU) | `mpeg4` |
| `--no-mouse` | 不在录像中绘制鼠标光标 | 绘制鼠标 |
| `--window TITLE` | 录制指定窗口（按标题匹配） | 全屏桌面 |
| `--webcam` | 启用摄像头叠加 | 不启用 |
| `--webcam-device NAME` | 摄像头设备名 | 无 |

### 音频参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--no-audio` | 不录制音频 | 录制音频 |
| `--audio-devices ID [ID ...]` | 指定音频设备 ID 列表 | 系统默认设备 |

### 输出参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--output FILE` / `-o FILE` | 输出文件名 | `ScreenCapture_YYYYMMDD_HHMMSS.mp4` |
| `--output-dir DIR` | 输出目录 | `ScreenCaptures/` |

### 停止条件

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--duration SECONDS` | 录制时长（秒），0=无限 | 0（无限） |
| `--max-size SIZE` | 文件大小上限，如 `500M` / `2G` | 无限制 |
| Ctrl+C | 随时可手动中断 | 始终可用 |

### 定时参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--schedule HH:MM` | 定时开始录制（24小时制） | 立即开始 |

### 日志参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--verbose` / `-v` | 在终端打印录制状态信息 | 静默 |
| `--log-file PATH` | 写入日志文件 | 不写日志 |

### 配置参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--config FILE` | 从 JSON 配置文件读取设置 | 使用内置默认值 |
| `--list-devices` | 列出可用的音频和摄像头设备后退出 | - |

## 架构设计

### 文件变更

- **新增**: `cli.py` (~250-300行) — 独立命令行入口
- **不修改**: 现有的 `recorder/`、`web/`、`app.py` 等文件不变

### 模块关系

```
cli.py (argparse + 信号处理 + 停止条件监控)
  ├── recorder/settings_manager.py (SettingsManager) — 可选，读取 --config
  ├── recorder/engine.py (RecordingEngine) — 核心录制引擎，完全复用
  │     ├── recorder/cmd_builder.py (CmdBuilder)
  │     ├── recorder/audio.py (StreamingAudioRecorder)
  │     └── recorder/webcam.py (WebcamCapturer, DeviceEnumerator)
  └── [无 Flask, 无浏览器, 无 Web 层]
```

### cli.py 内部结构

```python
# 伪代码结构

def parse_args():
    """argparse 参数解析"""

def parse_size(size_str):
    """解析文件大小字符串: '500M' -> 524288000, '2G' -> 2147483648"""

def setup_logging(verbose, log_file):
    """配置 logging: 静默/verbose/文件日志"""

def list_devices(engine):
    """列出音频和摄像头设备并退出"""

def wait_for_schedule(schedule_time):
    """等待到指定时间再开始录制"""

def build_config(args):
    """将 CLI 参数转换为 RecordingEngine.start_recording() 需要的 config dict"""

def apply_settings(settings_manager, args):
    """将 CLI 参数应用到 SettingsManager（fps, encoder, draw_mouse, audio 等）"""

class StopConditionMonitor:
    """后台监控停止条件的线程"""

    def __init__(self, engine, duration, max_size, tmp_dir):
        ...

    def start(self):
        """启动监控线程"""

    def _monitor_loop(self):
        """
        循环检查:
        1. duration > 0 且 已录制时间 >= duration → 触发停止
        2. max_size > 0 且 tmp/ 文件总大小 >= max_size → 触发停止
        每秒检查一次
        """

    def stop(self):
        """停止监控"""

def main():
    args = parse_args()
    setup_logging(args.verbose, args.log_file)

    # --list-devices: 列出设备后退出
    # --schedule: 等待到指定时间
    # 构建 engine, 应用 settings
    # 注册 SIGINT 处理器
    # engine.start_recording(config)
    # 启动 StopConditionMonitor
    # 等待录制结束（引擎状态变为 idle）
    # 输出结果信息
```

### 信号处理

```python
import signal

def _signal_handler(sig, frame):
    """Ctrl+C 处理: 优雅停止录制并等待合并完成"""
    log.info("收到中断信号，正在停止录制...")
    engine.stop_recording()
    # 不调用 sys.exit()，让主循环等待合并完成
```

- Ctrl+C 触发 `stop_recording()`，引擎会先停止录制再合并音视频
- 合并完成后主循环检测到 state 变为 idle，正常退出
- 两次 Ctrl+C 强制退出（第二次不拦截）

### 停止条件监控

`StopConditionMonitor` 是一个守护线程，每秒检查：

1. **时长**: `time.time() - start_time >= duration`
2. **文件大小**: 遍历 `tmp/` 目录计算总文件大小 >= `max_size`

任一条件满足时调用 `engine.stop_recording()`。

### 等待录制完成

主线程使用 `RecordingEngine.wait_for_state_change()` 方法等待状态变化：

```python
last_version = 0
while True:
    version, state = engine.wait_for_state_change(last_version, timeout=1.0)
    last_version = version

    if verbose:
        # 打印当前状态: 录制中 / 合并中 / 已完成
        ...

    if state["state"] == "idle" and was_recording:
        break  # 录制+合并完成，退出
```

### 日志输出

- **静默模式**（默认）: 无终端输出，适合 cron/任务计划程序
- **`--verbose`**: 打印到 stderr
  - 录制开始: `[14:30:00] 开始录制 → ScreenCapture_20260303_143000.mp4`
  - 录制中: `[14:30:05] 录制中... 00:00:05` (每5秒更新)
  - 停止: `[14:31:00] 停止录制，正在合并...`
  - 完成: `[14:31:03] 完成! 文件: ScreenCaptures/ScreenCapture_20260303_143000.mp4 (125.3 MB)`
- **`--log-file`**: 同 verbose 内容写入文件（可与 verbose 同时使用）

### 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 正常完成（时长到达/文件大小到达/Ctrl+C 后成功保存） |
| 1 | 错误（FFmpeg 崩溃、设备不可用等） |
| 2 | 参数错误 |

注意: Ctrl+C 中断后如果文件成功保存，退出码为 0（不是 2），因为文件已正常保存。

### --list-devices 输出格式

```
音频设备:
  [0] Microsoft Sound Mapper - Input (MME)
  [1] Microphone (Realtek Audio) (MME)
  [3] Stereo Mix (Realtek Audio) (MME)

摄像头设备:
  Integrated Camera
  USB Camera
```

### --config 配置文件

复用现有的 `settings.json` 格式：

```json
{
  "fps": 30,
  "encoder": "mpeg4",
  "draw_mouse": true,
  "audio_mode": "default",
  "audio_devices": []
}
```

CLI 参数优先级高于配置文件（即 `--fps 60 --config settings.json` 中 fps=60 覆盖配置文件中的 fps）。

## 与现有代码的关系

- `cli.py` 与 `app.py` 完全独立，互不影响
- 两者共享 `recorder/` 模块作为核心引擎
- Web UI 功能不受影响，`app.py` 无需修改
- `cli.py` 创建自己的 `SettingsManager` 和 `RecordingEngine` 实例

## 实现计划

1. **创建 `cli.py`**
   - argparse 参数定义
   - `parse_size()` 文件大小解析工具
   - `setup_logging()` 日志配置
   - `list_devices()` 设备列表功能
   - `wait_for_schedule()` 定时等待
   - `build_config()` 参数到配置的转换
   - `apply_settings()` 设置应用
   - `StopConditionMonitor` 类
   - `main()` 主流程
   - 信号处理（Ctrl+C 优雅退出）

2. **测试场景**
   - 零参数启动 → 全屏录制 + 默认音频，Ctrl+C 停止
   - `--duration 10` → 10秒自动停止
   - `--max-size 10M` → 文件达到10MB停止
   - `--no-audio` → 只录视频
   - `--window "Notepad"` → 录制记事本窗口
   - `--verbose` → 终端打印状态
   - `--list-devices` → 列出设备后退出
   - `--schedule 23:00 --duration 3600` → 23:00开始录1小时
   - `--config settings.json` → 读取Web UI的配置
