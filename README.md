# Screen Recorder WebUI

基于 Flask 的屏幕录制工具，通过浏览器控制录制，支持手机远程操控。

## 功能

- 屏幕录制（基于 ffmpeg gdigrab）
- 音频录制（系统音频 / 麦克风）
- 摄像头叠加
- 帧率、编码器等参数配置
- Web 界面控制，支持局域网内手机访问

## 依赖

- Python 3.10+
- ffmpeg.exe（放置在项目根目录）

## 安装

```bash
pip install -r requirements.txt
```

从 [ffmpeg.org](https://ffmpeg.org/download.html#build-windows) 下载 Windows 版本，将 `ffmpeg.exe` 放到项目根目录。

## 启动

```bash
python app.py
```

或双击 `start.bat`。

浏览器会自动打开 `http://127.0.0.1:5000`，局域网设备可通过 `http://<你的电脑IP>:5000` 访问。

## 项目结构

```
app.py              # Flask 应用入口
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
