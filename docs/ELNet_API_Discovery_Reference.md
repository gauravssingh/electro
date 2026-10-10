# ELMeasure ELNet PPS v3.0 --- API Discovery Reference

**Document status:** Reverse-engineering notes for a personal, read-only
dashboard\
**Last updated:** 2026-10-10\
**Audience:** Local coding agent / implementation engineer

## 1. Purpose and evidence model

This document records the ELNet API behavior observed from the Android
APK decompilation and successful client requests. It is not an official
vendor API specification.

Each item is marked with one of these evidence levels:

-   **Confirmed live** --- exercised successfully from the Python proof
    of concept or directly observed in a successful network response.
-   **Confirmed in source** --- endpoint or behavior appears in
    decompiled Android source, but a live request/response has not
    necessarily been verified.
-   **Route discovered; details unverified** --- route name was found in
    the service interface or route inventory, but exact request fields,
    response schema, or live availability remain unknown.
-   **State-changing / excluded** --- route may alter meter state,
    account details, complaints, payment/order state, or other
    server-side state. Do not call from the MVP.

Do not infer a request schema from a route name. Before implementing any
unverified route, inspect its Retrofit declaration, input DTO, response
DTO, and---if appropriate---one user-authorized read-only app request.

## 2. Base URL and transport

-   Observed base URL: `http://<elnet-server-host>:8091`
-   Server fingerprint observed: IIS 10.0 / ASP.NET.
-   Transport is plain HTTP, not HTTPS. Authorization tokens and request
    data therefore lack TLS protection in transit.
-   Do not expose this service through a public dashboard or proxy it to
    the internet.
-   Treat captured bearer tokens and account data as credentials/private
    data. Do not log them or commit captures to source control.

## 3. Shared request conventions

### 3.1 Encrypted request envelope

For the two confirmed requests, the JSON body has this shape:

``` json
{
  "InputType": "<Base64-encoded AES ciphertext>"
}
```

The plaintext payload is serialized as compact UTF-8 JSON, encrypted,
then Base64-encoded.

### 3.2 Encryption observed in the Android source

-   Cipher transformation: `AES/CBC/PKCS7Padding`
-   Key bytes: first 32 bytes/characters of the source constant
    `Utility.SKEY`, encoded as bytes.
-   IV bytes: first 16 bytes/characters of the source constant
    `Utility.IVKEY`, encoded as bytes.
-   Plaintext encoding: UTF-8.
-   Ciphertext representation: standard Base64.
-   The APK contains fixed encryption parameters. This is
    obfuscation/transport formatting, not a robust security boundary;
    AES-CBC with a fixed IV does not provide authenticated integrity.
-   Keep the source constants out of the docs, logs, Git history, and
    agent prompts. Load local values from environment variables or an
    untracked local configuration file.

**Compatibility note:** The Android implementation uses
`AES/CBC/PKCS7Padding` and Android Base64. Python's
`cryptography`/PyCryptodome PKCS#7 padding and standard Base64 matched
the successful HomeData request. Preserve the observed JSON property
names and login serialization order until verified otherwise.

### 3.3 Authentication

-   Login request: no bearer token is sent.
-   Login response: `Data.AuthToken` contains a JWT-like bearer token.
-   HomeData request: send `Authorization: Bearer <AuthToken>`.
-   A `RefreshToken` property is returned, but the observed successful
    login response had an empty string. Do not assume refresh-token
    renewal works.
-   Token expiry, renewal semantics, and server-side session
    invalidation have not been fully verified.
-   Never persist or print the bearer token unless strictly necessary;
    prefer in-memory use only.

### 3.4 HTTP and response handling

Confirmed calls use `POST`, JSON content type, and JSON responses. The
response envelope commonly includes:

``` json
{
  "Status": "Success",
  "Message": "Data Available",
  "Data": {}
}
```

Do not treat HTTP 200 alone as application success. Check the API's
`Status` and the presence/type of `Data`, while tolerating undocumented
response variations.

## 4. Confirmed endpoints

### 4.1 `POST /api/Dashboard/login`

**Evidence:** Confirmed live; request structure confirmed in source.

**Authentication:** No bearer token.

**Request body:**

``` json
{
  "InputType": "<encrypted JSON payload>"
}
```

**Decrypted payload fields** (`LoginInput` DTO):

  -----------------------------------------------------------------------
  Field                   Type                    Notes
  ----------------------- ----------------------- -----------------------
  `Access`                string                  Normal app login path
                                                  sets `"Live"`.

  `FCMID`                 string                  Firebase Cloud
                                                  Messaging token; source
                                                  session manager
                                                  defaults to empty
                                                  string if none is
                                                  available.

  `Password`              string                  User-provided password.

  `UserName`              string                  ELNet login username.
  -----------------------------------------------------------------------

The source serializes a `LoginInput` object with Gson. Keep field names
exact. Do not include actual account credentials in examples, fixtures,
logs, or tests.

**Successful response:** `Data.AuthToken` is used as the bearer token.
`Data` was also observed to contain account/flat metadata such as
`UserName`, `FlatId`, `UserID`, `FlatName`, `Access`, and
contact/address fields. Treat all such fields as private and do not
persist more than the MVP needs.

**Example response shape with sensitive values removed:**

``` json
{
  "Status": "Success",
  "Message": "Data Available",
  "Data": {
    "RefreshToken": "",
    "AuthToken": "<redacted>",
    "UserName": "<redacted>",
    "FlatId": 0,
    "UserID": 0,
    "Access": "User"
  }
}
```

**Failure handling:** If `Status` is not `"Success"` (case-insensitive)
or `Data.AuthToken` is missing, stop the run and report a redacted
error. Never print the raw response if it may contain a token or
personal information.

### 4.2 `POST /api/Dashboard/HomeData`

**Evidence:** Confirmed live; request DTO and source call confirmed.

**Authentication:** `Authorization: Bearer <AuthToken>`.

**Request body:**

``` json
{
  "InputType": "<encrypted JSON payload>"
}
```

**Decrypted payload** (`HomeDataInput` DTO):

``` json
{
  "MeterID": 0
}
```

`MeterID` is an integer populated from the app's stored flat ID
(`sessionManager.getFlatId()`). The login response exposes a `FlatId`
value; the observed client used that flat ID as `MeterID`.

**Observed successful response fields:**

  -----------------------------------------------------------------------
  Field                   Observed type           Interpretation / caveat
  ----------------------- ----------------------- -----------------------
  `DeviceId`              number                  Identifier returned for
                                                  the meter/device;
                                                  observed equal to the
                                                  requested flat ID in
                                                  one response. Do not
                                                  assume equality for
                                                  every installation.

  `kWhRecToday`           number                  Today's
                                                  recorded/imported
                                                  energy reading or usage
                                                  as labelled by app
                                                  data. Validate
                                                  unit/semantics against
                                                  app UI.

  `kvahRecToday`          number                  Today's recorded kVAh
                                                  value.

  `kWhDelToday`           number                  Today's
                                                  delivered/exported kWh
                                                  field.

  `SolarToday`            number                  Solar field for today.

  `kvahDelToday`          number                  Delivered/exported kVAh
                                                  field.

  `kWhRecMonth`           number                  Month-to-date
                                                  recorded/imported kWh
                                                  field.

  `kvahRecMonth`          number                  Month-to-date
                                                  recorded/imported kVAh
                                                  field.

  `kWhDelMonth`           number                  Month-to-date
                                                  delivered/exported kWh
                                                  field.

  `SolarMonth`            number                  Month-to-date solar
                                                  field.

  `kvahDelMonth`          number                  Month-to-date
                                                  delivered/exported kVAh
                                                  field.

  `MeterBal`              number                  Meter balance;
                                                  currency/unit inferred
                                                  from app context and
                                                  must be confirmed in
                                                  UI.

  `RelStatus`             string                  Observed `"ON"`; relay
                                                  status. Display only.

  `EBDGStatus`            number                  Active power source:
                                                  `0` = Main Source / Grid (EB)
                                                  (`"Running on Main Source"`),
                                                  `1` = Generator (DG)
                                                  (`"Running on DG"`),
                                                  `null` / other = Unknown / unavailable.
                                                  Confirmed in `HomeUserFragment.java`
                                                  lines 1384–1403.

  `kwh_kvah_Status`       number                  Status code; mapping
                                                  not yet known.

  `CurrentDay_EB`         number                  Appears to be
                                                  current-day EB
                                                  charge/amount based on
                                                  naming and app context;
                                                  confirm semantics.

  `CurrentDay_DG`         number                  Appears to be
                                                  current-day DG
                                                  charge/amount; confirm
                                                  semantics.

  `CurrentDay_Solar`      number                  Appears to be
                                                  current-day solar
                                                  amount/charge; confirm
                                                  semantics.

  `FixedChargesToday`     number                  Fixed-charge amount for
                                                  today, if applicable.

  `CurrentMonth_EB`       number                  Appears to be
                                                  month-to-date EB
                                                  charge/amount; confirm
                                                  semantics.

  `CurrentMonth_DG`       number                  Appears to be
                                                  month-to-date DG
                                                  charge/amount; confirm
                                                  semantics.

  `CurrentMonth_Solar`    number                  Appears to be
                                                  month-to-date solar
                                                  amount/charge; confirm
                                                  semantics.

  `FixedChargesMonth`     number                  Month-to-date
                                                  fixed-charge amount, if
                                                  applicable.

  `Meter_SN`              string                  Meter serial number;
                                                  sensitive operational
                                                  identifier.

  `IsCoilEnable`          boolean                 Feature/status flag;
                                                  exact meaning not yet
                                                  established.

  `IsEB`                  boolean                 Indicates EB
                                                  feature/source is
                                                  enabled.

  `IsDG`                  boolean                 Indicates DG
                                                  feature/source is
                                                  enabled.

  `IsSolar`               boolean                 Indicates solar
                                                  feature/source is
                                                  enabled.

  `IsFixedCharge`         boolean                 Indicates fixed charges
                                                  are enabled.
  -----------------------------------------------------------------------

The fields above are an observed response schema, not a guarantee that
every response includes every field. Preserve unknown fields in a raw,
local-only diagnostic mode only if necessary; redact identifiers and
never store tokens.

**Successful response example with values removed:**

``` json
{
  "Status": "Success",
  "Message": "Data Available",
  "Data": {
    "DeviceId": 0,
    "kWhRecToday": 0.0,
    "kWhRecMonth": 0.0,
    "MeterBal": 0.0,
    "RelStatus": "ON",
    "CurrentDay_EB": 0.0,
    "CurrentMonth_EB": 0.0,
    "IsEB": true,
    "IsDG": true,
    "IsSolar": false
  }
}
```

## 5. Additional routes discovered in `APIService.java`

The following routes were identified in the decompiled service/route
inventory. Unless otherwise marked, treat them as **source-discovered,
not live-verified**. The exact request DTO, body fields, response
fields, and availability must be checked in `APIService.java` and the
corresponding DTOs before use.

  -----------------------------------------------------------------------------------------
  Route suffix                              Likely purpose from     Evidence / MVP decision
                                            name                    
  ----------------------------------------- ----------------------- -----------------------
  `/api/Dashboard/RefreshToken`             Refresh authentication  Source-discovered.
                                            token                   `RefreshTokenInput`
                                                                    exists; a
                                                                    captured/observed
                                                                    response had an empty
                                                                    refresh-token value. Do
                                                                    not rely on it until
                                                                    tested safely.

  `/api/Dashboard/GetLiveUpdates`           Live updates            **Confirmed live.**
                                                                    Payload: `{"MeterID": flatId, "UserId": userId}`.
                                                                    Returns `PresentLoad` (real-time kW load),
                                                                    `Supply`, `Balance`, `EB`, `DG`,
                                                                    `SanctionEB`, `SanctionDG`, `UpdatedOn`.

  `/api/Dashboard/GetLiveBalance`           Live balance            **Confirmed live.**
                                                                    Payload: `{"MeterID": flatId, "UserId": userId}`.
                                                                    Returns `Balance` (current credit amount).

  `/api/Dashboard/GetDashboardDetails`      Dashboard               **Confirmed live.**
                                            summary/details         Payload: `{"MeterID": flatId, "UserId": userId}`.
                                                                    Returns `AvgEB`, `AvgDG`, `AvgEnergy`,
                                                                    `AvgCost`, `Balance`, `ExpRechargeDays`.

  `/api/Dashboard/GetEnergyDashboard`       Energy dashboard data   Source-discovered;
                                                                    read-only candidate.

  `/api/Dashboard/GetComparativeAnalysis`   Comparative             Source-discovered;
                                            energy/usage analysis   read-only candidate.

  `/api/Dashboard/GetReports`               Reports                 Source-discovered;
                                                                    read-only candidate.

  `/api/Dashboard/RechargeHistory`          Recharge history        **Confirmed live.**
                                                                    Payload: `{"MeterID": flatId, "Month": 0, "Year": 2026}`.
                                                                    Returns `TotalRecharge` and `RechargeHistory` array
                                                                    (`DateTime`, `Amount`, `Status`, `SerialNo`).

  `/api/Dashboard/BillingDetails`           Billing details         Source-discovered;
                                                                    read-only candidate.

  `/api/Dashboard/GetBillsGoals`            Bills/goals             Source-discovered;
                                                                    likely read-only
                                                                    retrieval, confirm.

  `/api/Dashboard/SlabDetails`              Tariff slab details     Source-discovered;
                                                                    read-only candidate.

  `/api/Dashboard/TariffMaster`             Tariff master data      Source-discovered;
                                                                    read-only candidate.

  `/api/Dashboard/GetFlatDetails`           Flat details            Source-discovered;
                                                                    read-only candidate;
                                                                    minimize personal data.

  `/api/Dashboard/GetResourcesDetails`      Meter resources/source  Source-discovered;
                                            details                 read-only candidate.

  `/api/Dashboard/GetNotifications`         Notifications           Source-discovered;
                                                                    read-only candidate.

  `/api/Dashboard/GetComplaintsHistory`     Complaint history       Source-discovered;
                                                                    read-only candidate.

  `/api/Dashboard/GetLowBalanceInfo`        Low-balance             Source-discovered;
                                            settings/information    read-only candidate.

  `/api/Dashboard/UpdatePassword`           Change password         State-changing;
                                                                    excluded.
  -----------------------------------------------------------------------------------------

The source also contains or references these additional operations. Do
not invoke them from the MVP:

  Operation / route name found in source   Reason for exclusion
  ---------------------------------------- ------------------------------------------
  `RelayOnOff`                             Changes relay/meter state.
  `CreateOrder`                            Creates an order/payment-related record.
  `CreateOrderRazorPay`                    Creates a payment/order flow.
  `UpdatePassword`                         Changes account credentials.
  `ProfileUpdate`                          Changes account profile.
  `SaveGoalsAmount`                        Changes a goal/amount setting.
  `UpdateComplaints`                       Creates or changes complaint data.

Exact route suffixes, HTTP methods, payload schemas, and response types
for these operations must be read from the service declaration before
documenting further. This document intentionally does not guess them.

## 6. Other DTO and call-chain findings

These are source-derived and useful for further read-only discovery:

-   `HomeDataInput` contains one field: `MeterID` (integer).
-   `UserInput` contains `UserId` and `MeterID`. It is used by source
    call chains for dashboard/live data fragments; exact route/payload
    pairings should be verified from `APIService.java`.
-   `TrendUserInput` is used by a trending fragment and includes
    user/meter identifiers plus range/type/date-related fields; inspect
    the actual class for exact serialized names and types.
-   `LoginInput` fields are `Access`, `FCMID`, `Password`, and
    `UserName`.
-   The Home screen source schedules a runnable with a 60,000 ms delay.
    This suggests a 60-second refresh cadence, but the full lifecycle
    and endpoint used for each poll were not fully established. MVP may
    use a configurable 60-second polling interval for its own `HomeData`
    snapshots, subject to reasonable rate limiting.

## 7. Recommended MVP API contract

Implement only:

1.  `POST /api/Dashboard/login` --- once at startup or when
    authentication is needed.
2.  `POST /api/Dashboard/HomeData` --- read-only polling with the
    `MeterID` derived from login response `Data.FlatId` unless source
    evidence requires otherwise.

Do not expose the upstream endpoint directly to the browser. Browser
calls should hit the local application server; the server performs
upstream requests and returns a deliberately reduced data model.

Suggested internal normalized model:

``` json
{
  "observed_at": "2026-10-10T12:00:00Z",
  "meter_id": 0,
  "balance": 0.0,
  "relay_status": "ON",
  "today": {
    "kwh_recorded": 0.0,
    "eb_amount": 0.0,
    "dg_amount": 0.0,
    "solar_amount": 0.0,
    "fixed_charges": 0.0
  },
  "month": {
    "kwh_recorded": 0.0,
    "eb_amount": 0.0,
    "dg_amount": 0.0,
    "solar_amount": 0.0,
    "fixed_charges": 0.0
  },
  "sources_enabled": {
    "eb": true,
    "dg": true,
    "solar": false,
    "fixed_charges": false
  }
}
```

This is an internal application schema, not an upstream API payload. Use
explicit field mapping and retain upstream names only in the adapter
layer.

## 8. Investigation checklist for future endpoints

For each candidate endpoint:

1.  Locate its declaration in `APIService.java` and record HTTP method,
    path, headers, and parameter annotations.
2.  Identify the request DTO and response DTO classes.
3.  Trace the Android call site to see how it populates the DTO.
4.  Confirm whether the request is encrypted in `InputType` or sent as a
    plain DTO.
5.  Only exercise endpoints that are read-only and needed for the
    dashboard.
6.  Capture a successful request and response with all tokens,
    credentials, contact details, and identifiers redacted.
7.  Document observed values and unknown enum/status mappings
    separately.
8.  Add a regression fixture containing synthetic data, not a real
    account capture.

## 9. Explicit non-goals

-   No meter relay control.
-   No payment, recharge, order creation, or checkout.
-   No profile/password changes or complaint submissions.
-   No bypassing authorization or accessing other flats/meters.
-   No public-facing proxy or multi-user credential service.
-   No attempt to treat the fixed APK encryption values as a security
    mechanism.
