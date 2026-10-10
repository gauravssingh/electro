# ELNet Personal Electricity Dashboard --- MVP Technical Design

**Status:** Proposed implementation specification\
**Last updated:** 2026-10-10\
**Target:** Local coding agent\
**Primary objective:** Build a private, local dashboard that reads the
user's own ELNet meter data, stores periodic snapshots, and charts
consumption/balance trends.

## 1. Product goal

Create a small local web app for a single authorized ELNet account. The
app logs into ELNet using the observed encrypted login request, calls
the read-only `HomeData` endpoint, normalizes the response, stores
time-stamped snapshots in SQLite, and presents a dashboard with current
values and historical trends.

The MVP should be reliable, inspectable, easy to run locally, and safe
by default. It must not mutate upstream meter/account/payment state.

## 2. Scope

### In scope

-   Local-only web application.
-   Python backend and SQLite.
-   HTML templates, Tailwind CSS, vanilla JavaScript, and Axios or
    `fetch`; no React.
-   Encrypted ELNet login and read-only HomeData retrieval.
-   Credentials entered through environment variables or a secure local
    setup path; never embedded in source.
-   In-memory bearer token handling by default.
-   Current balance, daily/month-to-date energy fields, EB/DG/Solar
    amounts, relay status, enabled source flags.
-   Snapshot history with configurable polling (default 60 seconds,
    configurable).
-   Trend charts based on snapshots.
-   Last-success timestamp, API status, and graceful handling of
    network/authentication failures.
-   Manual refresh.
-   Local logs with sensitive-field redaction.
-   Basic tests using synthetic fixtures and mocked upstream responses.

### Out of scope

-   Any relay/meter control or write endpoint.
-   Recharge, payment, order creation, or checkout.
-   Profile updates, password updates, complaint submissions,
    goal-setting writes.
-   Multi-account or multi-tenant hosting.
-   Public internet access, cloud deployment, or exposing an upstream
    proxy.
-   Historical backfill that the upstream API does not provide.
-   Treating successive cumulative meter values as daily deltas without
    reset/rollover logic.
-   AI analysis or anomaly detection in MVP.

> **Product Roadmap:** For planned future product direction, UI enhancements, and phased analytical capabilities beyond the initial MVP, see the canonical [Product Roadmap](ROADMAP.md).

## 3. Known upstream contract

Use the separate **ELNet API Discovery Reference** as the endpoint
catalogue.

Confirmed calls:

-   `POST /api/Dashboard/login`, encrypted payload fields `Access`,
    `FCMID`, `Password`, `UserName`, returns `Data.AuthToken` and
    account metadata.
-   `POST /api/Dashboard/HomeData`, bearer token required, encrypted
    payload `{"MeterID": <integer>}`.
-   `MeterID` should be taken from the authorized login response's
    `Data.FlatId` unless source validation proves a different mapping.
-   Upstream URL configured via `ELNET_BASE_URL` (e.g., `http://<elnet-server-host>:8091`).

The upstream service uses HTTP. Treat the local app as sensitive. Do not
run on public Wi-Fi if avoidable, do not bind to all interfaces by
default, and do not expose it through a reverse proxy or port forward.

## 4. Recommended architecture

``` text
Browser (local only)
  |
  | GET /, GET /api/dashboard, GET /api/history, POST /api/refresh
  v
Python web app (Flask recommended)
  |-- ELNet client adapter
  |     |-- encrypt/decrypt helpers as required
  |     |-- login and in-memory token lifecycle
  |     |-- read-only HomeData request
  |
  |-- Polling service
  |     |-- scheduled fetch with bounded timeout
  |     |-- normalize payload
  |     |-- store snapshot
  |
  |-- Repository layer
  |     |-- SQLite schema/migrations
  |     |-- latest snapshot/history queries
  |
  v
SQLite file in local app data directory
```

Use Flask because the UI is server-rendered and small; keep the ELNet
client, polling, and repository code independent of Flask so they can be
tested directly. Do not add React, Docker, a cloud database, or a
message queue for this MVP.

### Suggested project layout

``` text
electro/
  app.py
  config.py
  elnet/
    __init__.py
    crypto.py
    client.py
    schemas.py
  services/
    __init__.py
    polling.py
    normalization.py
  storage/
    __init__.py
    db.py
    repository.py
    migrations/
  templates/
    base.html
    dashboard.html
  static/
    css/
    js/
  tests/
    fixtures/
      login_success.json
      homedata_success.json
    test_crypto.py
    test_client.py
    test_normalization.py
    test_repository.py
    test_routes.py
  .env.example
  .gitignore
  README.md
```

Adapt to the existing repository instead of overwriting working code.
Inspect the current files first, preserve the existing `elnet_client.py`
behavior, and refactor incrementally.

## 5. Configuration and secrets

Configuration through environment variables (document names in
`.env.example`, never actual values):

-   `ELNET_BASE_URL` --- default to the observed base URL.
-   `ELNET_USERNAME`
-   `ELNET_PASSWORD`
-   `ELNET_SKEY`
-   `ELNET_IVKEY`
-   `ELNET_FCM_TOKEN` --- optional; default empty string.
-   `ELNET_POLL_INTERVAL_SECONDS` --- default `60`.
-   `ELNET_REQUEST_TIMEOUT_SECONDS` --- default `15`.
-   `ELECTRO_DB_PATH` --- local SQLite path.
-   `ELECTRO_HOST` --- default `127.0.0.1`.
-   `ELECTRO_PORT` --- default `5000`.
-   `ELECTRO_LOG_LEVEL` --- default `INFO`.

Rules:

-   Never commit `.env`; include it in `.gitignore`.
-   Provide `.env.example` with placeholders only.
-   Never print the password, AES parameters, `AuthToken`, raw login
    response, or unredacted upstream payload.
-   Prefer environment variables or a permissions-restricted local
    config file. Do not store credentials in SQLite.
-   Keep the bearer token in memory only. On app restart, authenticate
    again.
-   If auth fails, stop polling and show a redacted, actionable error;
    do not repeatedly hammer login.
-   Because upstream is plain HTTP, explain the transport risk in README
    and UI diagnostics.

## 6. Upstream client requirements

### 6.1 Crypto adapter

Implement one small, unit-tested helper that: 1. Serializes payload to
compact UTF-8 JSON. 2. Encrypts with AES-CBC and PKCS#7 padding using
the same key/IV byte derivation as the APK. 3. Base64-encodes
ciphertext. 4. Wraps it as `{"InputType": "<ciphertext>"}`.

Do not print keys or plaintext login payloads. Use deterministic unit
tests with synthetic values and test ciphertext length/padding behavior.
Do not add a fake security layer or claim this encryption makes HTTP
safe.

### 6.2 Login

-   POST encrypted `LoginInput` with exact property names: `Access`,
    `FCMID`, `Password`, `UserName`.
-   Normal app path sets `Access` to `"Live"`.
-   Send no bearer token on login.
-   Parse JSON defensively.
-   Require application status success and a non-empty `Data.AuthToken`.
-   Read `Data.FlatId` and validate it is a positive integer before
    using it as `MeterID`.
-   Do not retain or display the full `Data` object.
-   Do not include account contact/address details in the dashboard
    unless a future requirement needs them.

### 6.3 HomeData

-   POST encrypted `{"MeterID": <flat id>}`.
-   Include `Authorization: Bearer <token>`.
-   Use request timeout and handle connection errors, HTTP errors,
    malformed JSON, non-success application status, missing data, and
    token expiry.
-   Only retry safe read-only requests, with bounded backoff. Do not
    automatically retry a failed login continuously.
-   Keep upstream raw fields in an adapter mapping; UI and storage use
    normalized names.

### 6.4 Polling

-   Default interval: 60 seconds, configurable.
-   Ensure only one poll runs at a time.
-   Start with an immediate fetch, then poll on the configured interval.
-   Avoid concurrent polling from both a background thread and manual
    refresh. Use a lock or single worker.
-   On transient failures, preserve the last successful snapshot and
    show "stale" status plus last-success time.
-   Do not insert duplicate rows just because the browser page reloads;
    snapshots correspond to upstream fetch attempts that return valid
    data.
-   Use bounded request timeouts. A slow upstream call must not block
    all web requests.

Implementation choice: for a simple MVP, a single background polling
thread with a lock is acceptable. If the app process restarts, polling
resumes. Do not add Celery/Redis for this local single-user app.

## 7. Normalized data model

The upstream API's exact units/semantics for some fields have not been
fully verified. Keep source field names in the adapter and display
labels conservatively. Do not silently reinterpret readings.

### 7.1 Snapshot fields

Store one row per successful HomeData fetch:

-   `id` --- integer primary key.
-   `observed_at_utc` --- UTC timestamp generated by the local client at
    successful response receipt.
-   `meter_id` --- integer used in request.
-   `device_id` --- nullable numeric/string source identifier.
-   `meter_balance` --- nullable decimal/real from `MeterBal`.
-   `relay_status` --- nullable string from `RelStatus`.
-   `kwh_rec_today` --- nullable real.
-   `kvah_rec_today` --- nullable real.
-   `kwh_del_today` --- nullable real.
-   `solar_today` --- nullable real.
-   `kvah_del_today` --- nullable real.
-   `kwh_rec_month` --- nullable real.
-   `kvah_rec_month` --- nullable real.
-   `kwh_del_month` --- nullable real.
-   `solar_month` --- nullable real.
-   `kvah_del_month` --- nullable real.
-   `current_day_eb` --- nullable real.
-   `current_day_dg` --- nullable real.
-   `current_day_solar` --- nullable real.
-   `fixed_charges_today` --- nullable real.
-   `current_month_eb` --- nullable real.
-   `current_month_dg` --- nullable real.
-   `current_month_solar` --- nullable real.
-   `fixed_charges_month` --- nullable real.
-   `is_eb`, `is_dg`, `is_solar`, `is_fixed_charge`, `is_coil_enable`
    --- nullable booleans.
-   `eb_dg_status`, `kwh_kvah_status` --- nullable integers/numeric
    codes.
-   `meter_sn` --- optional; prefer not to persist unless needed. If
    persisted, treat as sensitive and do not show by default.

Use SQLite numeric types appropriate for the app. Store timestamps in
ISO-8601 UTC. Do not store password, encryption parameters, FCM token,
bearer token, email, mobile, or address in the snapshot table.

### 7.2 Poll/run status

A separate small status table or in-memory status object should track: -
`last_attempt_at_utc` - `last_success_at_utc` - `last_error_category`
(sanitized enum/string, not raw response) - `consecutive_failures` -
`polling_enabled` - `next_poll_at_utc` if useful

Avoid storing full raw upstream responses by default. If diagnostic raw
capture is temporarily enabled during development, keep it local,
short-lived, and redacted.

### 7.3 Database indexes and retention

-   Index `observed_at_utc`.
-   Index `(meter_id, observed_at_utc)`.
-   Keep all snapshots for the MVP, but document that 60-second polling
    creates about 1,440 rows/day. Add a configurable
    retention/compaction policy before long-term use; do not delete data
    silently.
-   SQLite access must be safe across the polling thread and request
    handlers. Use a new connection per operation or a correctly
    configured connection strategy; do not share an unsafe connection
    across threads.

## 8. Dashboard UX

Use a responsive, simple layout with Tailwind CSS and vanilla
JavaScript.

### 8.1 Header/status strip

-   App title: "Electro --- Meter Dashboard".
-   Connection indicator: Connected / Stale / Authentication required /
    Offline.
-   Last successful refresh timestamp in local timezone.
-   Manual "Refresh now" action.
-   Do not show secrets or raw API response.

### 8.2 Summary cards

-   Meter balance (label currency only if verified; likely INR, but make
    the label configurable or use a neutral label until confirmed).
-   Today's recorded energy (`kWhRecToday`, with unit only after
    validating app display).
-   Month-to-date recorded energy (`kWhRecMonth`).
-   Relay status as informational text only. No relay controls or
    buttons.

### 8.3 Usage and cost charts

-   Energy chart: snapshots of `kWhRecToday` and/or `kWhRecMonth` over
    time.
-   Amount chart: `CurrentDay_EB`, `CurrentMonth_EB`, and corresponding
    DG/Solar/fixed-charge fields, but clearly mark their interpretation
    as provisional until confirmed against the app.
-   A time range selector: 24 hours, 7 days, 30 days.
-   The chart must not present repeated cumulative readings as interval
    consumption. Either chart the cumulative reported values as a time
    series or compute deltas with explicit reset/day-boundary logic and
    label them "estimated delta."
-   If there are not enough samples, show an empty state with the
    explanation that history builds after polling starts.
-   Prefer lightweight browser charts; keep dependencies minimal.

### 8.4 Recent readings table

Optional for MVP if time allows: - Timestamp, balance, today kWh, month
kWh, relay status. - Paginated or limited to recent rows; no need for
DataTables unless row count makes it useful.

### 8.5 Accessibility and responsive behavior

-   Clear labels, keyboard-accessible buttons, visible focus states,
    semantic headings.
-   Good mobile layout and readable number formatting.
-   Use text/status indicators in addition to color.

## 9. Local HTTP API for the frontend

This is the app's own API, not ELNet's upstream API.

Suggested routes:

  -----------------------------------------------------------------------------------
  Method                  Local path                          Behavior
  ----------------------- ----------------------------------- -----------------------
  `GET`                   `/`                                 Render dashboard shell.

  `GET`                   `/api/dashboard`                    Return latest
                                                              normalized snapshot
                                                              plus connection/poll
                                                              status.

  `GET`                   `/api/history?range=24h\|7d\|30d`   Return bounded
                                                              time-series points.
                                                              Validate allowed range
                                                              values.

  `POST`                  `/api/refresh`                      Trigger a single safe
                                                              HomeData fetch; return
                                                              accepted/current status
                                                              or result. Prevent
                                                              overlapping refreshes.

  `GET`                   `/healthz`                          Local process health
                                                              only; do not include
                                                              credentials or upstream
                                                              payload.
  -----------------------------------------------------------------------------------

Example normalized `/api/dashboard` response:

``` json
{
  "status": "connected",
  "last_success_at": "2026-10-10T12:00:00Z",
  "stale": false,
  "data": {
    "meter_balance": 0.0,
    "relay_status": "ON",
    "kwh_rec_today": 0.0,
    "kwh_rec_month": 0.0,
    "current_day_eb": 0.0,
    "current_month_eb": 0.0
  }
}
```

Do not send upstream `AuthToken`, login payload, credentials, encryption
values, contact information, or the full raw response to the browser.

## 10. Failure handling and observability

Classify errors into: - `configuration_error` - `authentication_error` -
`authorization_or_expired_token` - `upstream_http_error` -
`upstream_application_error` - `timeout` - `network_error` -
`invalid_response` - `storage_error`

Requirements: - Log category, timestamp, endpoint suffix, HTTP status if
available, and elapsed time. - Never log Authorization headers,
plaintext login JSON, passwords, keys/IV, tokens, full login response,
or personal details. - For unexpected HTTP responses, log a bounded,
sanitized summary only. - Show the last known data with a stale
indicator after transient failures. - Stop repeated polling on
authentication errors and require reauthentication/restart rather than
tight-looping. - Retry only safe read-only HomeData requests with
exponential backoff and a cap; do not retry indefinitely. - Avoid
logging sensitive upstream response bodies.

## 11. Security and privacy requirements

1.  Bind Flask to `127.0.0.1` by default.
2.  Do not add a public listener, tunnel, port-forwarding instructions,
    or internet deployment.
3.  Upstream is HTTP; disclose this risk in README and diagnostics.
4.  Treat the bearer token as a secret; keep it in memory only.
5.  Do not store user password, key, IV, token, FCM token, phone, email,
    address, or raw login response in SQLite.
6.  `.env` must be gitignored; `.env.example` contains placeholders
    only.
7.  Never commit Proxyman exports, real responses, screenshots
    containing personal data, or account identifiers.
8.  Redact exception messages if they can contain request/response data.
9.  Do not add endpoints for state-changing upstream operations.
10. Frontend must not call the upstream API directly.
11. Add a visible note that the app is a personal unofficial dashboard
    and is not affiliated with ELMeasure.
12. Do not treat fixed AES-CBC parameters as protection for secrets over
    HTTP.

## 12. Testing strategy

Use pytest and mocks; do not call the real upstream service in automated
tests.

### Unit tests

-   AES padding and Base64 encoding/decode round-trip with synthetic
    payloads.
-   Exact login field names and `Access="Live"`.
-   HomeData payload includes integer `MeterID`.
-   Authorization header is added to HomeData but not login.
-   Login failure or missing token is handled without leaking response
    data.
-   Normalization handles missing/null optional fields.
-   Boolean/numeric status fields are stored correctly.
-   Snapshot inserts and history range queries work.
-   Timestamps are UTC and JSON serializable.
-   Poll lock prevents overlapping runs.
-   Error logs redact tokens and secrets.

### Route tests

-   `/` returns dashboard page.
-   `/api/dashboard` never exposes token or credentials.
-   `/api/history` rejects invalid range values and returns bounded
    data.
-   `/api/refresh` prevents overlapping refresh requests and handles
    upstream failures.
-   `/healthz` contains no sensitive information.

### Integration check (manual, authorized account)

-   Login succeeds.
-   `FlatId` is validated and used as `MeterID`.
-   HomeData succeeds and a snapshot is persisted.
-   Refresh produces a new timestamped snapshot.
-   Simulated network failure leaves last snapshot visible and marks
    dashboard stale.
-   Restarting the app requires login again and does not recover a token
    from disk.

## 13. Acceptance criteria

The MVP is complete when:

1.  `README.md` documents setup, configuration, running, testing, and
    the HTTP transport risk.
2.  The app runs locally with one documented command.
3.  The first successful fetch displays balance, today's and
    month-to-date energy, and relay status.
4.  A valid HomeData response is stored in SQLite with a UTC timestamp.
5.  Polling captures snapshots at the configured interval without
    overlapping requests.
6.  The dashboard displays historical samples from SQLite and makes
    clear when history is still accumulating.
7.  Manual refresh works and does not start a parallel fetch.
8.  Failed requests preserve last known data and show stale/error
    status.
9.  No secrets, bearer tokens, or personal contact/address data appear
    in logs, browser JSON, or the database.
10. No upstream state-changing endpoint is called anywhere in the
    codebase.
11. Tests pass without network access.
12. The server binds to loopback by default.

## 14. Implementation sequence

### Phase 1 --- Inspect and stabilize

-   Inspect the existing repository before editing.
-   Identify current `elnet_client.py`, venv, dependencies, and tests.
-   Preserve the working login/HomeData flow.
-   Resolve Python-version compatibility deliberately. Current
    environment is Python 3.9.6, so avoid PEP 604 `str | None` syntax
    unless upgrading to Python 3.10+; prefer `Optional[str]` for
    compatibility.
-   Address the `urllib3`/LibreSSL warning separately. It is a warning,
    not the original type-annotation crash. Do not suppress it without
    documenting why.

### Phase 2 --- Refactor upstream client

-   Move crypto and HTTP logic into `elnet/crypto.py` and
    `elnet/client.py`.
-   Add timeout, redacted errors, application-level success checks,
    token-in-memory handling.
-   Add synthetic tests and mocks.

### Phase 3 --- Persistence

-   Add SQLite schema/repository.
-   Store normalized successful snapshots only.
-   Add migration/versioning strategy even if the first migration is
    simple.
-   Test persistence and history retrieval.

### Phase 4 --- Polling/status

-   Add one background poller with locking and bounded retry.
-   Track last attempt/success and stale state.
-   Add a manual refresh path using the same lock.

### Phase 5 --- Dashboard

-   Add Flask routes and HTML template.
-   Implement summary cards, status strip, time-series charts, and empty
    states.
-   Ensure no secret fields cross the browser boundary.

### Phase 6 --- Hardening and handoff

-   Run tests and linting.
-   Review logs for sensitive values.
-   Verify the app binds to `127.0.0.1`.
-   Update README with known upstream limitations and unverified field
    semantics.
-   Provide the user with a concise summary of files changed and
    commands to run.

## 15. Coding-agent operating instructions

Before making changes:

1.  Read the whole existing repository and identify the current working
    client.
2.  Do not overwrite the existing working request flow without tests.
3.  Implement incrementally in the phases above.
4.  Do not invent upstream endpoint payloads or call routes whose
    schemas have not been verified.
5.  Do not use the real user's captured responses as fixtures; create
    synthetic redacted fixtures.
6.  Never print or copy actual credentials, AES constants, bearer
    tokens, or personal data into documentation or source.
7.  Keep the application read-only against ELNet.
8.  At the end, report tests run, files changed, run instructions, and
    known limitations.

## 16. Open questions / assumptions to validate

-   Exact units and user-facing meaning of `kWhRecToday` and
    `kWhRecMonth`.
-   Whether `CurrentDay_EB` and `CurrentMonth_EB` are currency amounts
    and whether the same is true for DG/Solar/fixed-charge fields.
-   Meaning of `EBDGStatus` (**resolved**: `0` = Grid/EB, `1` = Generator/DG, confirmed in `HomeUserFragment.java`), `kwh_kvah_Status`, and `IsCoilEnable`.
-   Whether the upstream token can be refreshed and its expiry behavior.
-   Whether a 60-second polling interval is permitted and sustainable;
    make it configurable.
-   Whether meter balance is always denominated in INR.
-   Whether date-boundary/reset behavior for daily/monthly fields
    follows local timezone and billing conventions.

Do not block the first read-only dashboard on optional endpoint
discovery. Build the MVP around the two confirmed endpoints, then extend
the API reference only after verifying additional read-only calls.
