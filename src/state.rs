//! Aggregated token state assembled from the stream of TokenCount events.

use crate::protocol::{RateLimitSnapshot, TokenCountEvent, TokenUsageInfo};

#[derive(Default, Debug, Clone)]
pub struct TokenState {
    pub info: Option<TokenUsageInfo>,
    pub rate_limits: Option<RateLimitSnapshot>,
    pub session_id: Option<String>,
    pub session_cwd: Option<String>,
}

impl TokenState {
    pub fn apply(&mut self, ev: &TokenCountEvent) {
        if let Some(info) = ev.info.as_ref() {
            self.info = Some(info.clone());
        }
        if let Some(rl) = ev.rate_limits.as_ref() {
            self.rate_limits = Some(rl.clone());
        }
    }

    pub fn percent_left(&self) -> Option<i64> {
        let info = self.info.as_ref()?;
        let window = info.model_context_window?;
        Some(
            info.last_token_usage
                .percent_of_context_window_remaining(window),
        )
    }

    /// Tokens currently occupying the context window (last turn's total).
    pub fn used_tokens(&self) -> Option<i64> {
        self.info
            .as_ref()
            .map(|i| i.last_token_usage.tokens_in_context_window())
    }

    /// Accumulated tokens billed across the whole session.
    pub fn session_total_tokens(&self) -> Option<i64> {
        self.info
            .as_ref()
            .map(|i| i.total_token_usage.tokens_in_context_window())
    }

    pub fn context_window(&self) -> Option<i64> {
        self.info.as_ref().and_then(|i| i.model_context_window)
    }
}
