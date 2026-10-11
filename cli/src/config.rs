use std::fs;
use std::path::PathBuf;
use serde::{Deserialize, Serialize};

fn default_endpoint() -> String {
    "http://127.0.0.1:5000".to_string()
}

fn default_timeout() -> u64 {
    5000
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    #[serde(default = "default_endpoint")]
    pub endpoint: String,
    pub token: Option<String>,
    #[serde(default = "default_timeout")]
    pub timeout_ms: u64,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            endpoint: default_endpoint(),
            token: None,
            timeout_ms: default_timeout(),
        }
    }
}

impl Config {
    /// Return the standard XDG configuration file path (~/.config/electro/config.toml).
    pub fn config_path() -> Option<PathBuf> {
        dirs::config_dir().map(|dir| dir.join("electro").join("config.toml"))
    }

    /// Load configuration by merging file settings, environment variables, and CLI overrides.
    pub fn load(cli_endpoint: Option<String>, cli_token: Option<String>) -> Self {
        let mut config = Self::load_from_file().unwrap_or_default();

        // 1. Environment variables override config file
        if let Ok(env_ep) = std::env::var("ELECTRO_ENDPOINT") {
            let trimmed = env_ep.trim();
            if !trimmed.is_empty() {
                config.endpoint = trimmed.to_string();
            }
        }
        if let Ok(env_tok) = std::env::var("ELECTRO_TOKEN") {
            let trimmed = env_tok.trim();
            if !trimmed.is_empty() {
                config.token = Some(trimmed.to_string());
            }
        }

        // 2. Explicit CLI flags have highest precedence
        if let Some(ep) = cli_endpoint {
            let trimmed = ep.trim();
            if !trimmed.is_empty() {
                config.endpoint = trimmed.to_string();
            }
        }
        if let Some(tok) = cli_token {
            let trimmed = tok.trim();
            if !trimmed.is_empty() {
                config.token = Some(trimmed.to_string());
            }
        }

        // Normalize endpoint: remove trailing slash
        config.endpoint = config.endpoint.trim_end_matches('/').to_string();
        config
    }

    /// Load raw settings from ~/.config/electro/config.toml if it exists.
    pub fn load_from_file() -> Result<Self, String> {
        let path = Self::config_path().ok_or_else(|| "Could not determine config directory".to_string())?;
        if !path.exists() {
            return Ok(Self::default());
        }

        let contents = fs::read_to_string(&path)
            .map_err(|e| format!("Failed to read {}: {}", path.display(), e))?;

        toml::from_str(&contents)
            .map_err(|e| format!("Failed to parse {}: {}", path.display(), e))
    }

    /// Save configuration to ~/.config/electro/config.toml with 0600 permissions.
    pub fn save(&self) -> Result<PathBuf, String> {
        let path = Self::config_path().ok_or_else(|| "Could not determine config directory".to_string())?;
        
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .map_err(|e| format!("Failed to create directory {}: {}", parent.display(), e))?;
        }

        let toml_str = toml::to_string_pretty(self)
            .map_err(|e| format!("Failed to serialize config to TOML: {}", e))?;

        fs::write(&path, toml_str)
            .map_err(|e| format!("Failed to write {}: {}", path.display(), e))?;

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(&path, fs::Permissions::from_mode(0o600));
        }

        Ok(path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_config() {
        let cfg = Config::default();
        assert_eq!(cfg.endpoint, "http://127.0.0.1:5000");
        assert_eq!(cfg.token, None);
        assert_eq!(cfg.timeout_ms, 5000);
    }

    #[test]
    fn test_load_overrides() {
        let cfg = Config::load(Some("http://raspberrypi.local:5000/".to_string()), Some("secret123".to_string()));
        assert_eq!(cfg.endpoint, "http://raspberrypi.local:5000");
        assert_eq!(cfg.token, Some("secret123".to_string()));
    }
}
