# Electro ⚡

> **Private, local dashboard and telemetry monitor for ELMeasure ELNet prepaid electricity meters.**

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/Framework-Flask-black.svg)](https://flask.palletsprojects.com/)
[![Database](https://img.shields.io/badge/Database-SQLite%20(WAL)-lightgrey.svg)](https://www.sqlite.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-24%20Passing-brightgreen.svg)](tests/)

**Electro** is a lightweight, local-first web application and CLI that connects to the ELNet PPS smart electricity metering system. It reverse-engineers the observed Android AES-CBC encryption protocol, periodically snapshots live meter readings into a local SQLite database, and presents an interactive real-time dashboard for balance, energy consumption, and power source telemetry.

---

## ⚡ Highlights

- **Active Power Source Detection**: Accurately decodes `EBDGStatus` (`0` = Main Grid / EB, `1` = Backup Generator / DG) verified from decompiled mobile sources.
- **Source Transition Timeline**: Automatically records timestamps and transitions whenever supply switches between Grid and Generator.
- **DG Analytics & Reliability**: Quantifies cumulative time spent running on DG vs Grid, transition frequencies, and grid availability percentage.
- **Independent Relay Telemetry**: Displays `RelStatus` independently as informational state without conflating it with the power source.
- **Real-Time Prepaid Tracking**: Observes balance deductions and reported cumulative kWh over 24-hour, 7-day, and 30-day windows.
- **Strictly Read-Only**: Zero upstream mutation calls. No relay toggling, no recharge flows, no order creation, and no profile modifications.
- **Privacy by Default**: Bearer authentication tokens are stored strictly in-memory and never written to disk or logs. The web server binds exclusively to `127.0.0.1`.

---

## 🏗️ Architecture

```text
Browser (Local Loopback)
  │
  │  GET /                  (Dashboard UI)
  │  GET /api/dashboard     (Live snapshot & power source status)
  │  GET /api/history       (Historical time-series, DG analytics & transitions)
  │  POST /api/refresh      (Non-blocking manual fetch)
  ▼
Flask Application (app.py)
  ├── Services
  │     ├── PollingService (Non-blocking thread lock, periodic scheduler)
  │     └── Normalization  (Clean data model & EBDGStatus / RelStatus mappers)
  │
  ├── Storage (storage/)
  │     ├── Repository (Time-series queries & analytics calculations)
  │     └── SQLite DB  (PRAGMA journal_mode=WAL, thread-safe connection lifecycle)
  │
  └── ELNet Client Adapter (elnet/)
        ├── Crypto Engine (AES-256-CBC, PKCS#7 padding matching Android Utility.java)
        └── Client Adapter (In-memory token lifecycle, bounded exponential backoff)
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
- Virtual environment

### 2. Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/your-username/electro.git
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

# Optional Polling & Server Options
ELNET_POLL_INTERVAL_SECONDS=60
ELNET_REQUEST_TIMEOUT_SECONDS=15
ELECTRO_HOST=127.0.0.1
ELECTRO_PORT=5000
ELECTRO_DB_PATH=electro.db
ELECTRO_LOG_LEVEL=INFO
```

---

## 💻 Usage

### Run the Web Dashboard

```bash
./.venv/bin/python app.py
```

Open your browser to:
👉 **[http://127.0.0.1:5000](http://127.0.0.1:5000)**

- The dashboard will automatically authenticate, capture an initial snapshot, and start periodic background polling every 60 seconds.
- Click **Refresh Now** in the top-right header at any time to trigger an immediate fetch.

### Run the Standalone CLI Client

To run a quick terminal check and inspect live JSON telemetry:

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

## ⚖️ Disclaimer

This is an unofficial, personal open-source project created strictly for single-user monitoring of the user's authorized account. It is not affiliated with, maintained by, or endorsed by **ELMeasure India Pvt. Ltd.** or its affiliates.

---

## 📜 License

Distributed under the [MIT License](LICENSE).
