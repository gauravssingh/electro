# Electro ⚡

> **Private, local dashboard and telemetry monitor for ELMeasure ELNet prepaid electricity meters.**

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/Framework-Flask-black.svg)](https://flask.palletsprojects.com/)
[![Database](https://img.shields.io/badge/Database-SQLite%20(WAL)-lightgrey.svg)](https://www.sqlite.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-32%20Passing-brightgreen.svg)](tests/)

**Electro** is a lightweight, local-first web application, background daemon, and CLI that connects to the ELNet PPS smart electricity metering system. It reverse-engineers the observed Android AES-CBC encryption protocol, periodically snapshots live meter readings into a local SQLite database, and presents an interactive real-time dashboard for balance, energy consumption, and power source telemetry.

---

## ⚡ Highlights

- **Durable Periodic Capture**: Polls on a fixed monotonic cadence (15s default) independently of whether the browser is open.
- **Active Power Source Detection**: Accurately decodes `EBDGStatus` (`0` = Main Grid / EB, `1` = Backup Generator / DG) verified from decompiled mobile sources.
- **Source Transition Timeline**: Records exact transition timestamps, detection delays, and previous/new source when power switches between Grid and Generator.
- **Trustworthy Duration Analytics**: Computes elapsed time from adjacent observations with explicit unmonitored gap detection (survives Mac sleep, network drops, and service restarts without overcounting).
- **macOS launchd Integration**: Ready to run as a persistent, self-healing per-user LaunchAgent with automated startup and bounded log rotation.
- **Telemetry & Health Probing**: Exposes `/healthz` for process liveness and `/api/health` for detailed telemetry age, failure counts, and 24h success rates.
- **Independent Relay Telemetry**: Displays `RelStatus` independently as informational state without conflating it with the power source.
- **Strictly Read-Only**: Zero upstream mutation calls. No relay toggling, no recharge flows, no order creation, and no profile modifications.
- **Privacy by Default**: Bearer authentication tokens are stored strictly in-memory and never written to disk or logs. The web server binds exclusively to `127.0.0.1`.

---

## 🏗️ Architecture

```text
macOS launchd (LaunchAgent: com.gauravs.electro)
  │  Runs at login, restarts on crashes, logs to ~/Library/Logs/electro/
  ▼
Gunicorn / Flask Application (app.py)
  ├── Services
  │     ├── PollingService (Fixed monotonic cadence, non-blocking lock, exponential backoff)
  │     ├── Analytics      (Elapsed-time duration calculations, transition detection, gap analysis)
  │     └── Normalization  (Clean data model & EBDGStatus / RelStatus mappers)
  │
  ├── Storage (storage/)
  │     ├── Repository (Snapshots & poll_events query interface)
  │     └── SQLite DB  (PRAGMA journal_mode=WAL, thread-safe connection lifecycle)
  │
  └── ELNet Client Adapter (elnet/)
        ├── Crypto Engine (AES-256-CBC, PKCS#7 padding matching Android Utility.java)
        └── Client Adapter (In-memory token lifecycle, recoverable session refresh)
              │
              │  POST /api/Dashboard/login     (Encrypted payload envelope)
              │  POST /api/Dashboard/HomeData  (Encrypted payload + Bearer token)
              ▼
   ELNet Upstream Server (Plain HTTP :8091)
```

---

## 🔒 Security & Privacy Notice

1. **Unencrypted Upstream HTTP**:
   The upstream ELNet portal operates over plain HTTP (e.g. port 8091). Although the application payload is encrypted using AES-CBC with fixed keys, standard HTTP provides **no authenticated TLS protection**. Do not expose Electro over public networks or forward ports to the internet.
2. **Local Loopback Isolation**:
   Electro binds strictly to `127.0.0.1` by default.
3. **Zero Credential Persistence**:
   Account passwords, encryption parameters, and bearer tokens are never written to SQLite, logs, or frontend payloads.
4. **Git Protection**:
   Local `.env` and SQLite database files (`*.db`, `*.db-wal`) are ignored via `.gitignore`.

---

## 🚀 Quickstart

### 1. Prerequisites

- Python 3.9+ (tested with macOS Python 3.9.6)
- macOS (for LaunchAgent background daemon) or Linux

### 2. Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/gauravssingh/electro.git
cd electro

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configuration

Copy the example environment configuration:

```bash
cp .env.example .env
```

Edit `.env` with your account details and encryption keys:

```env
# Upstream Server (configured according to your local ELNet deployment)
ELNET_BASE_URL=http://your-elnet-host:8091

# ELNet Credentials
ELNET_USERNAME=your_username
ELNET_PASSWORD=your_password

# Cryptographic Keys (obtained from decompiled Android Utility.java)
# Never commit real keys to source control!
ELNET_SKEY=your_32_character_skey
ELNET_IVKEY=your_16_character_ivkey

# Optional Polling & Server Options (15s recommended for source transition detection)
ELNET_POLL_INTERVAL_SECONDS=15
ELNET_REQUEST_TIMEOUT_SECONDS=15
ELECTRO_HOST=127.0.0.1
ELECTRO_PORT=5000
ELECTRO_DB_PATH=electro.db
ELECTRO_LOG_LEVEL=INFO
```

---

## 💻 Running Electro

### Option A: Install as an Always-On macOS Service (Recommended)

To run Electro continuously in the background on your Mac across restarts and logins:

```bash
./scripts/launchd/install_service.sh
```

- Installs `~/Library/LaunchAgents/com.gauravs.electro.plist` running via Gunicorn under your user session (no root needed).
- Automatically starts on login and restarts if interrupted.
- Standard logs are written to `~/Library/Logs/electro/`.

To inspect service health and logs:
```bash
./scripts/launchd/service_status.sh
```

To stop and remove the service:
```bash
./scripts/launchd/uninstall_service.sh
```

### Option B: Run in Terminal for Development

```bash
./.venv/bin/python app.py
```

Open your browser to:
👉 **[http://127.0.0.1:5000](http://127.0.0.1:5000)**

### Option C: Run the Standalone CLI Client

To test authentication and print raw decrypted telemetry to stdout:

```bash
./.venv/bin/python elnet_client.py
```

---

## 🧪 Testing

Electro includes comprehensive offline unit and integration tests using synthetic fixtures and network mocks:

```bash
./.venv/bin/pytest -v
```

Test coverage includes:
- AES-256-CBC PKCS#7 encryption and decryption round-trips.
- Error categorization and credential redaction.
- Upstream authentication and authorization handling.
- Normalization and `EBDGStatus` / `RelStatus` mapping.
- Non-blocking polling locks and race-condition prevention.
- SQLite WAL migrations and time-series queries.
- Frontend API route security and payload sanitization.

---

## 📑 Discovered Upstream Endpoints

Verified against `APIService.java` and live endpoint probing:

| Endpoint | Method | Status | Notes |
| :--- | :--- | :--- | :--- |
| `/api/Dashboard/login` | `POST` | **Confirmed Live** | Authenticates with `Access: "Live"` and returns `AuthToken` + `FlatId` |
| `/api/Dashboard/HomeData` | `POST` | **Confirmed Live** | Telemetry readings (`MeterBal`, `kWhRecToday`, `RelStatus`, `EBDGStatus`) |
| `/api/Dashboard/GetLiveUpdates` | `POST` | **Confirmed Live** | Returns instantaneous power draw (`PresentLoad` in kW), `SanctionEB`, `SanctionDG` |
| `/api/Dashboard/GetDashboardDetails` | `POST` | **Confirmed Live** | Returns `AvgEB`, `AvgCost`, and estimated days remaining (`ExpRechargeDays`) |
| `/api/Dashboard/GetLiveBalance` | `POST` | **Confirmed Live** | Dedicated lightweight endpoint for current balance |
| `/api/Dashboard/RechargeHistory` | `POST` | **Confirmed Live** | Historical payment transaction ledger |
| `/api/Dashboard/RelayOnOff` | `POST` | **Excluded** | State-changing relay switch; intentionally not called |

For complete reverse-engineering details, see [docs/ELNet_API_Discovery_Reference.md](docs/ELNet_API_Discovery_Reference.md).

---

## 🗺️ Product Roadmap & Future Enhancements

Electro's current version focuses on durable periodic telemetry capture, verified source decoding (`EBDGStatus`), and honest local persistence. 

Future product phases, UX improvements, and analytical capabilities are documented in the canonical [Product Roadmap](docs/ROADMAP.md) (or [ROADMAP.md](ROADMAP.md)):

- **Phase 1 — Trustworthy Dashboard**: Overview redesign around reported power source, capture freshness, and persisted Grid/DG transition timelines with visible missing-data gaps.
- **Phase 2 — Energy & Supply Analytics**: Daily consumption delta calculations, interval duration estimates, and CSV export.
- **Phase 3 — Balance Intelligence**: Depletion rate estimation, threshold forecasting, and data coverage indicators.
- **Phase 4 — Alerts & Operational Polish**: Local macOS notifications for stale capture or low balance, automated database backups, and settings configuration.

> **Note:** The roadmap outlines proposed future product direction and does not imply that planned features are currently implemented. Refer to [docs/ROADMAP.md](docs/ROADMAP.md) for detailed phase criteria, calculation data contracts, and design principles.

---

## ⚖️ Disclaimer

This is an unofficial, personal open-source project created strictly for single-user monitoring of the user's authorized account. It is not affiliated with, maintained by, or endorsed by **ELMeasure India Pvt. Ltd.** or its affiliates.

---

## 📜 License

Distributed under the [MIT License](LICENSE).
