mod client;
mod config;
mod models;

use clap::{Args, Parser, Subcommand};
use client::ElectroClient;
use config::Config;
use serde_json::json;

#[derive(Parser)]
#[command(
    name = "electro",
    about = "⚡ Fast CLI & LLM tool for Electro prepaid electricity meter telemetry",
    version,
    long_about = "Electro CLI provides instantaneous terminal inspection and machine-readable JSON for LLMs\nand autonomous agents to query power sources (Grid vs DG), live balances, outages, and consumption."
)]
struct Cli {
    /// Remote or local endpoint where Electro is running (e.g. http://127.0.0.1:5000 or http://pi.local:5000)
    #[arg(short, long, global = true, env = "ELECTRO_ENDPOINT")]
    endpoint: Option<String>,

    /// API access token for authentication (matches ELECTRO_API_TOKEN on server)
    #[arg(short, long, global = true, env = "ELECTRO_TOKEN")]
    token: Option<String>,

    /// Force machine-readable JSON output (ideal for LLMs and script pipelines)
    #[arg(long, global = true)]
    json: bool,

    #[command(subcommand)]
    command: Option<Commands>,
}

#[derive(Subcommand)]
enum Commands {
    /// Initialize or reconfigure the standalone CLI endpoint and access token
    Init(InitArgs),

    /// Display current power source (Grid vs DG), balance, relay state, and telemetry age
    #[command(alias = "current")]
    Status(RangeOnlyArgs),

    /// Display period summary: net consumption (kWh), spend (₹), and Grid vs DG runtimes
    Summary(RangeOnlyArgs),

    /// List power outage and Grid <-> DG generator transition history
    Transitions(TransitionsArgs),

    /// Check daemon health, collector liveness, and 24h polling reliability
    Health,

    /// Trigger an immediate on-demand meter reading refresh
    Refresh,

    /// Export historical telemetry snapshots in standard CSV format
    Export(ExportArgs),
}

#[derive(Args)]
struct InitArgs {
    /// Electro server endpoint URL (e.g. http://127.0.0.1:5000 or http://raspberrypi.local:5000)
    #[arg(long, default_value = "http://127.0.0.1:5000")]
    endpoint: String,

    /// Optional API access token for authentication
    #[arg(long)]
    token: Option<String>,
}

#[derive(Args)]
struct RangeOnlyArgs {
    /// Time range to inspect (24h, 7d, 30d, all)
    #[arg(short, long, default_value = "24h")]
    range: String,
}

#[derive(Args)]
struct TransitionsArgs {
    /// Time range to inspect (24h, 7d, 30d, all)
    #[arg(short, long, default_value = "24h")]
    range: String,

    /// Maximum number of transitions to display
    #[arg(short, long, default_value_t = 20)]
    limit: usize,
}

#[derive(Args)]
struct ExportArgs {
    /// Time range to export (24h, 7d, 30d, all)
    #[arg(short, long, default_value = "24h")]
    range: String,

    /// Destination file path (prints to stdout if omitted)
    #[arg(short, long)]
    output: Option<String>,
}

fn format_duration_mins(mins: f64) -> String {
    if mins <= 0.0 {
        return "0m".to_string();
    }
    let total_secs = (mins * 60.0).round() as u64;
    let hours = total_secs / 3600;
    let minutes = (total_secs % 3600) / 60;
    if hours > 0 {
        format!("{}h {}m", hours, minutes)
    } else {
        format!("{}m", minutes)
    }
}

fn main() {
    let cli = Cli::parse();
    let as_json = cli.json;

    // Default to 'status' if no subcommand provided
    let command = cli.command.unwrap_or(Commands::Status(RangeOnlyArgs {
        range: "24h".to_string(),
    }));

    match command {
        Commands::Init(args) => handle_init(args, as_json),
        Commands::Status(_) => {
            let config = Config::load(cli.endpoint, cli.token);
            let client = ElectroClient::new(config);
            handle_status(&client, as_json);
        }
        Commands::Summary(args) => {
            let config = Config::load(cli.endpoint, cli.token);
            let client = ElectroClient::new(config);
            handle_summary(&client, &args.range, as_json);
        }
        Commands::Transitions(args) => {
            let config = Config::load(cli.endpoint, cli.token);
            let client = ElectroClient::new(config);
            handle_transitions(&client, &args.range, args.limit, as_json);
        }
        Commands::Health => {
            let config = Config::load(cli.endpoint, cli.token);
            let client = ElectroClient::new(config);
            handle_health(&client, as_json);
        }
        Commands::Refresh => {
            let config = Config::load(cli.endpoint, cli.token);
            let client = ElectroClient::new(config);
            handle_refresh(&client, as_json);
        }
        Commands::Export(args) => {
            let config = Config::load(cli.endpoint, cli.token);
            let client = ElectroClient::new(config);
            handle_export(&client, &args.range, args.output.as_deref());
        }
    }
}

fn handle_init(args: InitArgs, as_json: bool) {
    let endpoint = args.endpoint.trim_end_matches('/').to_string();
    let token = args.token.filter(|t| !t.trim().is_empty());

    let test_config = Config {
        endpoint: endpoint.clone(),
        token: token.clone(),
        timeout_ms: 5000,
    };
    let client = ElectroClient::new(test_config.clone());

    // 1. Validate connection & authentication against /api/health
    match client.get_health() {
        Ok(health) => {
            // 2. Persist to ~/.config/electro/config.toml
            match test_config.save() {
                Ok(path) => {
                    if as_json {
                        println!(
                            "{}",
                            json!({
                                "status": "ok",
                                "endpoint": endpoint,
                                "authenticated": token.is_some(),
                                "config_file": path.display().to_string(),
                                "server_status": health.status,
                            })
                        );
                    } else {
                        println!("✓ Electro CLI initialized successfully!");
                        println!("  Endpoint:       {}", endpoint);
                        println!(
                            "  Authentication: {}",
                            if token.is_some() { "Bearer Token Configured" } else { "None (Open / Local)" }
                        );
                        println!("  Config File:    {}", path.display());
                        println!("  Server Health:  {}", health.status);
                    }
                }
                Err(e) => {
                    eprintln!("Error saving configuration file: {}", e);
                    std::process::exit(1);
                }
            }
        }
        Err(e) => {
            if as_json {
                println!(
                    "{}",
                    json!({
                        "status": "error",
                        "error": e,
                        "endpoint": endpoint,
                    })
                );
            } else {
                eprintln!("Initialization failed: Could not verify connection.");
                eprintln!("{}", e);
            }
            std::process::exit(1);
        }
    }
}

fn handle_status(client: &ElectroClient, as_json: bool) {
    match client.get_dashboard() {
        Ok(dash) => {
            let snap = dash.data;
            let (source_str, source_code) = match snap.as_ref().and_then(|s| s.eb_dg_status) {
                Some(0) => ("Grid", Some(0)),
                Some(1) => ("DG", Some(1)),
                _ => ("Unknown", None),
            };

            let balance = snap.as_ref().and_then(|s| s.meter_balance);
            let relay = snap.as_ref().and_then(|s| s.relay_status.as_deref()).unwrap_or("UNKNOWN");
            let age = dash.age_seconds.unwrap_or(0.0);
            let kwh_today = snap.as_ref().and_then(|s| s.kwh_rec_today);
            let kwh_month = snap.as_ref().and_then(|s| s.kwh_rec_month);
            let meter_id = snap.as_ref().and_then(|s| s.meter_id);

            if as_json {
                println!(
                    "{}",
                    json!({
                        "source": source_str,
                        "source_code": source_code,
                        "meter_id": meter_id,
                        "balance_inr": balance,
                        "relay_status": relay,
                        "age_seconds": (age * 10.0).round() / 10.0,
                        "kwh_today": kwh_today,
                        "kwh_month": kwh_month,
                        "status": dash.status,
                        "stale": dash.stale,
                        "last_success_at": dash.last_success_at,
                    })
                );
            } else {
                let icon = if source_str == "Grid" { "⚡" } else if source_str == "DG" { "⛽" } else { "❓" };
                println!("{} Power Source:  {} ({})", icon, source_str, if source_str == "Grid" { "Main Grid Supply Active" } else { "Backup Generator Active" });
                if let Some(b) = balance {
                    println!("💰 Meter Balance: ₹{:.2}", b);
                }
                println!("🔌 Relay Status:  {}", relay);
                println!(
                    "⏱️  Telemetry:     Updated {:.0}s ago (Status: {}, {}s cadence)",
                    age,
                    dash.status,
                    dash.poll_interval_seconds.unwrap_or(60)
                );
                if let Some(m) = kwh_month {
                    println!("📅 Month Energy:  {:.2} kWh", m);
                }
                if let Some(t) = kwh_today {
                    println!("⚡ Today Energy:  {:.2} kWh", t);
                }
            }
        }
        Err(e) => {
            if as_json {
                println!("{}", json!({ "error": "fetch_failed", "message": e }));
            } else {
                eprintln!("{}", e);
            }
            std::process::exit(1);
        }
    }
}

fn handle_summary(client: &ElectroClient, range: &str, as_json: bool) {
    match client.get_history(range) {
        Ok(hist) => {
            let a = hist.analytics;

            if as_json {
                println!(
                    "{}",
                    json!({
                        "range": hist.range,
                        "total_snapshots": a.total_snapshots,
                        "telemetry_coverage_pct": a.telemetry_coverage_pct,
                        "grid_availability_pct": a.grid_availability_pct,
                        "grid_runtime_minutes": a.estimated_grid_minutes,
                        "dg_runtime_minutes": a.estimated_dg_minutes,
                        "power_transitions": a.total_transitions,
                        "balance_start_inr": a.balance_start,
                        "balance_current_inr": a.balance_end,
                        "balance_change_inr": a.balance_change,
                        "kwh_consumption": a.consumption_kwh,
                    })
                );
            } else {
                println!("📊 Electro Telemetry Summary ({})", hist.range.to_uppercase());
                println!("──────────────────────────────────────────────");
                println!("Power Source Runtime:");
                println!(
                    "  ⚡ Grid Runtime:          {} ({:.1}%)",
                    format_duration_mins(a.estimated_grid_minutes),
                    a.grid_availability_pct
                );
                println!(
                    "  ⛽ DG (Generator) Runtime: {} ({:.1}%)",
                    format_duration_mins(a.estimated_dg_minutes),
                    100.0 - a.grid_availability_pct
                );
                println!("  🔄 Source Transitions:    {} switches", a.total_transitions);
                println!("\nConsumption & Expenditure:");
                if let (Some(start), Some(end), Some(diff)) = (a.balance_start, a.balance_end, a.balance_change) {
                    println!("  💰 Start Balance:         ₹{:.2}", start);
                    println!("  💰 Current Balance:       ₹{:.2}", end);
                    println!("  📉 Net Spend / Deduction: ₹{:.2}", diff);
                }
                if let Some(kwh) = a.consumption_kwh {
                    println!("  ⚡ Recorded Consumption:  {:.2} kWh", kwh);
                }
                println!("\nData Quality:");
                println!("  📈 Monitored Snapshots:   {}", a.total_snapshots);
                println!("  📡 Telemetry Coverage:    {:.1}%", a.telemetry_coverage_pct);
            }
        }
        Err(e) => {
            if as_json {
                println!("{}", json!({ "error": "fetch_failed", "message": e }));
            } else {
                eprintln!("{}", e);
            }
            std::process::exit(1);
        }
    }
}

fn handle_transitions(client: &ElectroClient, range: &str, limit: usize, as_json: bool) {
    match client.get_history(range) {
        Ok(hist) => {
            let mut transitions = hist.transitions;
            transitions.truncate(limit);

            if as_json {
                println!("{}", json!({ "range": range, "count": transitions.len(), "transitions": transitions }));
            } else {
                println!("🔄 Power Source Transitions ({})", range.to_uppercase());
                println!("──────────────────────────────────────────────");
                if transitions.is_empty() {
                    println!("No power source switches recorded during this timeframe.");
                    println!("Power has remained 100% on the active source.");
                } else {
                    for t in transitions {
                        let from_str = match t.from_code {
                            Some(0) => "Grid",
                            Some(1) => "DG",
                            _ => "Unknown",
                        };
                        let to_str = match t.to_code {
                            Some(0) => "Grid",
                            Some(1) => "DG",
                            _ => "Unknown",
                        };
                        let time = t.observed_at_utc.as_deref().unwrap_or("--");
                        let dur = t.detection_window_seconds.unwrap_or(0.0);
                        println!("• {} -> {} at {} (detection window: {:.0}s)", from_str, to_str, time, dur);
                    }
                }
            }
        }
        Err(e) => {
            if as_json {
                println!("{}", json!({ "error": "fetch_failed", "message": e }));
            } else {
                eprintln!("{}", e);
            }
            std::process::exit(1);
        }
    }
}

fn handle_health(client: &ElectroClient, as_json: bool) {
    match client.get_health() {
        Ok(h) => {
            if as_json {
                println!("{}", serde_json::to_string_pretty(&h).unwrap_or_else(|_| "{}".to_string()));
            } else {
                println!("🏥 Electro Service Health: {}", h.status.to_uppercase());
                println!("──────────────────────────────────────────────");
                println!("Collector:     {} ({})", h.collector.status, h.collector.role);
                println!(
                    "24h Polling:   {:.1}% success rate ({} successes, {} failures)",
                    h.poll_stats_24h.success_rate_pct,
                    h.poll_stats_24h.successes,
                    h.poll_stats_24h.failures
                );
                println!("Avg Latency:   {:.0} ms", h.poll_stats_24h.avg_duration_ms);
                println!("Database:      {} ({} snapshots stored)", if h.database.ok { "OK" } else { "ERROR" }, h.database.snapshot_count);
                if let Some(tel) = h.telemetry {
                    if let Some(age) = tel.age_seconds {
                        println!("Latest Poll:   {:.0}s ago", age);
                    }
                }
            }
        }
        Err(e) => {
            if as_json {
                println!("{}", json!({ "error": "fetch_failed", "message": e }));
            } else {
                eprintln!("{}", e);
            }
            std::process::exit(1);
        }
    }
}

fn handle_refresh(client: &ElectroClient, as_json: bool) {
    match client.refresh() {
        Ok(val) => {
            if as_json {
                println!("{}", val);
            } else {
                println!("✓ Meter reading refresh successfully triggered!");
            }
        }
        Err(e) => {
            if as_json {
                println!("{}", json!({ "error": "refresh_failed", "message": e }));
            } else {
                eprintln!("{}", e);
            }
            std::process::exit(1);
        }
    }
}

fn handle_export(client: &ElectroClient, range: &str, output_path: Option<&str>) {
    match client.export_csv(range) {
        Ok(csv_data) => {
            if let Some(path) = output_path {
                match std::fs::write(path, &csv_data) {
                    Ok(_) => println!("✓ Exported {} bytes to {}", csv_data.len(), path),
                    Err(e) => {
                        eprintln!("Failed to write to {}: {}", path, e);
                        std::process::exit(1);
                    }
                }
            } else {
                use std::io::Write;
                let _ = std::io::stdout().write_all(csv_data.as_bytes());
            }
        }
        Err(e) => {
            eprintln!("{}", e);
            std::process::exit(1);
        }
    }
}
