//! Stdout rendering for the token state. Two formats: `kv` (KEY=VALUE, default,
//! shell-friendly) and `json` (single JSON object per snapshot).

use serde_json::json;

use crate::state::TokenState;

pub enum Format {
    Kv,
    Json,
}

pub fn render(state: &TokenState, format: &Format, multi: bool) -> String {
    match format {
        Format::Kv => render_kv(state, multi),
        Format::Json => render_json(state),
    }
}

fn render_kv(s: &TokenState, multi: bool) -> String {
    let mut out = String::new();

    // In multi-session mode prepend a clearly visible block header so a human
    // reading the stream can spot session boundaries at a glance. Bash scripts
    // can keep using the `session_id=...` line below as the maschinenlesbarer
    // anchor.
    if multi && let Some(id) = &s.session_id {
        out.push_str("=== session ");
        out.push_str(id);
        out.push_str(" ===\n");
    }

    if let Some(id) = &s.session_id {
        push_kv(&mut out, "session_id", id);
    }
    if let Some(cwd) = &s.session_cwd {
        push_kv(&mut out, "session_cwd", cwd);
    }
    if let Some(active) = s.session_active {
        push_kv(&mut out, "session_active", if active { "true" } else { "false" });
    }

    if let Some(info) = &s.info {
        if let Some(w) = info.model_context_window {
            push_kv(&mut out, "context_window", &w.to_string());
        }
        if let Some(p) = s.percent_left() {
            push_kv(&mut out, "percent_left", &p.to_string());
            push_kv(&mut out, "percent_used", &(100 - p).to_string());
        }
        if let Some(u) = s.used_tokens() {
            push_kv(&mut out, "tokens_in_context", &u.to_string());
        }
        if let Some(u) = s.session_total_tokens() {
            push_kv(&mut out, "session_total_tokens", &u.to_string());
        }
        push_kv(
            &mut out,
            "total_input_tokens",
            &info.total_token_usage.input_tokens.to_string(),
        );
        push_kv(
            &mut out,
            "total_cached_input_tokens",
            &info.total_token_usage.cached_input_tokens.to_string(),
        );
        push_kv(
            &mut out,
            "total_output_tokens",
            &info.total_token_usage.output_tokens.to_string(),
        );
        push_kv(
            &mut out,
            "total_reasoning_output_tokens",
            &info.total_token_usage.reasoning_output_tokens.to_string(),
        );
        push_kv(
            &mut out,
            "total_tokens",
            &info.total_token_usage.total_tokens.to_string(),
        );
        push_kv(
            &mut out,
            "total_blended",
            &info.total_token_usage.blended_total().to_string(),
        );

        push_kv(
            &mut out,
            "last_input_tokens",
            &info.last_token_usage.input_tokens.to_string(),
        );
        push_kv(
            &mut out,
            "last_cached_input_tokens",
            &info.last_token_usage.cached_input_tokens.to_string(),
        );
        push_kv(
            &mut out,
            "last_output_tokens",
            &info.last_token_usage.output_tokens.to_string(),
        );
        push_kv(
            &mut out,
            "last_reasoning_output_tokens",
            &info.last_token_usage.reasoning_output_tokens.to_string(),
        );
        push_kv(
            &mut out,
            "last_total_tokens",
            &info.last_token_usage.total_tokens.to_string(),
        );
        push_kv(
            &mut out,
            "last_blended",
            &info.last_token_usage.blended_total().to_string(),
        );
    }

    if let Some(rl) = &s.rate_limits {
        if let Some(id) = &rl.limit_id {
            push_kv(&mut out, "rate_limit_id", id);
        }
        if let Some(plan) = &rl.plan_type {
            push_kv(&mut out, "plan_type", plan);
        }
        if let Some(p) = &rl.primary {
            push_kv(&mut out, "primary_used_percent", &format!("{:.1}", p.used_percent));
            if let Some(w) = p.window_minutes {
                push_kv(&mut out, "primary_window_minutes", &w.to_string());
            }
            if let Some(r) = p.resets_at {
                push_kv(&mut out, "primary_resets_at", &r.to_string());
            }
        }
        if let Some(s2) = &rl.secondary {
            push_kv(&mut out, "secondary_used_percent", &format!("{:.1}", s2.used_percent));
            if let Some(w) = s2.window_minutes {
                push_kv(&mut out, "secondary_window_minutes", &w.to_string());
            }
            if let Some(r) = s2.resets_at {
                push_kv(&mut out, "secondary_resets_at", &r.to_string());
            }
        }
    }

    out
}

fn push_kv(out: &mut String, key: &str, value: &str) {
    out.push_str(key);
    out.push('=');
    out.push_str(value);
    out.push('\n');
}

fn render_json(s: &TokenState) -> String {
    let v = json!({
        "session_id": s.session_id,
        "session_cwd": s.session_cwd,
        "session_active": s.session_active,
        "context_window": s.context_window(),
        "percent_left": s.percent_left(),
        "percent_used": s.percent_left().map(|p| 100 - p),
        "tokens_in_context": s.used_tokens(),
        "session_total_tokens": s.session_total_tokens(),
        "total": s.info.as_ref().map(|i| json!({
            "input_tokens": i.total_token_usage.input_tokens,
            "cached_input_tokens": i.total_token_usage.cached_input_tokens,
            "output_tokens": i.total_token_usage.output_tokens,
            "reasoning_output_tokens": i.total_token_usage.reasoning_output_tokens,
            "total_tokens": i.total_token_usage.total_tokens,
            "blended": i.total_token_usage.blended_total(),
        })),
        "last": s.info.as_ref().map(|i| json!({
            "input_tokens": i.last_token_usage.input_tokens,
            "cached_input_tokens": i.last_token_usage.cached_input_tokens,
            "output_tokens": i.last_token_usage.output_tokens,
            "reasoning_output_tokens": i.last_token_usage.reasoning_output_tokens,
            "total_tokens": i.last_token_usage.total_tokens,
            "blended": i.last_token_usage.blended_total(),
        })),
        "rate_limits": s.rate_limits.as_ref().map(|rl| json!({
            "limit_id": rl.limit_id,
            "limit_name": rl.limit_name,
            "plan_type": rl.plan_type,
            "primary": rl.primary.as_ref().map(|p| json!({
                "used_percent": p.used_percent,
                "window_minutes": p.window_minutes,
                "resets_at": p.resets_at,
            })),
            "secondary": rl.secondary.as_ref().map(|p| json!({
                "used_percent": p.used_percent,
                "window_minutes": p.window_minutes,
                "resets_at": p.resets_at,
            })),
        })),
    });
    serde_json::to_string(&v).unwrap_or_default()
}
