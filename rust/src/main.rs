// Screen Recorder - Rust 版本
// 支持 WebUI / CLI 双模式，编译时嵌入前端资产，可选嵌入 FFmpeg

// Hide console window on Windows when double-clicked (WebUI mode).
// CLI mode will re-attach the console for output.
#![cfg_attr(windows, windows_subsystem = "windows")]

use std::convert::Infallible;
use std::io::Write as IoWrite;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime};

use axum::body::Body;
use axum::extract::{Path as AxumPath, State};
use axum::http::{header, StatusCode};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::IntoResponse;
use axum::routing::{delete, get, post};
use axum::{Json, Router};
use chrono::Local;
use clap::Parser;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use serde::{Deserialize, Serialize};
use tokio::sync::{watch, Mutex};
use tokio_stream::Stream;
use tokio_util::io::ReaderStream;

// ============================================================
// SECTION 1: Embedded Assets
// ============================================================

const INDEX_HTML: &str = include_str!("../assets/index.html");
const APP_CSS: &str = include_str!("../assets/app.css");
const APP_JS: &str = include_str!("../assets/app.js");
const FAVICON_ICO: &[u8] = include_bytes!("../assets/favicon.ico");

#[cfg(embedded_ffmpeg)]
const EMBEDDED_FFMPEG: &[u8] = include_bytes!(env!("FFMPEG_EMBED_PATH"));

// ============================================================
// SECTION 2: Data Types & Constants
// ============================================================

const CREATE_NO_WINDOW: u32 = 0x08000000;

const SUPPORTED_ENCODERS: &[&str] = &[
    "h264_nvenc",
    "h264_qsv", 
    "h264_amf",
    "libx264",
    "mpeg4",
];

fn get_encoder_name(encoder: &str) -> &str {
    match encoder {
        "h264_nvenc" => "NVIDIA NVENC",
        "h264_qsv" => "Intel QuickSync",
        "h264_amf" => "AMD AMF",
        "libx264" => "H.264 (CPU)",
        "mpeg4" => "MPEG-4 (CPU)",
        _ => encoder,
    }
}

fn is_hardware_encoder(encoder: &str) -> bool {
    matches!(encoder, "h264_nvenc" | "h264_qsv" | "h264_amf")
}

#[derive(Debug, Clone, Serialize)]
struct EncoderInfo {
    id: String,
    name: String,
    is_hardware: bool,
}

#[derive(Debug, Clone, Serialize)]
struct EncodersResponse {
    encoders: Vec<EncoderInfo>,
}

#[derive(Debug, Clone, Serialize)]
struct BestEncoderResponse {
    encoder: String,
    name: String,
    is_hardware: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
enum RecordingState {
    Idle,
    Recording,
    Merging,
}

#[derive(Debug, Clone, Serialize)]
struct EngineStatus {
    state: RecordingState,
    recording: bool,
    merging: bool,
    filename: Option<String>,
    elapsed: f64,
    error: Option<String>,
}

impl Default for EngineStatus {
    fn default() -> Self {
        Self {
            state: RecordingState::Idle,
            recording: false,
            merging: false,
            filename: None,
            elapsed: 0.0,
            error: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Settings {
    fps: u32,
    encoder: String,
    draw_mouse: bool,
    audio_mode: String,
    audio_devices: Vec<String>,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            fps: 30,
            encoder: "mpeg4".to_string(),
            draw_mouse: true,
            audio_mode: "default".to_string(),
            audio_devices: vec![],
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
struct RecordConfig {
    filename: Option<String>,
    source: Option<String>,
    window_title: Option<String>,
    webcam: Option<bool>,
    webcam_device: Option<String>,
}

#[derive(Debug, Serialize)]
struct FileInfo {
    name: String,
    size: u64,
    size_human: String,
    date: String,
}

#[derive(Debug, Serialize)]
struct OkResponse {
    ok: bool,
}

#[derive(Debug, Serialize)]
struct StartResponse {
    ok: bool,
    filename: Option<String>,
}

#[derive(Debug, Serialize)]
struct ErrorResponse {
    ok: bool,
    error: String,
}

#[derive(Debug, Serialize)]
struct FilesResponse {
    files: Vec<FileInfo>,
}

#[derive(Debug, Serialize)]
struct DevicesResponse {
    audio: Vec<AudioDeviceInfo>,
    webcam: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct AudioDeviceInfo {
    name: String,
    /// "input" (microphone) or "output" (speaker/loopback)
    device_type: String,
}

#[derive(Debug, Serialize)]
struct FilenameResponse {
    filename: String,
}

// ============================================================
// SECTION 3: Settings Manager
// ============================================================

struct SettingsManager {
    path: PathBuf,
    ffmpeg_path: PathBuf,
    settings: Settings,
}

impl SettingsManager {
    fn new(base_dir: &Path, ffmpeg_path: PathBuf) -> Self {
        let path = base_dir.join("settings.json");
        let mut mgr = Self {
            path,
            ffmpeg_path,
            settings: Settings::default(),
        };
        mgr.load();
        mgr
    }

    fn load(&mut self) {
        if self.path.is_file() {
            if let Ok(data) = std::fs::read_to_string(&self.path) {
                if let Ok(saved) = serde_json::from_str::<serde_json::Value>(&data) {
                    if let Some(fps) = saved.get("fps").and_then(|v| v.as_u64()) {
                        self.settings.fps = fps.clamp(1, 120) as u32;
                    }
                    if let Some(enc) = saved.get("encoder").and_then(|v| v.as_str()) {
                        if SUPPORTED_ENCODERS.contains(&enc) {
                            self.settings.encoder = enc.to_string();
                        }
                    }
                    if let Some(dm) = saved.get("draw_mouse").and_then(|v| v.as_bool()) {
                        self.settings.draw_mouse = dm;
                    }
                    if let Some(am) = saved.get("audio_mode").and_then(|v| v.as_str()) {
                        if am == "default" || am == "selected" || am == "disabled" {
                            self.settings.audio_mode = am.to_string();
                        }
                    }
                    if let Some(ad) = saved.get("audio_devices").and_then(|v| v.as_array()) {
                        self.settings.audio_devices = ad
                            .iter()
                            .filter_map(|v| v.as_str().map(|s| s.to_string()))
                            .collect();
                    }
                }
            }
        }
    }

    fn save(&self) {
        if let Ok(json) = serde_json::to_string_pretty(&self.settings) {
            let _ = std::fs::write(&self.path, json);
        }
    }

    fn get_all(&self) -> Settings {
        self.settings.clone()
    }

    fn update(&mut self, changes: &serde_json::Value) -> Settings {
        if let Some(fps) = changes.get("fps").and_then(|v| v.as_u64()) {
            self.settings.fps = fps.clamp(1, 120) as u32;
        }
        if let Some(enc) = changes.get("encoder").and_then(|v| v.as_str()) {
            if SUPPORTED_ENCODERS.contains(&enc) {
                self.settings.encoder = enc.to_string();
            }
        }
        if let Some(dm) = changes.get("draw_mouse").and_then(|v| v.as_bool()) {
            self.settings.draw_mouse = dm;
        }
        if let Some(am) = changes.get("audio_mode").and_then(|v| v.as_str()) {
            if am == "default" || am == "selected" || am == "disabled" {
                self.settings.audio_mode = am.to_string();
            }
        }
        if let Some(ad) = changes.get("audio_devices").and_then(|v| v.as_array()) {
            self.settings.audio_devices = ad
                .iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect();
        }
        self.save();
        self.settings.clone()
    }

    fn detect_available_encoders(&self) -> Vec<String> {
        let mut available = vec!["mpeg4".to_string()];
        
        if !self.ffmpeg_path.exists() {
            return available;
        }

        let output = Command::new(&self.ffmpeg_path)
            .arg("-encoders")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        #[cfg(windows)]
        let output = output.creation_flags(CREATE_NO_WINDOW);

        let output = match output.output() {
            Ok(o) => o,
            Err(_) => return available,
        };

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);
        let combined = stdout.to_string() + &stderr;

        for &encoder in SUPPORTED_ENCODERS {
            if encoder == "mpeg4" {
                continue;
            }
            if combined.contains(encoder) {
                available.push(encoder.to_string());
            }
        }

        available
    }

    fn get_best_encoder(&self) -> String {
        let available = self.detect_available_encoders();
        for &encoder in SUPPORTED_ENCODERS {
            if available.contains(&encoder.to_string()) {
                return encoder.to_string();
            }
        }
        "mpeg4".to_string()
    }
}

// ============================================================
// SECTION 4: FFmpeg Locator + Embedded Extraction
// ============================================================

fn find_ffmpeg(base_dir: &Path, cli_override: Option<&Path>) -> Option<PathBuf> {
    // 1. CLI override
    if let Some(p) = cli_override {
        if p.is_file() {
            return Some(p.to_path_buf());
        }
    }

    // 2. Beside executable
    if let Ok(exe) = std::env::current_exe() {
        let beside = exe.parent().unwrap().join("ffmpeg.exe");
        if beside.is_file() {
            return Some(beside);
        }
    }

    // 3. Base directory
    let in_base = base_dir.join("ffmpeg.exe");
    if in_base.is_file() {
        return Some(in_base);
    }

    // 4. Parent directory (project root)
    if let Some(parent) = base_dir.parent() {
        let in_parent = parent.join("ffmpeg.exe");
        if in_parent.is_file() {
            return Some(in_parent);
        }
    }

    // 5. Extract embedded FFmpeg
    #[cfg(embedded_ffmpeg)]
    {
        if let Some(extracted) = extract_embedded_ffmpeg() {
            return Some(extracted);
        }
    }

    // 6. PATH
    if Command::new("ffmpeg")
        .arg("-version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok()
    {
        return Some(PathBuf::from("ffmpeg"));
    }

    None
}

#[cfg(embedded_ffmpeg)]
fn extract_embedded_ffmpeg() -> Option<PathBuf> {
    let target = if let Ok(exe) = std::env::current_exe() {
        exe.parent().unwrap().join("ffmpeg.exe")
    } else {
        return None;
    };

    // Check if already extracted and same size
    if target.is_file() {
        if let Ok(meta) = std::fs::metadata(&target) {
            if meta.len() == EMBEDDED_FFMPEG.len() as u64 {
                return Some(target);
            }
        }
    }

    // Extract
    tracing::info!("Extracting embedded ffmpeg.exe ({} bytes)...", EMBEDDED_FFMPEG.len());
    match std::fs::File::create(&target) {
        Ok(mut f) => {
            if f.write_all(EMBEDDED_FFMPEG).is_ok() {
                tracing::info!("ffmpeg.exe extracted to {}", target.display());
                return Some(target);
            }
        }
        Err(e) => {
            tracing::warn!("Failed to extract ffmpeg.exe: {}", e);
        }
    }
    None
}

// ============================================================
// SECTION 5: Command Builder
// ============================================================

fn build_capture_cmd(
    ffmpeg: &Path,
    settings: &Settings,
    source: &str,
    tmp_video: &Path,
) -> Vec<String> {
    let mut cmd = vec![
        ffmpeg.to_string_lossy().to_string(),
        "-f".to_string(),
        "gdigrab".to_string(),
        "-framerate".to_string(),
        settings.fps.to_string(),
        "-draw_mouse".to_string(),
        if settings.draw_mouse { "1" } else { "0" }.to_string(),
        "-i".to_string(),
        source.to_string(),
    ];
    
    cmd.extend(["-c:v".to_string(), settings.encoder.clone()]);
    
    match settings.encoder.as_str() {
        "mpeg4" => {
            cmd.extend(["-q:v".to_string(), "7".to_string()]);
        }
        "libx264" => {
            cmd.extend(["-preset".to_string(), "fast".to_string()]);
            cmd.extend(["-crf".to_string(), "23".to_string()]);
        }
        "h264_nvenc" | "h264_qsv" | "h264_amf" => {
            cmd.extend(["-preset".to_string(), "fast".to_string()]);
        }
        _ => {}
    }
    
    cmd.extend(["-y".to_string(), tmp_video.to_string_lossy().to_string()]);
    cmd
}

/// A handle to a cpal audio recording thread. Drop or call stop() to finish.
struct CpalRecordingHandle {
    stop_flag: Arc<std::sync::atomic::AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl CpalRecordingHandle {
    fn stop(&mut self) {
        self.stop_flag
            .store(true, std::sync::atomic::Ordering::SeqCst);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

impl Drop for CpalRecordingHandle {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Start recording audio from a cpal device to a WAV file.
/// For output devices, WASAPI loopback is automatically enabled by cpal.
fn start_cpal_recording(
    device_name: &str,
    device_type: &str,
    output_path: PathBuf,
) -> Result<CpalRecordingHandle, String> {
    let host = cpal::default_host();

    // Find the device by name and type
    let device = if device_type == "output" {
        host.output_devices()
            .map_err(|e| format!("Failed to enumerate output devices: {}", e))?
            .find(|d| d.name().map(|n| n == device_name).unwrap_or(false))
    } else {
        host.input_devices()
            .map_err(|e| format!("Failed to enumerate input devices: {}", e))?
            .find(|d| d.name().map(|n| n == device_name).unwrap_or(false))
    };

    let device = device.ok_or_else(|| format!("Audio device not found: {}", device_name))?;

    // For loopback (output devices used as input), cpal WASAPI automatically sets
    // AUDCLNT_STREAMFLAGS_LOOPBACK when build_input_stream is called on an output device.
    let config = device
        .default_input_config()
        .or_else(|_| device.default_output_config())
        .map_err(|e| format!("No supported audio config for {}: {}", device_name, e))?;

    let sample_format = config.sample_format();
    let wav_spec = hound::WavSpec {
        channels: config.channels(),
        sample_rate: config.sample_rate().0,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };

    let stop_flag = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let stop_clone = stop_flag.clone();
    let config = config.into();

    let thread = std::thread::spawn(move || {
        let writer = match hound::WavWriter::create(&output_path, wav_spec) {
            Ok(w) => w,
            Err(e) => {
                tracing::error!("Failed to create WAV file {:?}: {}", output_path, e);
                return;
            }
        };
        let writer = Arc::new(std::sync::Mutex::new(Some(writer)));
        let writer_clone = writer.clone();

        let err_fn = |err: cpal::StreamError| {
            tracing::warn!("Audio stream error: {}", err);
        };

        let stream = match sample_format {
            cpal::SampleFormat::I16 => device.build_input_stream(
                &config,
                {
                    let writer = writer.clone();
                    let stop = stop_clone.clone();
                    move |data: &[i16], _: &cpal::InputCallbackInfo| {
                        if stop.load(std::sync::atomic::Ordering::Relaxed) {
                            return;
                        }
                        if let Ok(mut guard) = writer.lock() {
                            if let Some(ref mut w) = *guard {
                                for &sample in data {
                                    let _ = w.write_sample(sample);
                                }
                            }
                        }
                    }
                },
                err_fn,
                None,
            ),
            cpal::SampleFormat::F32 => device.build_input_stream(
                &config,
                {
                    let writer = writer.clone();
                    let stop = stop_clone.clone();
                    move |data: &[f32], _: &cpal::InputCallbackInfo| {
                        if stop.load(std::sync::atomic::Ordering::Relaxed) {
                            return;
                        }
                        if let Ok(mut guard) = writer.lock() {
                            if let Some(ref mut w) = *guard {
                                for &sample in data {
                                    let s = (sample * 32767.0).clamp(-32768.0, 32767.0) as i16;
                                    let _ = w.write_sample(s);
                                }
                            }
                        }
                    }
                },
                err_fn,
                None,
            ),
            _ => device.build_input_stream(
                &config,
                {
                    let writer = writer.clone();
                    let stop = stop_clone.clone();
                    move |data: &[i16], _: &cpal::InputCallbackInfo| {
                        if stop.load(std::sync::atomic::Ordering::Relaxed) {
                            return;
                        }
                        if let Ok(mut guard) = writer.lock() {
                            if let Some(ref mut w) = *guard {
                                for &sample in data {
                                    let _ = w.write_sample(sample);
                                }
                            }
                        }
                    }
                },
                err_fn,
                None,
            ),
        };

        match stream {
            Ok(stream) => {
                if let Err(e) = stream.play() {
                    tracing::error!("Failed to start audio stream: {}", e);
                    return;
                }
                // Wait until stopped
                while !stop_clone.load(std::sync::atomic::Ordering::Relaxed) {
                    std::thread::sleep(std::time::Duration::from_millis(100));
                }
                drop(stream);
            }
            Err(e) => {
                tracing::error!("Failed to build audio input stream: {}", e);
            }
        }

        // Finalize WAV file
        if let Ok(mut guard) = writer_clone.lock() {
            if let Some(w) = guard.take() {
                let _ = w.finalize();
            }
        };
    });

    Ok(CpalRecordingHandle {
        stop_flag,
        thread: Some(thread),
    })
}

fn build_webcam_cmd(ffmpeg: &Path, device_name: &str, output: &Path) -> Vec<String> {
    vec![
        ffmpeg.to_string_lossy().to_string(),
        "-f".to_string(),
        "dshow".to_string(),
        "-i".to_string(),
        format!("video={}", device_name),
        "-y".to_string(),
        "-c:v".to_string(),
        "mpeg4".to_string(),
        "-qscale:v".to_string(),
        "7".to_string(),
        output.to_string_lossy().to_string(),
    ]
}

fn build_merge_cmd(
    ffmpeg: &Path,
    tmp_dir: &Path,
    output: &Path,
    audio_count: usize,
    audio_delay_ms: u64,
    has_webcam: bool,
    encoder: &str,
) -> Vec<String> {
    let mut cmd = vec![ffmpeg.to_string_lossy().to_string()];

    // Input: video
    cmd.extend(["-i".to_string(), tmp_dir.join("tmp.mkv").to_string_lossy().to_string()]);

    // Input: audio files
    for i in 0..audio_count {
        cmd.extend([
            "-i".to_string(),
            tmp_dir
                .join(format!("tmp_{}.wav", i))
                .to_string_lossy()
                .to_string(),
        ]);
    }

    // Audio filter
    if audio_count > 0 {
        if audio_count == 1 {
            if audio_delay_ms > 0 {
                cmd.extend([
                    "-af".to_string(),
                    format!("adelay={}|{}", audio_delay_ms, audio_delay_ms),
                ]);
            }
        } else {
            let merge_inputs: String = (0..audio_count).map(|i| format!("[{}:a]", i + 1)).collect();
            if audio_delay_ms > 0 {
                cmd.extend([
                    "-filter_complex".to_string(),
                    format!(
                        "{}amerge=inputs={}[merged];[merged]adelay={}|{}[out]",
                        merge_inputs, audio_count, audio_delay_ms, audio_delay_ms
                    ),
                    "-map".to_string(),
                    "0:v".to_string(),
                    "-map".to_string(),
                    "[out]".to_string(),
                ]);
            } else {
                cmd.extend([
                    "-filter_complex".to_string(),
                    format!("{}amerge=inputs={}[out]", merge_inputs, audio_count),
                    "-map".to_string(),
                    "0:v".to_string(),
                    "-map".to_string(),
                    "[out]".to_string(),
                ]);
            }
        }
        cmd.extend(["-ac".to_string(), "2".to_string()]);
    }

    // Webcam overlay
    if has_webcam {
        let webcam_path = tmp_dir.join("webcamtmp.mkv");
        // Webcam input index = 1 (video) + audio_count (audio inputs)
        let webcam_idx = 1 + audio_count;
        cmd.extend([
            "-i".to_string(),
            webcam_path.to_string_lossy().to_string(),
            "-filter_complex".to_string(),
            format!(
                "[{}:v] scale=640:-1 [inner]; [0:v][inner] overlay=0:0 [out]",
                webcam_idx
            ),
            "-map".to_string(),
            "[out]".to_string(),
        ]);
    }

    // Video codec
    if !has_webcam {
        match encoder {
            "h264_nvenc" | "h264_qsv" | "h264_amf" | "libx264" => {
                cmd.extend(["-c:v".to_string(), encoder.to_string()]);
                if encoder == "libx264" {
                    cmd.extend(["-preset".to_string(), "fast".to_string()]);
                    cmd.extend(["-crf".to_string(), "23".to_string()]);
                } else {
                    cmd.extend(["-preset".to_string(), "fast".to_string()]);
                }
            }
            _ => {
                cmd.extend(["-c:v".to_string(), "copy".to_string()]);
            }
        }
    }

    cmd.extend([
        "-shortest".to_string(),
        "-y".to_string(),
        output.to_string_lossy().to_string(),
    ]);
    cmd
}

// ============================================================
// SECTION 6: Device Enumerator
// ============================================================

struct DeviceEnumerator {
    ffmpeg_path: PathBuf,
    cache: Option<DeviceList>,
    cache_time: Instant,
    cache_ttl: Duration,
}

#[derive(Debug, Clone)]
struct DeviceList {
    webcam: Vec<String>,
    audio: Vec<AudioDeviceInfo>,
}

impl DeviceEnumerator {
    fn new(ffmpeg_path: PathBuf) -> Self {
        Self {
            ffmpeg_path,
            cache: None,
            cache_time: Instant::now(),
            cache_ttl: Duration::from_secs(10),
        }
    }

    fn list_all(&mut self) -> DeviceList {
        if let Some(ref cache) = self.cache {
            if self.cache_time.elapsed() < self.cache_ttl {
                return cache.clone();
            }
        }

        // Enumerate audio devices via cpal (supports WASAPI loopback for output devices)
        let audio = enumerate_audio_devices();

        // Enumerate webcams via FFmpeg dshow (cpal doesn't handle video)
        let webcam = self.enumerate_webcams();

        let result = DeviceList { webcam, audio };
        self.cache = Some(result.clone());
        self.cache_time = Instant::now();
        result
    }

    fn enumerate_webcams(&self) -> Vec<String> {
        let mut cmd = Command::new(&self.ffmpeg_path);
        cmd.args(["-list_devices", "true", "-f", "dshow", "-i", "dummy"])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        #[cfg(windows)]
        cmd.creation_flags(CREATE_NO_WINDOW);

        let output = cmd.spawn().and_then(|child| child.wait_with_output());

        match output {
            Ok(out) => {
                let text = String::from_utf8_lossy(&out.stdout).to_string()
                    + &String::from_utf8_lossy(&out.stderr);
                parse_dshow_webcams(&text)
            }
            Err(_) => vec![],
        }
    }
}

/// Enumerate audio devices via cpal. Input devices get type "input", output devices get "output".
/// Output devices can be used for WASAPI loopback (recording system audio).
fn enumerate_audio_devices() -> Vec<AudioDeviceInfo> {
    let mut devices = vec![];
    let host = cpal::default_host();

    // Input devices (microphone, line-in, stereo mix, etc.)
    if let Ok(inputs) = host.input_devices() {
        for dev in inputs {
            if let Ok(name) = dev.name() {
                devices.push(AudioDeviceInfo {
                    name,
                    device_type: "input".to_string(),
                });
            }
        }
    }

    // Output devices (speakers, headphones) — available for loopback capture
    if let Ok(outputs) = host.output_devices() {
        for dev in outputs {
            if let Ok(name) = dev.name() {
                devices.push(AudioDeviceInfo {
                    name,
                    device_type: "output".to_string(),
                });
            }
        }
    }

    devices
}

/// Parse FFmpeg dshow output for video (webcam) devices only.
fn parse_dshow_webcams(output: &str) -> Vec<String> {
    let mut webcams = vec![];

    // FFmpeg 7.x: per-line "(video)" tag
    let has_per_line_tags = output
        .lines()
        .any(|l| l.trim().ends_with("(video)") || l.trim().ends_with("(audio)"));

    if has_per_line_tags {
        for line in output.lines() {
            let bracket_end = match line.find(']') {
                Some(i) => i,
                None => continue,
            };
            let remainder = line[bracket_end + 1..].trim();
            if !remainder.ends_with("(video)") {
                continue;
            }
            let name_part = &remainder[..remainder.len() - 7];
            let name = name_part.trim().trim_matches('"');
            if !name.is_empty() && !name.starts_with("Alternative name") {
                webcams.push(name.to_string());
            }
        }
    } else {
        // Old format: section before "DirectShow audio"
        let section = if let Some(idx) = output.find("DirectShow audio") {
            &output[..idx]
        } else {
            output
        };
        for line in section.lines() {
            let bracket_end = match line.find(']') {
                Some(i) => i,
                None => continue,
            };
            let remainder = line[bracket_end + 1..].trim();
            if remainder.starts_with('"') {
                let name = remainder.trim_matches(|c: char| c == '"' || c == ' ');
                if !name.is_empty() {
                    webcams.push(name.to_string());
                }
            }
        }
    }

    webcams
}

// ============================================================
// SECTION 7: Recording Engine
// ============================================================

struct EngineInner {
    state: RecordingState,
    filename: Option<String>,
    recording_start: Option<Instant>,
    audio_start: Option<Instant>,
    error_message: Option<String>,
    video_process: Option<Child>,
    audio_handles: Vec<CpalRecordingHandle>,
    webcam_process: Option<Child>,
    audio_device_count: usize,
    has_webcam: bool,
}

impl Default for EngineInner {
    fn default() -> Self {
        Self {
            state: RecordingState::Idle,
            filename: None,
            recording_start: None,
            audio_start: None,
            error_message: None,
            video_process: None,
            audio_handles: vec![],
            webcam_process: None,
            audio_device_count: 0,
            has_webcam: false,
        }
    }
}

struct RecordingEngine {
    inner: Arc<Mutex<EngineInner>>,
    state_tx: watch::Sender<EngineStatus>,
    state_rx: watch::Receiver<EngineStatus>,
    settings: Arc<Mutex<SettingsManager>>,
    device_enumerator: Arc<Mutex<DeviceEnumerator>>,
    ffmpeg_path: PathBuf,
    captures_dir: PathBuf,
    tmp_dir: PathBuf,
}

impl RecordingEngine {
    fn new(
        base_dir: PathBuf,
        settings: Arc<Mutex<SettingsManager>>,
        ffmpeg_path: PathBuf,
    ) -> Self {
        Self::with_captures_dir(base_dir.join("ScreenCaptures"), base_dir, settings, ffmpeg_path)
    }

    fn with_captures_dir(
        captures_dir: PathBuf,
        base_dir: PathBuf,
        settings: Arc<Mutex<SettingsManager>>,
        ffmpeg_path: PathBuf,
    ) -> Self {
        let tmp_dir = base_dir.join("tmp");
        let (state_tx, state_rx) = watch::channel(EngineStatus::default());
        let device_enumerator = Arc::new(Mutex::new(DeviceEnumerator::new(ffmpeg_path.clone())));

        let _ = std::fs::create_dir_all(&captures_dir);
        // Clean up any leftover tmp
        let _ = std::fs::remove_dir_all(&tmp_dir);

        Self {
            inner: Arc::new(Mutex::new(EngineInner::default())),
            state_tx,
            state_rx,
            settings,
            device_enumerator,
            ffmpeg_path,
            captures_dir,
            tmp_dir,
        }
    }

    fn subscribe(&self) -> watch::Receiver<EngineStatus> {
        self.state_rx.clone()
    }

    async fn get_status(&self) -> EngineStatus {
        let inner = self.inner.lock().await;
        build_engine_status(&inner)
    }

    fn notify(&self, inner: &EngineInner) {
        let _ = self.state_tx.send(build_engine_status(inner));
    }

    fn generate_filename(&self) -> String {
        let timestamp = Local::now().format("%Y%m%d_%H%M%S").to_string();
        let mut name = format!("ScreenCapture_{}.mp4", timestamp);
        let mut num = 0;
        while self.captures_dir.join(&name).exists() {
            num += 1;
            name = format!("ScreenCapture_{}_{}.mp4", timestamp, num);
        }
        name
    }

    async fn start_recording(&self, config: RecordConfig) -> Result<String, String> {
        // === Phase 1: Prepare everything BEFORE locking inner ===
        // Read settings and enumerate devices without holding inner lock
        let settings = self.settings.lock().await.get_all();
        let filename = config.filename.clone().unwrap_or_else(|| self.generate_filename());
        let _ = std::fs::create_dir_all(&self.captures_dir);
        let _ = std::fs::create_dir_all(&self.tmp_dir);

        // Build source string
        let source_type = config.source.unwrap_or_else(|| "desktop".to_string());
        let source = if source_type == "title" {
            format!(
                "title={}",
                config.window_title.unwrap_or_default()
            )
        } else {
            "desktop".to_string()
        };

        // Determine audio devices (may need device_enumerator lock, done before inner lock)
        let no_audio = settings.audio_mode == "disabled";
        let audio_devices: Vec<AudioDeviceInfo> = if no_audio {
            vec![]
        } else if settings.audio_mode == "selected" && !settings.audio_devices.is_empty() {
            // Look up device types from cpal enumeration
            let all_devs = self.device_enumerator.lock().await.list_all();
            settings
                .audio_devices
                .iter()
                .map(|name| {
                    let dev_type = all_devs
                        .audio
                        .iter()
                        .find(|d| d.name == *name)
                        .map(|d| d.device_type.clone())
                        .unwrap_or_else(|| "input".to_string());
                    AudioDeviceInfo {
                        name: name.clone(),
                        device_type: dev_type,
                    }
                })
                .collect()
        } else {
            // Default mode: get first input audio device
            let devs = self.device_enumerator.lock().await.list_all();
            devs.audio
                .into_iter()
                .filter(|d| d.device_type == "input")
                .take(1)
                .collect()
        };

        // Spawn video capture process (blocking I/O, before inner lock)
        let tmp_video = self.tmp_dir.join("tmp.mkv");
        let video_cmd = build_capture_cmd(&self.ffmpeg_path, &settings, &source, &tmp_video);
        let stderr_file = std::fs::File::create(self.tmp_dir.join("ffmpeg_stderr.log")).ok();
        let video_proc = spawn_ffmpeg(&video_cmd, stderr_file)?;

        let recording_start = Instant::now();

        // Start audio recording via cpal (supports both input devices and output loopback)
        let mut audio_handles: Vec<CpalRecordingHandle> = vec![];
        let audio_start = if !audio_devices.is_empty() {
            let start = Instant::now();
            for (i, dev) in audio_devices.iter().enumerate() {
                let wav_path = self.tmp_dir.join(format!("tmp_{}.wav", i));
                match start_cpal_recording(&dev.name, &dev.device_type, wav_path) {
                    Ok(handle) => {
                        audio_handles.push(handle);
                    }
                    Err(e) => {
                        tracing::warn!(
                            "Failed to start audio capture for {} ({}): {}",
                            dev.name,
                            dev.device_type,
                            e
                        );
                    }
                }
            }
            Some(start)
        } else {
            None
        };

        // Spawn webcam capture (blocking I/O, before inner lock)
        let has_webcam = config.webcam.unwrap_or(false);
        let webcam_proc = if has_webcam {
            if let Some(ref dev) = config.webcam_device {
                if !dev.is_empty() {
                    let webcam_output = self.tmp_dir.join("webcamtmp.mkv");
                    let webcam_cmd =
                        build_webcam_cmd(&self.ffmpeg_path, dev, &webcam_output);
                    match spawn_ffmpeg(&webcam_cmd, None) {
                        Ok(proc) => Some(proc),
                        Err(e) => {
                            tracing::warn!("Failed to start webcam: {}", e);
                            None
                        }
                    }
                } else {
                    None
                }
            } else {
                None
            }
        } else {
            None
        };

        // === Phase 2: Lock inner briefly to update state ===
        let mut inner = self.inner.lock().await;
        if inner.state != RecordingState::Idle {
            // Already recording — kill the processes we just spawned
            drop(inner);
            let mut vp = Some(video_proc);
            let mut wp = webcam_proc;
            tokio::task::spawn_blocking(move || {
                if let Some(ref mut p) = vp { let _ = p.kill(); let _ = p.wait(); }
                if let Some(ref mut p) = wp { let _ = p.kill(); let _ = p.wait(); }
            });
            // Stop cpal audio handles
            for mut h in audio_handles {
                h.stop();
            }
            return Err("Already recording or merging".to_string());
        }
        let audio_count = audio_handles.len();
        inner.error_message = None;
        inner.state = RecordingState::Recording;
        inner.recording_start = Some(recording_start);
        inner.audio_start = audio_start;
        inner.filename = Some(filename.clone());
        inner.video_process = Some(video_proc);
        inner.audio_handles = audio_handles;
        inner.audio_device_count = audio_count;
        inner.webcam_process = webcam_proc;
        inner.has_webcam = has_webcam && inner.webcam_process.is_some();

        self.notify(&inner);
        drop(inner);

        // Spawn monitor task
        // Spawn monitor task with lightweight engine reference
        {
            let monitor_ref = EngineRef {
                inner: self.inner.clone(),
                state_tx: self.state_tx.clone(),
                tmp_dir: self.tmp_dir.clone(),
                captures_dir: self.captures_dir.clone(),
                ffmpeg_path: self.ffmpeg_path.clone(),
                settings: self.settings.clone(),
            };
            tokio::spawn(async move {
                monitor_ref.monitor_loop().await;
            });
        }

        Ok(filename)
    }

    async fn stop_recording(&self) {
        // Take ownership of processes/handles under lock, then stop outside lock
        let (mut video, audio_handles, mut webcam) = {
            let mut inner = self.inner.lock().await;
            if inner.state != RecordingState::Recording {
                return;
            }
            inner.state = RecordingState::Merging;
            self.notify(&inner);

            (
                inner.video_process.take(),
                std::mem::take(&mut inner.audio_handles),
                inner.webcam_process.take(),
            )
        };

        // Stop cpal audio recordings (finalize WAV files)
        tokio::task::spawn_blocking(move || {
            if let Some(ref mut proc) = video {
                let _ = proc.kill();
                let _ = proc.wait();
            }
            for mut handle in audio_handles {
                handle.stop();
            }
            if let Some(ref mut proc) = webcam {
                let _ = proc.kill();
                let _ = proc.wait();
            }
        })
        .await
        .ok();

        // Spawn merge task
        let engine_clone = EngineRef {
            inner: self.inner.clone(),
            state_tx: self.state_tx.clone(),
            tmp_dir: self.tmp_dir.clone(),
            captures_dir: self.captures_dir.clone(),
            ffmpeg_path: self.ffmpeg_path.clone(),
            settings: self.settings.clone(),
        };

        tokio::spawn(async move {
            engine_clone.merge().await;
        });
    }

    fn list_files(&self) -> Vec<FileInfo> {
        let mut files = vec![];
        if let Ok(entries) = std::fs::read_dir(&self.captures_dir) {
            for entry in entries.filter_map(|e| e.ok()) {
                let path = entry.path();
                if path.is_file() {
                    if let Some(ext) = path.extension() {
                        let ext = ext.to_string_lossy().to_lowercase();
                        if ["mp4", "mkv", "avi"].contains(&ext.as_str()) {
                            if let Ok(meta) = path.metadata() {
                                let date = meta
                                    .modified()
                                    .unwrap_or(SystemTime::UNIX_EPOCH);
                                let datetime: chrono::DateTime<Local> = date.into();
                                files.push(FileInfo {
                                    name: entry
                                        .file_name()
                                        .to_string_lossy()
                                        .to_string(),
                                    size: meta.len(),
                                    size_human: human_size(meta.len()),
                                    date: datetime.to_rfc3339(),
                                });
                            }
                        }
                    }
                }
            }
        }
        files.sort_by(|a, b| b.date.cmp(&a.date));
        files
    }

    fn delete_file(&self, name: &str) -> bool {
        let safe_name = Path::new(name)
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        let path = self.captures_dir.join(&safe_name);
        if path.is_file() {
            std::fs::remove_file(&path).is_ok()
        } else {
            false
        }
    }
}

/// Lightweight reference to engine pieces for async tasks
#[derive(Clone)]
struct EngineRef {
    inner: Arc<Mutex<EngineInner>>,
    state_tx: watch::Sender<EngineStatus>,
    tmp_dir: PathBuf,
    captures_dir: PathBuf,
    ffmpeg_path: PathBuf,
    settings: Arc<Mutex<SettingsManager>>,
}

impl EngineRef {
    fn notify(&self, inner: &EngineInner) {
        let _ = self.state_tx.send(build_engine_status(inner));
    }

    async fn monitor_loop(&self) {
        loop {
            tokio::time::sleep(Duration::from_secs(1)).await;

            // Brief lock: check state and try_wait on video process
            let crashed = {
                let mut inner = self.inner.lock().await;
                if inner.state != RecordingState::Recording {
                    return;
                }

                // Send periodic status update for elapsed time
                self.notify(&inner);

                // Check if video process has exited unexpectedly
                if let Some(ref mut proc) = inner.video_process {
                    matches!(proc.try_wait(), Ok(Some(_)))
                } else {
                    false
                }
            }; // lock released here

            if crashed {
                let stderr = read_log(&self.tmp_dir.join("ffmpeg_stderr.log"));

                // Take handles out under lock, then stop outside
                let (audio_handles, mut webcam) = {
                    let mut inner = self.inner.lock().await;
                    inner.error_message = Some(format!("FFmpeg crashed: {}", stderr));
                    inner.state = RecordingState::Idle;
                    self.notify(&inner);
                    (
                        std::mem::take(&mut inner.audio_handles),
                        inner.webcam_process.take(),
                    )
                };

                // Stop audio and kill webcam outside lock
                for mut h in audio_handles {
                    h.stop();
                }
                if let Some(ref mut p) = webcam {
                    let _ = p.kill();
                }

                let _ = std::fs::remove_dir_all(&self.tmp_dir);
                return;
            }
        }
    }

    async fn merge(&self) {
        let result = self.do_merge().await;

        let mut inner = self.inner.lock().await;
        if let Err(e) = result {
            inner.error_message = Some(e);
        }
        inner.state = RecordingState::Idle;
        self.notify(&inner);
        drop(inner);

        let _ = std::fs::remove_dir_all(&self.tmp_dir);
    }

    async fn do_merge(&self) -> Result<(), String> {
        // Read settings before inner lock to avoid nested lock
        let encoder = self.settings.lock().await.get_all().encoder;

        let inner = self.inner.lock().await;
        let filename = inner
            .filename
            .clone()
            .ok_or_else(|| "No filename set".to_string())?;

        let audio_delay_ms = if let (Some(rec_start), Some(aud_start)) =
            (inner.recording_start, inner.audio_start)
        {
            let delay = aud_start.duration_since(rec_start);
            delay.as_millis() as u64
        } else {
            0
        };

        let audio_count = inner.audio_device_count;
        let has_webcam = inner.has_webcam;
        drop(inner);

        let output_path = self.captures_dir.join(&filename);

        if audio_count == 0 && !has_webcam {
            // No audio, just copy video
            let tmp_video = self.tmp_dir.join("tmp.mkv");
            if tmp_video.is_file() {
                std::fs::copy(&tmp_video, &output_path)
                    .map_err(|e| format!("Failed to copy video: {}", e))?;
            } else {
                return Err("Video file not found after recording".to_string());
            }
            return Ok(());
        }

        // Check audio files
        let mut valid_audio = 0;
        for i in 0..audio_count {
            let wav = self.tmp_dir.join(format!("tmp_{}.wav", i));
            if wav.is_file() {
                if let Ok(meta) = wav.metadata() {
                    if meta.len() > 100 {
                        valid_audio += 1;
                    }
                }
            }
        }

        if valid_audio == 0 && !has_webcam {
            // Audio files too small or missing, just copy video
            let tmp_video = self.tmp_dir.join("tmp.mkv");
            if tmp_video.is_file() {
                std::fs::copy(&tmp_video, &output_path)
                    .map_err(|e| format!("Failed to copy video: {}", e))?;
            }
            return Ok(());
        }

        let merge_cmd = build_merge_cmd(
            &self.ffmpeg_path,
            &self.tmp_dir,
            &output_path,
            valid_audio,
            audio_delay_ms,
            has_webcam,
            &encoder,
        );

        let stderr_file = std::fs::File::create(self.tmp_dir.join("merge_stderr.log")).ok();
        let mut proc = spawn_ffmpeg(&merge_cmd, stderr_file)
            .map_err(|e| format!("Failed to start merge: {}", e))?;

        let status = tokio::task::spawn_blocking(move || proc.wait())
            .await
            .map_err(|e| format!("Merge task panicked: {}", e))?
            .map_err(|e| format!("Merge process error: {}", e))?;

        if !status.success() {
            let stderr = read_log(&self.tmp_dir.join("merge_stderr.log"));
            return Err(format!(
                "Merge failed (exit {}): {}",
                status.code().unwrap_or(-1),
                stderr
            ));
        }

        Ok(())
    }
}

fn build_engine_status(inner: &EngineInner) -> EngineStatus {
    let elapsed = if inner.state == RecordingState::Recording {
        inner
            .recording_start
            .map(|t| t.elapsed().as_secs_f64())
            .unwrap_or(0.0)
    } else {
        0.0
    };

    EngineStatus {
        state: inner.state,
        recording: inner.state == RecordingState::Recording,
        merging: inner.state == RecordingState::Merging,
        filename: inner.filename.clone(),
        elapsed,
        error: inner.error_message.clone(),
    }
}

fn spawn_ffmpeg(cmd: &[String], stderr_file: Option<std::fs::File>) -> Result<Child, String> {
    if cmd.is_empty() {
        return Err("Empty command".to_string());
    }

    let stderr_cfg = match stderr_file {
        Some(f) => Stdio::from(f),
        None => Stdio::null(),
    };

    let mut command = Command::new(&cmd[0]);
    command
        .args(&cmd[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(stderr_cfg);

    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    command
        .spawn()
        .map_err(|e| format!("Failed to spawn FFmpeg: {}", e))
}

fn read_log(path: &Path) -> String {
    std::fs::read_to_string(path)
        .unwrap_or_default()
        .chars()
        .rev()
        .take(500)
        .collect::<String>()
        .chars()
        .rev()
        .collect()
}

// ============================================================
// SECTION 8: CLI Utilities
// ============================================================

fn human_size(size: u64) -> String {
    let mut s = size as f64;
    for unit in &["B", "KB", "MB", "GB"] {
        if s < 1024.0 {
            return format!("{:.1} {}", s, unit);
        }
        s /= 1024.0;
    }
    format!("{:.1} TB", s)
}

fn parse_size(s: &str) -> Option<u64> {
    let s = s.trim().to_uppercase();
    let re = regex::Regex::new(r"^(\d+(?:\.\d+)?)\s*([A-Z]*)$").ok()?;
    let caps = re.captures(&s)?;
    let num: f64 = caps.get(1)?.as_str().parse().ok()?;
    let unit = caps.get(2).map(|m| m.as_str()).unwrap_or("B");
    let multiplier: u64 = match unit {
        "" | "B" => 1,
        "K" | "KB" => 1024,
        "M" | "MB" => 1024 * 1024,
        "G" | "GB" => 1024 * 1024 * 1024,
        "T" | "TB" => 1024 * 1024 * 1024 * 1024,
        _ => return None,
    };
    Some((num * multiplier as f64) as u64)
}

fn format_duration(seconds: f64) -> String {
    let total = seconds as u64;
    let h = total / 3600;
    let m = (total % 3600) / 60;
    let s = total % 60;
    if h > 0 {
        format!("{:02}:{:02}:{:02}", h, m, s)
    } else {
        format!("{:02}:{:02}", m, s)
    }
}

fn get_tmp_dir_size(tmp_dir: &Path) -> u64 {
    let mut total = 0u64;
    if let Ok(entries) = std::fs::read_dir(tmp_dir) {
        for entry in entries.filter_map(|e| e.ok()) {
            if let Ok(meta) = entry.metadata() {
                if meta.is_file() {
                    total += meta.len();
                }
            }
        }
    }
    total
}

// ============================================================
// SECTION 9: CLI Argument Parsing
// ============================================================

#[derive(Parser, Debug)]
#[command(
    name = "screen-recorder",
    about = "Screen Recorder - WebUI / CLI 屏幕录制工具",
)]
struct CliArgs {
    /// Start WebUI mode
    #[arg(long)]
    web: bool,

    /// Open browser automatically when starting WebUI
    #[arg(long)]
    open: bool,

    /// WebUI port (0 = auto-detect available port)
    #[arg(long, default_value = "0")]
    port: u16,

    /// WebUI bind address
    #[arg(long, default_value = "0.0.0.0")]
    host: String,

    // === Video ===
    /// Frame rate 1-120
    #[arg(long, value_parser = clap::value_parser!(u32).range(1..=120))]
    fps: Option<u32>,

    /// Encoder: mpeg4 (CPU), h264_nvenc (NVIDIA), h264_qsv (Intel), h264_amf (AMD), or libx264 (H.264 CPU)
    #[arg(long, value_parser = ["mpeg4", "h264_nvenc", "h264_qsv", "h264_amf", "libx264"])]
    encoder: Option<String>,

    /// Do not draw mouse cursor
    #[arg(long)]
    no_mouse: bool,

    /// Record a specific window by title
    #[arg(long)]
    window: Option<String>,

    /// Enable webcam overlay
    #[arg(long)]
    webcam: bool,

    /// Webcam device name
    #[arg(long)]
    webcam_device: Option<String>,

    // === Audio ===
    /// Disable audio recording
    #[arg(long)]
    no_audio: bool,

    /// Audio device names (can specify multiple)
    #[arg(long, num_args = 1..)]
    audio_devices: Option<Vec<String>>,

    // === Output ===
    /// Output filename
    #[arg(short, long)]
    output: Option<String>,

    /// Output directory
    #[arg(long)]
    output_dir: Option<PathBuf>,

    // === Stop Conditions ===
    /// Recording duration in seconds (0 = unlimited)
    #[arg(long, default_value = "0")]
    duration: u64,

    /// Maximum file size (e.g., 500M, 2G)
    #[arg(long)]
    max_size: Option<String>,

    // === Schedule ===
    /// Scheduled recording: HH:MM (start only) or HH:MM-HH:MM (start-end)
    #[arg(long)]
    schedule: Option<String>,

    // === Logging ===
    /// Print recording status to stderr
    #[arg(short, long)]
    verbose: bool,

    /// Write log to file
    #[arg(long)]
    log_file: Option<PathBuf>,

    // === Config ===
    /// Load settings from JSON config file
    #[arg(long)]
    config: Option<PathBuf>,

    /// List available devices
    #[arg(long)]
    list_devices: bool,

    /// Path to ffmpeg executable
    #[arg(long)]
    ffmpeg_path: Option<PathBuf>,
}

fn is_cli_mode(args: &CliArgs) -> bool {
    args.fps.is_some()
        || args.encoder.is_some()
        || args.no_mouse
        || args.window.is_some()
        || args.no_audio
        || args.audio_devices.is_some()
        || args.output.is_some()
        || args.output_dir.is_some()
        || args.duration > 0
        || args.max_size.is_some()
        || args.schedule.is_some()
        || args.list_devices
        || args.webcam
}

// ============================================================
// SECTION 10: API Handlers
// ============================================================

#[derive(Clone)]
struct AppState {
    engine: Arc<RecordingEngine>,
}

async fn serve_index() -> impl IntoResponse {
    (
        [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
        INDEX_HTML,
    )
}

async fn serve_css() -> impl IntoResponse {
    (
        [
            (header::CONTENT_TYPE, "text/css; charset=utf-8"),
            (header::CACHE_CONTROL, "public, max-age=3600"),
        ],
        APP_CSS,
    )
}

async fn serve_js() -> impl IntoResponse {
    (
        [
            (header::CONTENT_TYPE, "application/javascript; charset=utf-8"),
            (header::CACHE_CONTROL, "public, max-age=3600"),
        ],
        APP_JS,
    )
}

async fn serve_favicon() -> impl IntoResponse {
    (
        [
            (header::CONTENT_TYPE, "image/x-icon"),
            (header::CACHE_CONTROL, "public, max-age=86400"),
        ],
        FAVICON_ICO,
    )
}

async fn api_status(State(state): State<AppState>) -> Json<EngineStatus> {
    Json(state.engine.get_status().await)
}

async fn api_start(
    State(state): State<AppState>,
    Json(config): Json<RecordConfig>,
) -> Result<Json<StartResponse>, (StatusCode, Json<ErrorResponse>)> {
    match state.engine.start_recording(config).await {
        Ok(filename) => Ok(Json(StartResponse {
            ok: true,
            filename: Some(filename),
        })),
        Err(e) => Err((
            StatusCode::CONFLICT,
            Json(ErrorResponse {
                ok: false,
                error: e,
            }),
        )),
    }
}

async fn api_stop(State(state): State<AppState>) -> Json<OkResponse> {
    state.engine.stop_recording().await;
    Json(OkResponse { ok: true })
}

async fn api_files(State(state): State<AppState>) -> Json<FilesResponse> {
    Json(FilesResponse {
        files: state.engine.list_files(),
    })
}

async fn api_delete_file(
    State(state): State<AppState>,
    AxumPath(name): AxumPath<String>,
) -> Result<Json<OkResponse>, (StatusCode, Json<ErrorResponse>)> {
    if state.engine.delete_file(&name) {
        Ok(Json(OkResponse { ok: true }))
    } else {
        Err((
            StatusCode::NOT_FOUND,
            Json(ErrorResponse {
                ok: false,
                error: "File not found".to_string(),
            }),
        ))
    }
}

async fn api_download_file(
    State(state): State<AppState>,
    AxumPath(name): AxumPath<String>,
) -> Result<impl IntoResponse, StatusCode> {
    let safe_name = Path::new(&name)
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .ok_or(StatusCode::BAD_REQUEST)?;

    let path = state.engine.captures_dir.join(&safe_name);
    if !path.is_file() {
        return Err(StatusCode::NOT_FOUND);
    }

    let file = tokio::fs::File::open(&path)
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let stream = ReaderStream::new(file);
    let body = Body::from_stream(stream);

    Ok((
        [
            (
                header::CONTENT_TYPE,
                "application/octet-stream".to_string(),
            ),
            (
                header::CONTENT_DISPOSITION,
                format!("attachment; filename=\"{}\"", safe_name),
            ),
        ],
        body,
    ))
}

async fn api_get_settings(State(state): State<AppState>) -> Json<Settings> {
    Json(state.engine.settings.lock().await.get_all())
}

async fn api_update_settings(
    State(state): State<AppState>,
    Json(changes): Json<serde_json::Value>,
) -> Json<Settings> {
    Json(state.engine.settings.lock().await.update(&changes))
}

async fn api_devices(State(state): State<AppState>) -> Json<DevicesResponse> {
    let devs = state.engine.device_enumerator.lock().await.list_all();
    Json(DevicesResponse {
        audio: devs.audio,
        webcam: devs.webcam,
    })
}

async fn api_events(
    State(state): State<AppState>,
) -> Sse<impl Stream<Item = Result<Event, Infallible>>> {
    let mut rx = state.engine.subscribe();

    let stream = async_stream::stream! {
        // Send initial state
        let current = rx.borrow().clone();
        yield Ok(Event::default().data(serde_json::to_string(&current).unwrap_or_default()));

        loop {
            match tokio::time::timeout(Duration::from_secs(1), rx.changed()).await {
                Ok(Ok(())) => {
                    let status = rx.borrow().clone();
                    yield Ok(Event::default().data(serde_json::to_string(&status).unwrap_or_default()));
                    if status.recording {
                        tokio::time::sleep(Duration::from_millis(500)).await;
                    }
                }
                Ok(Err(_)) => break, // channel closed
                Err(_) => {
                    // Timeout: during recording, send periodic updates
                    let status = rx.borrow().clone();
                    if status.recording {
                        yield Ok(Event::default().data(serde_json::to_string(&status).unwrap_or_default()));
                    }
                }
            }
        }
    };

    Sse::new(stream).keep_alive(
        KeepAlive::new()
            .interval(Duration::from_secs(15))
            .text("keep-alive"),
    )
}

async fn api_next_filename(State(state): State<AppState>) -> Json<FilenameResponse> {
    Json(FilenameResponse {
        filename: state.engine.generate_filename(),
    })
}

async fn api_available_encoders(State(state): State<AppState>) -> Json<EncodersResponse> {
    let available = state.engine.settings.lock().await.detect_available_encoders();
    let encoders = available
        .into_iter()
        .map(|id| EncoderInfo {
            id: id.clone(),
            name: get_encoder_name(&id).to_string(),
            is_hardware: is_hardware_encoder(&id),
        })
        .collect();
    Json(EncodersResponse { encoders })
}

async fn api_best_encoder(State(state): State<AppState>) -> Json<BestEncoderResponse> {
    let best = state.engine.settings.lock().await.get_best_encoder();
    Json(BestEncoderResponse {
        encoder: best.clone(),
        name: get_encoder_name(&best).to_string(),
        is_hardware: is_hardware_encoder(&best),
    })
}

// ============================================================
// SECTION 11: Web Server Setup
// ============================================================

fn create_router(state: AppState) -> Router {
    Router::new()
        // Static assets
        .route("/", get(serve_index))
        .route("/static/css/app.css", get(serve_css))
        .route("/static/js/app.js", get(serve_js))
        .route("/static/favicon.ico", get(serve_favicon))
        // API
        .route("/api/status", get(api_status))
        .route("/api/record/start", post(api_start))
        .route("/api/record/stop", post(api_stop))
        .route("/api/files", get(api_files))
        .route("/api/files/{name}", delete(api_delete_file))
        .route("/api/files/{name}/download", get(api_download_file))
        .route(
            "/api/settings",
            get(api_get_settings).put(api_update_settings),
        )
        .route("/api/devices", get(api_devices))
        .route("/api/events", get(api_events))
        .route("/api/filename/next", get(api_next_filename))
        .route("/api/encoders", get(api_available_encoders))
        .route("/api/encoders/best", get(api_best_encoder))
        .with_state(state)
}

// ============================================================
// SECTION 12: CLI Mode Runner
// ============================================================

async fn run_cli_mode(args: CliArgs) {
    let base_dir = determine_base_dir();
    let ffmpeg_path = find_ffmpeg(
        &base_dir,
        args.ffmpeg_path.as_deref(),
    )
    .expect("ffmpeg.exe not found. Place it beside the executable or use --ffmpeg-path");

    // Load settings
    let settings = if let Some(ref config_path) = args.config {
        SettingsManager::new(config_path.parent().unwrap_or(Path::new(".")), ffmpeg_path.clone())
    } else {
        SettingsManager::new(&base_dir, ffmpeg_path.clone())
    };

    // Apply CLI overrides
    let mut changes = serde_json::Map::new();
    if let Some(fps) = args.fps {
        changes.insert("fps".to_string(), serde_json::Value::from(fps));
    }
    if let Some(ref enc) = args.encoder {
        changes.insert("encoder".to_string(), serde_json::Value::from(enc.clone()));
    }
    if args.no_mouse {
        changes.insert("draw_mouse".to_string(), serde_json::Value::from(false));
    }
    if args.no_audio {
        changes.insert(
            "audio_mode".to_string(),
            serde_json::Value::from("disabled"),
        );
    } else if let Some(ref devs) = args.audio_devices {
        changes.insert(
            "audio_mode".to_string(),
            serde_json::Value::from("selected"),
        );
        changes.insert(
            "audio_devices".to_string(),
            serde_json::Value::from(devs.clone()),
        );
    }

    let settings = Arc::new(Mutex::new(settings));
    if !changes.is_empty() {
        settings
            .lock()
            .await
            .update(&serde_json::Value::Object(changes));
    }

    let engine = if let Some(ref output_dir) = args.output_dir {
        let output_dir = std::fs::canonicalize(output_dir).unwrap_or_else(|_| output_dir.clone());
        let _ = std::fs::create_dir_all(&output_dir);
        Arc::new(RecordingEngine::with_captures_dir(
            output_dir,
            base_dir.clone(),
            settings.clone(),
            ffmpeg_path.clone(),
        ))
    } else {
        Arc::new(RecordingEngine::new(
            base_dir.clone(),
            settings.clone(),
            ffmpeg_path.clone(),
        ))
    };

    // --list-devices
    if args.list_devices {
        let devs = engine.device_enumerator.lock().await.list_all();
        let inputs: Vec<_> = devs.audio.iter().filter(|d| d.device_type == "input").collect();
        let outputs: Vec<_> = devs.audio.iter().filter(|d| d.device_type == "output").collect();

        println!("音频输入设备 (麦克风等):");
        if inputs.is_empty() {
            println!("  (未找到)");
        } else {
            for dev in &inputs {
                println!("  {}", dev.name);
            }
        }
        println!();
        println!("音频输出设备 (扬声器/耳机 - 可录制系统声音):");
        if outputs.is_empty() {
            println!("  (未找到)");
        } else {
            for dev in &outputs {
                println!("  {}", dev.name);
            }
        }
        println!();
        println!("摄像头设备:");
        if devs.webcam.is_empty() {
            println!("  (未找到)");
        } else {
            for cam in &devs.webcam {
                println!("  {}", cam);
            }
        }
        println!();
        println!("提示: 选择\"输出设备\"可通过 WASAPI loopback 录制系统音频。");
        println!("      同时选择输入+输出设备可实现麦克风+扬声器同时录制。");
        return;
    }

    // --schedule: wait for start time, get optional end time
    let schedule_end = if let Some(ref schedule) = args.schedule {
        wait_for_schedule(schedule).await
    } else {
        None
    };

    // Parse max_size
    let max_size_bytes = args
        .max_size
        .as_deref()
        .map(|s| parse_size(s).expect("Invalid --max-size"))
        .unwrap_or(0);

    // Build config
    let config = RecordConfig {
        filename: args.output.clone(),
        source: if args.window.is_some() {
            Some("title".to_string())
        } else {
            Some("desktop".to_string())
        },
        window_title: args.window.clone(),
        webcam: Some(args.webcam),
        webcam_device: args.webcam_device.clone(),
    };

    // Start recording
    let filename = match engine.start_recording(config).await {
        Ok(f) => f,
        Err(e) => {
            eprintln!("错误: {}", e);
            std::process::exit(1);
        }
    };

    tracing::info!("开始录制 → {}", filename);

    // Ctrl+C handler
    let interrupted = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let interrupted_clone = interrupted.clone();

    tokio::spawn(async move {
        tokio::signal::ctrl_c().await.ok();
        interrupted_clone.store(true, std::sync::atomic::Ordering::SeqCst);
        tracing::info!("收到中断信号，正在停止录制...");

        // Second Ctrl+C: force exit
        tokio::signal::ctrl_c().await.ok();
        eprintln!("\n强制退出");
        std::process::exit(1);
    });

    // Monitor loop
    let rx = engine.subscribe();
    let start_time = Instant::now();
    let mut was_recording = false;
    let mut logged_merging = false;

    loop {
        tokio::time::sleep(Duration::from_secs(1)).await;

        let status = rx.borrow().clone();

        // Check interrupt
        if interrupted.load(std::sync::atomic::Ordering::SeqCst)
            && status.state == RecordingState::Recording
        {
            engine.stop_recording().await;
        }

        // Check duration
        if args.duration > 0
            && start_time.elapsed().as_secs() >= args.duration
            && status.state == RecordingState::Recording
        {
            tracing::info!("已达到设定时长，停止录制");
            engine.stop_recording().await;
        }

        // Check max size
        if max_size_bytes > 0 && status.state == RecordingState::Recording {
            let current_size = get_tmp_dir_size(&engine.tmp_dir);
            if current_size >= max_size_bytes {
                tracing::info!("已达到文件大小上限，停止录制");
                engine.stop_recording().await;
            }
        }

        // Check scheduled end time
        if let Some(end_dt) = schedule_end {
            if Local::now().naive_local() >= end_dt
                && status.state == RecordingState::Recording
            {
                tracing::info!("已到达定时结束时间，停止录制");
                engine.stop_recording().await;
            }
        }

        // Verbose output
        if args.verbose && status.state == RecordingState::Recording {
            let elapsed = start_time.elapsed().as_secs_f64();
            tracing::info!("录制中... {}", format_duration(elapsed));
        }

        match status.state {
            RecordingState::Recording => {
                was_recording = true;
            }
            RecordingState::Merging => {
                if !logged_merging {
                    tracing::info!("停止录制，正在合并音视频...");
                    logged_merging = true;
                }
            }
            RecordingState::Idle if was_recording || logged_merging => {
                break;
            }
            RecordingState::Idle => {
                // Crashed before we saw recording
                break;
            }
        }
    }

    // Check result
    let final_status = engine.get_status().await;
    if let Some(error) = final_status.error {
        eprintln!("错误: {}", error);
        std::process::exit(1);
    }

    let output_path = engine.captures_dir.join(&filename);
    if output_path.is_file() {
        let size = std::fs::metadata(&output_path)
            .map(|m| m.len())
            .unwrap_or(0);
        tracing::info!("完成! 文件: {} ({})", output_path.display(), human_size(size));
        println!("{}", output_path.display());
    } else {
        eprintln!("警告: 输出文件不存在: {}", output_path.display());
        std::process::exit(1);
    }
}

/// Parse HH:MM string into (hour, minute). Returns None on invalid input.
fn parse_hhmm(s: &str) -> Option<(u32, u32)> {
    let parts: Vec<&str> = s.split(':').collect();
    if parts.len() != 2 {
        return None;
    }
    let h: u32 = parts[0].parse().ok()?;
    let m: u32 = parts[1].parse().ok()?;
    if h > 23 || m > 59 {
        return None;
    }
    Some((h, m))
}

/// Parse schedule string: "HH:MM" (start only) or "HH:MM-HH:MM" (start-end).
/// Waits until start time, returns the optional end time as a `chrono::NaiveDateTime`.
async fn wait_for_schedule(schedule_str: &str) -> Option<chrono::NaiveDateTime> {
    let (start_str, end_str) = if let Some(idx) = schedule_str.find('-') {
        (&schedule_str[..idx], Some(&schedule_str[idx + 1..]))
    } else {
        (schedule_str, None)
    };

    let (start_h, start_m) = parse_hhmm(start_str).unwrap_or_else(|| {
        eprintln!("--schedule 开始时间格式错误: {} (应为 HH:MM)", start_str);
        std::process::exit(2);
    });

    let end_time = end_str.map(|es| {
        let (end_h, end_m) = parse_hhmm(es).unwrap_or_else(|| {
            eprintln!("--schedule 结束时间格式错误: {} (应为 HH:MM)", es);
            std::process::exit(2);
        });
        (end_h, end_m)
    });

    // Wait for start time
    let now = Local::now();
    let mut start_target = now
        .date_naive()
        .and_hms_opt(start_h, start_m, 0)
        .unwrap();
    if start_target <= now.naive_local() {
        start_target += chrono::Duration::days(1);
    }

    let wait_secs = (start_target - now.naive_local()).num_seconds().max(0) as u64;

    if end_time.is_some() {
        println!(
            "定时录制: {} → {} (等待 {})",
            start_str,
            end_str.unwrap(),
            format_duration(wait_secs as f64)
        );
    } else {
        println!(
            "定时录制: 将在 {} 开始录制 (等待 {})",
            start_str,
            format_duration(wait_secs as f64)
        );
    }

    tokio::time::sleep(Duration::from_secs(wait_secs)).await;

    // Compute end datetime (relative to the actual start moment)
    end_time.map(|(end_h, end_m)| {
        let start_date = Local::now().date_naive();
        let mut end_dt = start_date.and_hms_opt(end_h, end_m, 0).unwrap();
        // If end time <= current time, it means next day
        if end_dt <= Local::now().naive_local() {
            end_dt += chrono::Duration::days(1);
        }
        end_dt
    })
}

// ============================================================
// SECTION 13: Main Entry Point
// ============================================================

fn determine_base_dir() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|p| p.to_path_buf()))
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))
}

/// Try to bind to the specified port. If port is 0, auto-select from preferred range.
async fn bind_with_auto_port(host: &str, port: u16) -> tokio::net::TcpListener {
    if port != 0 {
        // Try the specified port, then fallback to auto-detect
        let addr = format!("{}:{}", host, port);
        match tokio::net::TcpListener::bind(&addr).await {
            Ok(listener) => return listener,
            Err(e) => {
                tracing::warn!("端口 {} 被占用 ({}), 自动选择可用端口...", port, e);
            }
        }
    }

    // Auto-detect: try preferred ports first, then let OS pick
    let preferred_ports = [5000, 5001, 5002, 5003, 8080, 8081, 8888, 9000];
    for &p in &preferred_ports {
        let addr = format!("{}:{}", host, p);
        if let Ok(listener) = tokio::net::TcpListener::bind(&addr).await {
            return listener;
        }
    }

    // Let OS pick any available port
    let addr = format!("{}:0", host);
    tokio::net::TcpListener::bind(&addr)
        .await
        .unwrap_or_else(|e| {
            eprintln!("无法绑定任何端口: {}", e);
            std::process::exit(1);
        })
}

/// On Windows with windows_subsystem = "windows", re-attach parent console
/// so CLI mode can print output.
#[cfg(windows)]
fn attach_console() {
    unsafe {
        extern "system" {
            fn AttachConsole(dw_process_id: u32) -> i32;
        }
        const ATTACH_PARENT_PROCESS: u32 = 0xFFFFFFFF;
        AttachConsole(ATTACH_PARENT_PROCESS);
    }
}

#[cfg(not(windows))]
fn attach_console() {}

/// Wait for a shutdown signal (Ctrl+C or platform-specific termination).
async fn shutdown_signal() {
    let ctrl_c = tokio::signal::ctrl_c();

    #[cfg(windows)]
    {
        // On Windows, also listen for CTRL_CLOSE_EVENT, CTRL_SHUTDOWN_EVENT, etc.
        // These are delivered through ctrl_c on tokio for Windows.
        ctrl_c.await.ok();
    }

    #[cfg(not(windows))]
    {
        use tokio::signal::unix::{signal, SignalKind};
        let mut sigterm = signal(SignalKind::terminate()).ok();
        tokio::select! {
            _ = ctrl_c => {}
            _ = async { if let Some(ref mut s) = sigterm { s.recv().await } else { std::future::pending().await } } => {}
        }
    }
}

#[tokio::main]
async fn main() {
    let args = CliArgs::parse();
    let cli_mode = is_cli_mode(&args);

    // In CLI mode, re-attach console for output (needed due to windows_subsystem = "windows")
    if cli_mode || args.open {
        attach_console();
    }

    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::from_default_env()
                .add_directive(tracing::Level::INFO.into()),
        )
        .with_target(false)
        .init();

    if args.web || !cli_mode {
        // WebUI mode
        let base_dir = determine_base_dir();
        let ffmpeg_path = find_ffmpeg(&base_dir, args.ffmpeg_path.as_deref())
            .expect("ffmpeg.exe not found. Place it beside the executable or use --ffmpeg-path");

        let settings = Arc::new(Mutex::new(SettingsManager::new(&base_dir, ffmpeg_path.clone())));
        let engine = Arc::new(RecordingEngine::new(base_dir, settings, ffmpeg_path));

        let state = AppState {
            engine: engine.clone(),
        };
        let app = create_router(state);

        let listener = bind_with_auto_port(&args.host, args.port).await;
        let actual_addr = listener.local_addr().unwrap();

        tracing::info!("Screen Recorder WebUI running at http://{}", actual_addr);
        tracing::info!(
            "Access from mobile: http://<your-pc-ip>:{}",
            actual_addr.port()
        );
        tracing::info!("WARNING: No authentication. Anyone on your network can control this recorder.");

        // Only open browser if --open flag is set
        if args.open {
            let port = actual_addr.port();
            tokio::spawn(async move {
                tokio::time::sleep(Duration::from_millis(1500)).await;
                let _ = open::that(format!("http://127.0.0.1:{}", port));
            });
        }

        // Graceful shutdown: on Ctrl+C / process termination, save any active recording
        let shutdown_engine = engine.clone();
        axum::serve(listener, app)
            .with_graceful_shutdown(async move {
                shutdown_signal().await;
                tracing::info!("收到关闭信号，正在保存录制...");
                shutdown_engine.stop_recording().await;
                // Give merge a moment to start
                tokio::time::sleep(Duration::from_millis(500)).await;
                // Wait for merge to complete (up to 30s)
                let rx = shutdown_engine.subscribe();
                let deadline = Instant::now() + Duration::from_secs(30);
                loop {
                    let status = rx.borrow().clone();
                    if status.state == RecordingState::Idle {
                        break;
                    }
                    if Instant::now() > deadline {
                        tracing::warn!("合并超时，强制退出");
                        break;
                    }
                    tokio::time::sleep(Duration::from_millis(200)).await;
                }
                tracing::info!("录制已保存，正在关闭...");
            })
            .await
            .unwrap();
    } else {
        // CLI mode
        run_cli_mode(args).await;
    }
}

