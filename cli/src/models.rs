use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct DashboardResponse {
    pub status: String,
    pub stale: bool,
    pub last_success_at: Option<String>,
    pub last_attempt_at: Option<String>,
    pub last_duration_ms: Option<u64>,
    pub age_seconds: Option<f64>,
    pub consecutive_failures: Option<u32>,
    pub poll_interval_seconds: Option<u32>,
    pub data: Option<SnapshotData>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct SnapshotData {
    pub observed_at_utc: Option<String>,
    pub meter_id: Option<i64>,
    pub meter_balance: Option<f64>,
    pub relay_status: Option<String>,
    pub eb_dg_status: Option<i32>,
    pub kwh_rec_today: Option<f64>,
    pub kvah_rec_today: Option<f64>,
    pub kwh_rec_month: Option<f64>,
    pub current_day_eb: Option<f64>,
    pub current_day_dg: Option<f64>,
    pub current_month_eb: Option<f64>,
    pub current_month_dg: Option<f64>,
    pub fixed_charges_today: Option<f64>,
    pub is_eb: Option<bool>,
    pub is_dg: Option<bool>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct HistoryResponse {
    pub range: String,
    pub count: usize,
    pub points: Vec<HistoryPoint>,
    pub transitions: Vec<TransitionEvent>,
    pub analytics: AnalyticsData,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct HistoryPoint {
    pub observed_at_utc: Option<String>,
    pub meter_balance: Option<f64>,
    pub kwh_rec_today: Option<f64>,
    pub kwh_rec_month: Option<f64>,
    pub relay_status: Option<String>,
    pub eb_dg_status: Option<i32>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct TransitionEvent {
    pub observed_at_utc: Option<String>,
    pub from_code: Option<i32>,
    pub to_code: Option<i32>,
    pub detection_window_seconds: Option<f64>,
    pub is_gap: Option<bool>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct AnalyticsData {
    pub total_snapshots: usize,
    pub grid_snapshots: usize,
    pub dg_snapshots: usize,
    pub unknown_snapshots: usize,
    pub grid_seconds: f64,
    pub dg_seconds: f64,
    pub estimated_grid_minutes: f64,
    pub estimated_dg_minutes: f64,
    pub gap_minutes: f64,
    pub telemetry_coverage_pct: f64,
    pub grid_availability_pct: f64,
    pub total_transitions: usize,
    pub balance_start: Option<f64>,
    pub balance_end: Option<f64>,
    pub balance_change: Option<f64>,
    pub kwh_start: Option<f64>,
    pub kwh_end: Option<f64>,
    pub consumption_kwh: Option<f64>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct HealthResponse {
    pub status: String,
    pub app: String,
    pub collector: CollectorInfo,
    pub poll_stats_24h: PollStats,
    pub database: DatabaseInfo,
    pub telemetry: Option<TelemetryStatus>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct CollectorInfo {
    pub role: String,
    pub running: bool,
    pub status: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct PollStats {
    pub window_hours: u32,
    pub total_attempts: u64,
    pub successes: u64,
    pub failures: u64,
    pub success_rate_pct: f64,
    pub avg_duration_ms: f64,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct DatabaseInfo {
    pub ok: bool,
    pub snapshot_count: u64,
    pub db_path: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct TelemetryStatus {
    pub status: Option<String>,
    pub age_seconds: Option<f64>,
    pub poll_interval_seconds: Option<u32>,
    pub last_success_at: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ApiError {
    pub error: Option<String>,
    pub message: Option<String>,
}
