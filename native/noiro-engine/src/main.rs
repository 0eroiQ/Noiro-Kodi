use serde_json::{json, Value};
use std::env;
use std::ffi::{c_void, CStr, CString};
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::thread;

const PROTOCOL_VERSION: u64 = 1;

extern "C" fn core_event(_ctx: *mut c_void, _data: *const u8, _len: usize) {}

struct Config {
    socket: PathBuf,
    storage: PathBuf,
}

fn config() -> Result<Config, String> {
    let mut args = env::args().skip(1);
    let mut socket = None;
    let mut storage = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--socket" => socket = args.next().map(PathBuf::from),
            "--storage" => storage = args.next().map(PathBuf::from),
            _ => return Err(format!("unknown argument: {arg}")),
        }
    }
    Ok(Config {
        socket: socket.ok_or_else(|| "--socket is required".to_owned())?,
        storage: storage.ok_or_else(|| "--storage is required".to_owned())?,
    })
}

fn init_core(storage: &Path) -> Result<(), String> {
    let cache = storage.join("cache");
    let buckets = storage.join("buckets");
    fs::create_dir_all(&cache).map_err(|error| error.to_string())?;
    fs::create_dir_all(&buckets).map_err(|error| error.to_string())?;
    let storage_c = CString::new(buckets.to_string_lossy().as_bytes()).map_err(|error| error.to_string())?;
    let cache_c = CString::new(cache.to_string_lossy().as_bytes()).map_err(|error| error.to_string())?;
    let ready = stremiox_core::stremiox_core_init(
        storage_c.as_ptr(),
        cache_c.as_ptr(),
        std::ptr::null_mut(),
        core_event,
    );
    if ready { Ok(()) } else { Err("stremio core initialization failed".to_owned()) }
}

fn core_dispatch(params: &Value) -> Result<Value, String> {
    let action = params.get("action").ok_or_else(|| "action is required".to_owned())?;
    let payload = if action.is_string() {
        action.as_str().unwrap_or_default().to_owned()
    } else {
        serde_json::to_string(action).map_err(|error| error.to_string())?
    };
    let value = CString::new(payload).map_err(|error| error.to_string())?;
    stremiox_core::stremiox_core_dispatch(value.as_ptr());
    Ok(json!({"accepted": true}))
}

fn core_state(params: &Value) -> Result<Value, String> {
    let field = params.get("field").and_then(Value::as_str).ok_or_else(|| "field is required".to_owned())?;
    let encoded = serde_json::to_string(field).map_err(|error| error.to_string())?;
    let value = CString::new(encoded).map_err(|error| error.to_string())?;
    let pointer = stremiox_core::stremiox_core_get_state(value.as_ptr());
    if pointer.is_null() {
        return Err("core returned a null state pointer".to_owned());
    }
    let text = unsafe { CStr::from_ptr(pointer) }.to_string_lossy().into_owned();
    stremiox_core::stremiox_core_string_free(pointer);
    serde_json::from_str(&text).map_err(|error| format!("invalid core state: {error}"))
}

fn dispatch(method: &str, params: &Value) -> Result<Value, String> {
    match method {
        "system.health" => Ok(json!({
            "ready": true,
            "protocol": PROTOCOL_VERSION,
            "engine": env!("CARGO_PKG_VERSION"),
            "stremio_schema": stremiox_core::stremiox_core_schema_version(),
            "target": env::consts::ARCH,
        })),
        "engine.dispatch" => core_dispatch(params),
        "engine.state" => core_state(params),
        _ => Err(format!("unknown method: {method}")),
    }
}

fn serve_client(mut stream: UnixStream) -> Result<(), String> {
    let clone = stream.try_clone().map_err(|error| error.to_string())?;
    let mut reader = BufReader::new(clone);
    let mut line = String::new();
    reader.read_line(&mut line).map_err(|error| error.to_string())?;
    let request: Value = serde_json::from_str(&line).map_err(|error| error.to_string())?;
    let id = request.get("id").cloned().unwrap_or(Value::Null);
    let response = match request.get("method").and_then(Value::as_str) {
        Some(method) => match dispatch(method, request.get("params").unwrap_or(&Value::Null)) {
            Ok(result) => json!({"jsonrpc":"2.0", "id":id, "result":result}),
            Err(message) => json!({"jsonrpc":"2.0", "id":id, "error":{"code":-32000,"message":message}}),
        },
        None => json!({"jsonrpc":"2.0", "id":id, "error":{"code":-32600,"message":"invalid request"}}),
    };
    let encoded = serde_json::to_vec(&response).map_err(|error| error.to_string())?;
    stream.write_all(&encoded).map_err(|error| error.to_string())?;
    stream.write_all(b"\n").map_err(|error| error.to_string())?;
    Ok(())
}

fn run() -> Result<(), String> {
    let config = config()?;
    fs::create_dir_all(&config.storage).map_err(|error| error.to_string())?;
    init_core(&config.storage)?;
    if let Some(parent) = config.socket.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    if config.socket.exists() {
        fs::remove_file(&config.socket).map_err(|error| error.to_string())?;
    }
    let listener = UnixListener::bind(&config.socket).map_err(|error| error.to_string())?;
    fs::set_permissions(&config.socket, fs::Permissions::from_mode(0o600)).map_err(|error| error.to_string())?;
    for stream in listener.incoming() {
        match stream {
            Ok(stream) => {
                thread::spawn(move || {
                    let _ = serve_client(stream);
                });
            }
            Err(error) => return Err(error.to_string()),
        }
    }
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("noiro-engine: {error}");
        std::process::exit(1);
    }
}
