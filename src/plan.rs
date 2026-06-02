//! `codex-tokens plan` — query the account-level rate-limit endpoint.
//!
//! Reads the ChatGPT access token that the Codex CLI persists under
//! `~/.codex/auth.json` and calls `https://chatgpt.com/backend-api/wham/usage`.
//! The response carries the user's plan resources: rolling 5h/7d windows
//! plus per-feature additional rate limits and credits balance.
//!
//! On success the raw upstream JSON is forwarded verbatim to stdout. On
//! failure a small `{"error": "...", "code": "..."}` envelope is emitted
//! and a non-zero exit code is returned so the server can render an
//! appropriate fallback state without parsing prose.

use std::path::PathBuf;
use std::time::Duration;

use serde_json::{Value, json};

const ENDPOINT: &str = "https://chatgpt.com/backend-api/wham/usage";
const REQUEST_TIMEOUT: Duration = Duration::from_secs(10);

/// Exit codes used by the subcommand. The server distinguishes states
/// purely by exit code so it can render a precise widget status.
pub mod exit {
    pub const OK: i32 = 0;
    pub const AUTH_MISSING: i32 = 2;
    pub const TOKEN_EXPIRED: i32 = 3;
    pub const NETWORK: i32 = 4;
}

#[derive(Debug)]
enum PlanError {
    AuthMissing(String),
    TokenExpired,
    Network(String),
}

impl PlanError {
    fn code(&self) -> &'static str {
        match self {
            PlanError::AuthMissing(_) => "auth_missing",
            PlanError::TokenExpired => "token_expired",
            PlanError::Network(_) => "network",
        }
    }
    fn exit_code(&self) -> i32 {
        match self {
            PlanError::AuthMissing(_) => exit::AUTH_MISSING,
            PlanError::TokenExpired => exit::TOKEN_EXPIRED,
            PlanError::Network(_) => exit::NETWORK,
        }
    }
    fn message(&self) -> String {
        match self {
            PlanError::AuthMissing(detail) => format!("not logged in: {detail}"),
            PlanError::TokenExpired => "access token rejected — run `codex login` to refresh".into(),
            PlanError::Network(detail) => format!("upstream call failed: {detail}"),
        }
    }
}

/// Entry point for `codex-tokens plan`. Returns the process exit code.
pub fn run(codex_home: Option<PathBuf>) -> i32 {
    let home = match codex_home.or_else(|| crate::locate::codex_home().ok()) {
        Some(p) => p,
        None => return emit_error(&PlanError::AuthMissing("CODEX_HOME not resolvable".into())),
    };

    let token = match read_access_token(&home) {
        Ok(t) => t,
        Err(e) => return emit_error(&e),
    };

    match fetch_usage(&token) {
        Ok(value) => {
            // Forward the upstream JSON verbatim. The server cares about the
            // exact shape (`rate_limit`, `additional_rate_limits`, …) and the
            // UI handles unknown extra fields gracefully.
            println!("{}", value);
            exit::OK
        }
        Err(e) => emit_error(&e),
    }
}

fn emit_error(err: &PlanError) -> i32 {
    let envelope = json!({ "error": err.message(), "code": err.code() });
    println!("{}", envelope);
    err.exit_code()
}

fn read_access_token(codex_home: &std::path::Path) -> Result<String, PlanError> {
    let auth_path = codex_home.join("auth.json");
    let raw = std::fs::read_to_string(&auth_path)
        .map_err(|e| PlanError::AuthMissing(format!("{}: {e}", auth_path.display())))?;
    let parsed: Value = serde_json::from_str(&raw)
        .map_err(|e| PlanError::AuthMissing(format!("auth.json malformed: {e}")))?;
    let token = parsed
        .get("tokens")
        .and_then(|t| t.get("access_token"))
        .and_then(|v| v.as_str())
        .ok_or_else(|| PlanError::AuthMissing("tokens.access_token missing".into()))?;
    Ok(token.to_string())
}

fn fetch_usage(access_token: &str) -> Result<Value, PlanError> {
    let agent = ureq::AgentBuilder::new()
        .timeout(REQUEST_TIMEOUT)
        .user_agent(concat!("codex-token-monitor/", env!("CARGO_PKG_VERSION")))
        .build();

    let resp = agent
        .get(ENDPOINT)
        .set("Authorization", &format!("Bearer {access_token}"))
        .set("Accept", "application/json")
        .call();

    match resp {
        Ok(r) => r
            .into_json::<Value>()
            .map_err(|e| PlanError::Network(format!("response body parse: {e}"))),
        Err(ureq::Error::Status(401, _)) | Err(ureq::Error::Status(403, _)) => {
            Err(PlanError::TokenExpired)
        }
        Err(ureq::Error::Status(code, resp)) => {
            let body = resp.into_string().unwrap_or_default();
            Err(PlanError::Network(format!(
                "HTTP {code}: {}",
                body.chars().take(200).collect::<String>()
            )))
        }
        Err(ureq::Error::Transport(t)) => Err(PlanError::Network(t.to_string())),
    }
}
