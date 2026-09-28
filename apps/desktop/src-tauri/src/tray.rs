use std::sync::atomic::{AtomicBool, Ordering};

use tauri::{
    menu::{CheckMenuItemBuilder, MenuBuilder, MenuItemBuilder},
    tray::TrayIconBuilder,
    Manager,
};

#[derive(Default)]
pub struct AlertMuteState(AtomicBool);

impl AlertMuteState {
    pub fn is_muted(&self) -> bool {
        self.0.load(Ordering::Relaxed)
    }

    fn set_muted(&self, muted: bool) {
        self.0.store(muted, Ordering::Relaxed);
    }
}

pub fn install(app: &tauri::App) -> tauri::Result<()> {
    let mute = CheckMenuItemBuilder::with_id("mute-alerts", "Mute alerts")
        .checked(false)
        .build(app)?;

    let quit = MenuItemBuilder::with_id("quit", "Quit Imp").build(app)?;

    let menu = MenuBuilder::new(app)
        .item(&mute)
        .separator()
        .item(&quit)
        .build()?;

    let mute_for_event = mute.clone();

    let mut tray = TrayIconBuilder::with_id("imp")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .tooltip("Imp")
        .on_menu_event(move |app, event| match event.id().as_ref() {
            "mute-alerts" => {
                let muted = mute_for_event.is_checked().unwrap_or(false);
                app.state::<AlertMuteState>().set_muted(muted);
            }
            "quit" => app.exit(0),
            _ => {}
        });

    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }

    tray.build(app)?;
    Ok(())
}
