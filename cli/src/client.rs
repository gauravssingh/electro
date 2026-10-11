use std::time::Duration;
use crate::config::Config;
use crate::models::{ApiError, DashboardResponse, HealthResponse, HistoryResponse};

pub struct ElectroClient {
    config: Config,
    agent: ureq::Agent,
}

impl ElectroClient {
    pub fn new(config: Config) -> Self {
        let timeout = Duration::from_millis(config.timeout_ms);
        let agent = ureq::builder()
            .timeout(timeout)
            .build();

        Self { config, agent }
    }

    fn prepare_request(&self, path: &str) -> ureq::Request {
        let url = format!("{}{}", self.config.endpoint, path);
        let mut req = self.agent.get(&url);
        if let Some(token) = &self.config.token {
            req = req.set("Authorization", &format!("Bearer {}", token));
        }
        req
    }

    fn handle_error(&self, err: ureq::Error) -> String {
        match err {
            ureq::Error::Status(401, resp) => {
                let body: Option<ApiError> = resp.into_json().ok();
                let msg = body.and_then(|b| b.message).unwrap_or_else(|| "Unauthorized: Valid API token required".to_string());
                format!("Authentication error (HTTP 401): {}\nRun `electro init --token <TOKEN>` to configure a valid access token.", msg)
            }
            ureq::Error::Status(status, resp) => {
                let body: Option<ApiError> = resp.into_json().ok();
                let msg = body.and_then(|b| b.message).unwrap_or_else(|| format!("HTTP status {}", status));
                format!("Server error (HTTP {}): {}", status, msg)
            }
            ureq::Error::Transport(transport) => {
                format!(
                    "Connection failed: Could not reach Electro at {}.\nDetails: {}\nIs the Electro daemon running? Check `com.gauravs.electro` or verify the endpoint.",
                    self.config.endpoint, transport
                )
            }
        }
    }

    pub fn get_health(&self) -> Result<HealthResponse, String> {
        let req = self.prepare_request("/api/health");
        let resp = req.call().map_err(|e| self.handle_error(e))?;
        resp.into_json::<HealthResponse>()
            .map_err(|e| format!("Failed to parse health response: {}", e))
    }

    pub fn get_dashboard(&self) -> Result<DashboardResponse, String> {
        let req = self.prepare_request("/api/dashboard");
        let resp = req.call().map_err(|e| self.handle_error(e))?;
        resp.into_json::<DashboardResponse>()
            .map_err(|e| format!("Failed to parse dashboard response: {}", e))
    }

    pub fn get_history(&self, range: &str) -> Result<HistoryResponse, String> {
        let path = format!("/api/history?range={}", range);
        let req = self.prepare_request(&path);
        let resp = req.call().map_err(|e| self.handle_error(e))?;
        resp.into_json::<HistoryResponse>()
            .map_err(|e| format!("Failed to parse history response: {}", e))
    }

    pub fn export_csv(&self, range: &str) -> Result<String, String> {
        let path = format!("/api/export/csv?range={}", range);
        let req = self.prepare_request(&path);
        let resp = req.call().map_err(|e| self.handle_error(e))?;
        resp.into_string()
            .map_err(|e| format!("Failed to read CSV response: {}", e))
    }

    pub fn refresh(&self) -> Result<serde_json::Value, String> {
        let url = format!("{}/api/refresh", self.config.endpoint);
        let mut req = self.agent.post(&url);
        if let Some(token) = &self.config.token {
            req = req.set("Authorization", &format!("Bearer {}", token));
        }
        let resp = req.call().map_err(|e| self.handle_error(e))?;
        resp.into_json::<serde_json::Value>()
            .map_err(|e| format!("Failed to parse refresh response: {}", e))
    }
}
