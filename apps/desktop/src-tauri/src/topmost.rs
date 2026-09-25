use tauri::WebviewWindow;

/// Reinforce TinyScry's always-on-top window contract.
pub fn install(window: &WebviewWindow) -> tauri::Result<()> {
    window.set_always_on_top(true)?;

    #[cfg(windows)]
    windows::install(window);

    Ok(())
}

#[cfg(windows)]
mod windows {
    use raw_window_handle::{HasWindowHandle, RawWindowHandle};
    use tauri::{WebviewWindow, WindowEvent};
    use windows_sys::Win32::{
        Foundation::HWND,
        UI::{
            Shell::{DefSubclassProc, SetWindowSubclass},
            WindowsAndMessaging::{
                EnableMenuItem, GetSystemMenu, GetWindowLongPtrW, SetWindowPos, GWL_EXSTYLE,
                HWND_TOPMOST, MF_BYCOMMAND, MF_GRAYED, SC_MAXIMIZE, SC_MINIMIZE, SC_MOVE, SC_SIZE,
                SWP_ASYNCWINDOWPOS, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, WM_INITMENU,
                WM_INITMENUPOPUP, WS_EX_TOPMOST,
            },
        },
    };

    const SYSTEM_MENU_SUBCLASS_ID: usize = 0x5453_4D45;

    pub(super) fn install(window: &WebviewWindow) {
        install_system_menu_policy(window);
        enforce(window, "startup");

        let window = window.clone();
        let event_window = window.clone();
        window.on_window_event(move |event| {
            if matches!(event, WindowEvent::Focused(_)) {
                enforce(&event_window, "focus transition");
            }
        });
    }

    fn hwnd(window: &WebviewWindow) -> Option<HWND> {
        let handle = window.window_handle().ok()?;
        let RawWindowHandle::Win32(handle) = handle.as_raw() else {
            return None;
        };

        Some(handle.hwnd.get() as HWND)
    }

    fn install_system_menu_policy(window: &WebviewWindow) {
        let Some(hwnd) = hwnd(window) else {
            eprintln!("TinyScry could not obtain the native HUD window handle");
            return;
        };

        gray_system_move(hwnd);

        let installed = unsafe {
            SetWindowSubclass(
                hwnd,
                Some(system_menu_subclass_proc),
                SYSTEM_MENU_SUBCLASS_ID,
                0,
            )
        };

        if installed == 0 {
            eprintln!(
                "TinyScry could not install the HUD system-menu policy: {}",
                std::io::Error::last_os_error()
            );
        }
    }

    unsafe extern "system" fn system_menu_subclass_proc(
        hwnd: HWND,
        message: u32,
        wparam: usize,
        lparam: isize,
        _subclass_id: usize,
        _ref_data: usize,
    ) -> isize {
        let result = unsafe { DefSubclassProc(hwnd, message, wparam, lparam) };

        if matches!(message, WM_INITMENU | WM_INITMENUPOPUP) {
            gray_system_move(hwnd);
        }

        result
    }

    fn gray_system_move(hwnd: HWND) {
        let menu = unsafe { GetSystemMenu(hwnd, 0) };

        if !menu.is_null() {
            let disabled = (MF_BYCOMMAND | MF_GRAYED) as u32;

            unsafe {
                for command in [SC_MOVE, SC_SIZE, SC_MINIMIZE, SC_MAXIMIZE] {
                    EnableMenuItem(menu, command as u32, disabled);
                }
            }
        }
    }

    fn enforce(window: &WebviewWindow, reason: &str) {
        let Some(hwnd) = hwnd(window) else {
            eprintln!("TinyScry could not obtain the native HUD window handle");
            return;
        };

        let was_topmost =
            unsafe { GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST as isize != 0 };

        if !was_topmost {
            eprintln!("TinyScry HUD lost native WS_EX_TOPMOST state during {reason}; restoring it");
        }

        let result = unsafe {
            SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                (SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS) as u32,
            )
        };

        if result == 0 {
            eprintln!(
                "TinyScry could not restore native always-on-top state during {reason}: {}",
                std::io::Error::last_os_error()
            );
        }
    }
}
