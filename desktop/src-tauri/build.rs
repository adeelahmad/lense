fn main() {
    // the update key is built into the app (src/main.rs, UPDATER_PUBKEY)
    println!("cargo:rerun-if-env-changed=LENS_UPDATER_PUBKEY");
    tauri_build::build()
}
