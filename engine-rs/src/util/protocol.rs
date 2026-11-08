//! stdout protocol shared with the add-on: one flushed line per message.
//!   P <percent> <message>   progress
//!   R <json>                repair report
//!   S <json>                statistics of the result
//!   OK                      finished
//!   ERR <message>           failure (exit code 2)
use std::io::Write;

pub fn say(tag: &str, text: &str) {
    let out = std::io::stdout();
    let mut lock = out.lock();
    let _ = writeln!(lock, "{tag} {text}");
    let _ = lock.flush();
}

pub fn progress(percent: i32, message: &str) { say("P", &format!("{percent} {message}")); }

/// Replaces characters that would break the one-line JSON/message format.
pub fn json_safe(text: &str) -> String {
    text.chars().map(|c| if c == '"' || c == '\\' || (c as u32) < 32 { ' ' } else { c }).collect()
}
