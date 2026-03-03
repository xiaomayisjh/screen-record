use std::path::Path;

fn main() {
    // Declare check-cfg for embedded_ffmpeg
    println!("cargo::rustc-check-cfg=cfg(embedded_ffmpeg)");

    // Look for ffmpeg.exe to embed
    let candidates = [
        "ffmpeg.exe",                // rust/ directory
        "../ffmpeg.exe",             // project root
    ];

    for candidate in &candidates {
        let path = Path::new(candidate);
        if path.exists() {
            let abs = std::fs::canonicalize(path).unwrap();
            println!("cargo:rustc-cfg=embedded_ffmpeg");
            println!("cargo:rustc-env=FFMPEG_EMBED_PATH={}", abs.display());
            println!("cargo:rerun-if-changed={}", abs.display());
            return;
        }
    }

    println!("cargo:warning=ffmpeg.exe not found for embedding. The binary will search PATH at runtime.");
}
