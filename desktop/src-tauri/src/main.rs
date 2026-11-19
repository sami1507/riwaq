#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::{Duration, Instant},
};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

struct Service(Mutex<Option<Child>>);

fn stop_service(mut child: Child) {
    if matches!(child.try_wait(), Ok(Some(_))) { return; }
    // PyInstaller onefile runs a worker below its bootloader. End only the
    // process tree this desktop instance owns, never an existing service.
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        let mut command = Command::new("taskkill.exe");
        let _ = command.args(["/PID", &child.id().to_string(), "/T", "/F"])
            .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null())
            .creation_flags(0x08000000).status();
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn service_ready() -> bool {
    let addr: SocketAddr = "127.0.0.1:8765".parse().unwrap();
    let Ok(mut socket) = TcpStream::connect_timeout(&addr, Duration::from_millis(300)) else { return false; };
    let _ = socket.set_read_timeout(Some(Duration::from_secs(3)));
    if socket.write_all(b"GET /api/auth/status HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nX-Riwaq-Client: local\r\nConnection: close\r\n\r\n").is_err() { return false; }
    let mut response = String::new();
    socket.read_to_string(&mut response).is_ok()
        && response.starts_with("HTTP/1.1 200")
        && response.contains("\"paired\"")
        && response.contains("\"authenticated\"")
}

fn launch_service(app: &tauri::App) -> Result<Option<Child>, Box<dyn std::error::Error>> {
    let addr: SocketAddr = "127.0.0.1:8765".parse()?;
    // Windows on this host reports a refused loopback connection after about
    // two seconds. A shorter timeout cannot distinguish a closed port. Keep
    // refusing every outcome except the explicit ConnectionRefused result.
    match TcpStream::connect_timeout(&addr, Duration::from_millis(3000)) {
        Ok(_) => return Err("Local port 8765 is occupied. This launcher refuses to trust an existing listener. Close the separately started service explicitly.".into()),
        Err(error) if error.kind() == std::io::ErrorKind::ConnectionRefused => (),
        Err(error) => return Err(format!("Cannot verify that local port 8765 is free; launch refused. Socket kind={:?}, os_error={:?}.", error.kind(), error.raw_os_error()).into()),
    }
    let resource_dir = app.path().resource_dir()?;
    let exe_dir = std::env::current_exe()?.parent().unwrap().to_path_buf();
    let candidates = [resource_dir.join("riwaq-service.exe"), exe_dir.join("riwaq-service.exe")];
    let binary = candidates.iter().find(|p| p.is_file());
    let Some(binary) = binary else { return Err("Bundled local service is missing. Rebuild using scripts/desktop-build.ps1.".into()); };
    let mut command = Command::new(binary);
    command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command.spawn()?;
    let started = Instant::now();
    while started.elapsed() < Duration::from_secs(30) {
        if child.try_wait()?.is_some() { return Err("Riwaq local service exited before startup.".into()); }
        if service_ready() { return Ok(Some(child)); }
        std::thread::sleep(Duration::from_millis(200));
    }
    stop_service(child);
    Err("Riwaq local service did not start within the 30-second startup limit.".into())
}

fn main() {
    let shortcut = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::Space);
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new()
            .with_handler(move |app, pressed, event| {
                if pressed == &shortcut && event.state() == ShortcutState::Pressed {
                    if let Some(window) = app.get_webview_window("main") {
                        let _ = window.show();
                        let _ = window.unminimize();
                        let _ = window.set_focus();
                    }
                }
            }).build())
        .setup(move |app| {
            let (service, url) = match launch_service(app) {
                Ok(service) => (service, WebviewUrl::External("http://127.0.0.1:8765".parse()?)),
                Err(error) => {
                    if let Ok(folder) = app.path().app_log_dir() {
                        let _ = std::fs::create_dir_all(&folder);
                        let _ = std::fs::write(folder.join("startup-error.txt"), error.to_string());
                    }
                    (None, WebviewUrl::App("index.html".into()))
                }
            };
            app.manage(Service(Mutex::new(service)));
            WebviewWindowBuilder::new(app, "main", url)
                .title("رِواق · Riwaq")
                .inner_size(1380.0, 900.0)
                .min_inner_size(420.0, 580.0)
                .on_navigation(|url| (url.scheme() == "http" && url.host_str() == Some("127.0.0.1") && url.port() == Some(8765)) || url.host_str() == Some("tauri.localhost") || url.scheme() == "tauri")
                .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
                .build()?;
            if let Err(error) = app.global_shortcut().register(shortcut) {
                eprintln!("Quick-open shortcut could not be registered: {error}");
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Unable to initialize Riwaq desktop")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app.try_state::<Service>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(child) = guard.take() { stop_service(child); }
                    }
                }
            }
        });
}
