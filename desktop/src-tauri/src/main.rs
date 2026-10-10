// Lens for macOS: a menu-bar app around Lens (desktop/README.md).
//
// The work is done by resources/lens-app.sh, which install.sh's own steps already cover: on a first launch it runs
// the web installer, which this app shows in its window; after that it starts Lens and the app opens it in the
// browser, since Lens signs in with passkeys and an app's web view can't use them. The menu-bar item starts, stops,
// opens and updates Lens. Closing the window leaves the app in the menu bar.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::path::BaseDirectory;
use tauri::tray::TrayIconBuilder;
use tauri::{
    AppHandle, Manager, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent,
};

/// The app's own page, as the web view addresses it.
#[cfg(any(target_os = "macos", target_os = "ios"))]
const APP_PAGE: &str = "tauri://localhost/index.html";
#[cfg(not(any(target_os = "macos", target_os = "ios")))]
const APP_PAGE: &str = "http://tauri.localhost/index.html";

/// The page the window may show besides its own: the web installer, while it runs.
#[derive(Default)]
struct Shown(Arc<Mutex<Option<String>>>);

fn helper(app: &AppHandle) -> PathBuf {
    app.path()
        .resolve("lens-app.sh", BaseDirectory::Resource)
        .unwrap_or_else(|_| {
            PathBuf::from(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/../resources/lens-app.sh"
            ))
        })
}

fn msb_bundle(app: &AppHandle) -> Option<PathBuf> {
    app.path()
        .resolve(
            "microsandbox-darwin-aarch64.tar.gz",
            BaseDirectory::Resource,
        )
        .ok()
        .filter(|p| p.exists())
}

fn window(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window("main")
}

fn show_window(app: &AppHandle) {
    if let Some(w) = window(app) {
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn set_status(app: &AppHandle, text: &str, error: bool) {
    if let Some(w) = window(app) {
        let js = format!(
            "window.setStatus && window.setStatus({}, {})",
            serde_json::to_string(text).unwrap_or_default(),
            error
        );
        let _ = w.eval(&js);
    }
}

/// Opens a page in the default browser.
fn open_in_browser(url: &str) {
    #[cfg(target_os = "macos")]
    let opener = "open";
    #[cfg(not(target_os = "macos"))]
    let opener = "xdg-open";
    let _ = Command::new(opener).arg(url).spawn();
}

/// Runs lens-app.sh with a command, and does what its output says (the lines are listed at its top).
fn run(app: &AppHandle, command: &'static str) {
    let app = app.clone();
    std::thread::spawn(move || {
        let mut cmd = Command::new("/bin/sh");
        cmd.arg(helper(&app))
            .arg(command)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        if let Some(bundle) = msb_bundle(&app) {
            cmd.env("LENS_MSB_BUNDLE", bundle);
        }
        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                set_status(&app, &format!("Couldn't run Lens's helper: {e}"), true);
                show_window(&app);
                return;
            }
        };
        let out = child.stdout.take().expect("piped");
        for line in BufReader::new(out).lines().map_while(Result::ok) {
            let (tag, rest) = line.split_once(' ').unwrap_or((line.as_str(), ""));
            match tag {
                "@status" => set_status(&app, rest, false),
                "@error" => {
                    set_status(&app, rest, true);
                    show_window(&app);
                }
                "@show" => {
                    if let (Some(w), Ok(url)) = (window(&app), Url::parse(rest)) {
                        *app.state::<Shown>().0.lock().unwrap() = Some(origin(&url));
                        let _ = w.navigate(url);
                        show_window(&app);
                    }
                }
                "@open" => {
                    open_in_browser(rest);
                    if let Some(w) = window(&app) {
                        set_status(
                            &app,
                            "Lens is running. It opens in your browser; it stays in the menu bar.",
                            false,
                        );
                        let _ = w.hide();
                    }
                }
                _ => {}
            }
        }
        let _ = child.wait();
    });
}

fn origin(url: &Url) -> String {
    url.origin().ascii_serialization()
}

fn main() {
    let shown = Shown::default();
    let allowed = shown.0.clone();
    tauri::Builder::default()
        .manage(shown)
        .setup(move |app| {
            let handle = app.handle().clone();
            // The window: the app's status page, and the web installer on a first launch. Anything else (Lens
            // itself, once the installer's Open Lens goes there) opens in the browser.
            let nav_handle = handle.clone();
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Lens")
                .inner_size(980.0, 760.0)
                .min_inner_size(520.0, 420.0)
                .on_navigation(move |url| {
                    if matches!(url.scheme(), "tauri" | "asset")
                        || url.host_str() == Some("tauri.localhost")
                    {
                        return true;
                    }
                    if allowed.lock().unwrap().as_deref() == Some(origin(url).as_str()) {
                        return true;
                    }
                    if matches!(url.scheme(), "http" | "https") {
                        // Lens itself (the installer's Open Lens): lens-app.sh opens it in the browser once the
                        // installer has stopped; anything else opens there now
                        if !matches!(url.host_str(), Some("localhost" | "127.0.0.1")) {
                            open_in_browser(url.as_str());
                            return false;
                        }
                        // the installer is finished with: Lens runs from here on
                        *allowed.lock().unwrap() = None;
                        let h = nav_handle.clone();
                        let _ = nav_handle.run_on_main_thread(move || {
                            if let Some(w) = window(&h) {
                                let _ = w.navigate(APP_PAGE.parse().unwrap());
                                let _ = w.hide();
                            }
                        });
                    }
                    false
                })
                .build()?;

            let open = MenuItem::with_id(app, "open", "Open Lens", true, None::<&str>)?;
            let start = MenuItem::with_id(app, "start", "Start Lens", true, None::<&str>)?;
            let stop = MenuItem::with_id(app, "stop", "Stop Lens", true, None::<&str>)?;
            let update = MenuItem::with_id(app, "update", "Update Lens", true, None::<&str>)?;
            let window_item = MenuItem::with_id(app, "window", "Show Window", true, None::<&str>)?;
            let logs = MenuItem::with_id(app, "logs", "Show Logs", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let sep = || PredefinedMenuItem::separator(app);
            let menu = Menu::with_items(
                app,
                &[
                    &open,
                    &sep()?,
                    &start,
                    &stop,
                    &update,
                    &sep()?,
                    &window_item,
                    &logs,
                    &sep()?,
                    &quit,
                ],
            )?;
            let mut tray = TrayIconBuilder::with_id("lens")
                .tooltip("Lens")
                .menu(&menu)
                .show_menu_on_left_click(true);
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.on_menu_event(|app, event| match event.id().as_ref() {
                "open" | "start" => run(app, "start"),
                "stop" => run(app, "stop"),
                "update" => {
                    show_window(app);
                    run(app, "update");
                }
                "window" => show_window(app),
                "logs" =>
                {
                    #[cfg(target_os = "macos")]
                    if let Some(home) = std::env::var_os("HOME") {
                        let _ = Command::new("open")
                            .arg(PathBuf::from(home).join("Library/Logs/Lens"))
                            .spawn();
                    }
                }
                "quit" => app.exit(0),
                _ => {}
            })
            .build(app)?;

            run(&handle, "launch");
            Ok(())
        })
        .on_window_event(|window, event| {
            // closing the window leaves Lens in the menu bar
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("couldn't start Lens")
        .run(|app, event| {
            // clicking the Dock icon brings the window back
            #[cfg(target_os = "macos")]
            if let tauri::RunEvent::Reopen { .. } = event {
                show_window(app);
            }
            let _ = (app, event);
        });
}
