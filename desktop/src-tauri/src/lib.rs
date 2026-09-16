pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    #[test]
    fn test_tauri_context_generates() {
        let _context: tauri::Context<tauri::Wry> = tauri::generate_context!();
    }
}
