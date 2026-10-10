# Electro — Product Roadmap & Future Enhancements

**Status:** Proposed roadmap  
**Purpose:** Product direction and implementation guidance for future enhancements  
**Scope:** Personal, single-user, self-hosted ELNet electricity monitoring on macOS

---

## 1. Product Vision

Electro should evolve from a reliable telemetry dashboard into a personal energy-intelligence tool.

It should help answer:

- What electricity source is active now: Grid/Main Source or DG?
- How fresh and trustworthy is the latest reading?
- What happened to the electricity supply today?
- How much energy have I used, and how has usage changed?
- How quickly is my balance depleting?
- Is there anything that needs my attention?

The guiding principle is:

> Build features around trustworthy measurements, not around the number of cards on the screen.

The collection service and historical data are the foundation. Future analytics must be derived from validated, persisted observations, with uncertainty and missing data represented honestly.

## 2. Product Principles and Constraints

Preserve the current product direction unless there is a clear, documented reason to change it.

- **Single user:** Electro is a personal application, not a multi-tenant service.
- **Self-hosted:** It runs on the user's Mac.
- **Private by default:** Keep the service local-only and bind to `127.0.0.1` by default.
- **Simple stack:** Python/Flask, SQLite, HTML, Tailwind CSS, and vanilla JavaScript.
- **No unnecessary infrastructure:** Do not introduce React, Docker, Redis, message brokers, cloud databases, or hosted services without a concrete need.
- **Capture independent of the browser:** The background collector must continue when the dashboard is closed.
- **Read-only upstream integration:** Do not add ELNet relay control, recharge/order creation, password/profile updates, or other state-changing upstream operations.
- **Honest analytics:** Distinguish upstream-reported values from derived estimates. Never imply greater precision than the data supports.
- **Incremental delivery:** Prefer small, testable changes over broad rewrites.

## 3. Roadmap Overview

### Phase 1 — Trustworthy Dashboard

**Priority:** Highest  
**Goal:** Make current readings and historical information easy to understand and trust.

Planned work:

- Redesign the overview around current source, balance, daily metrics, and capture freshness.
- Show the last successful capture timestamp and data age.
- Make collector health and telemetry freshness visible without overwhelming the interface.
- Build a Grid/DG/Unknown source-transition timeline from persisted observations.
- Record transitions only when valid observed source values change.
- Make missing-data gaps visible instead of filling them with assumed source states.
- Audit labels so upstream-reported, cumulative, and derived values are clearly distinguished.
- Verify that every chart uses real persisted SQLite data, not demo, seeded, or hard-coded values.
- Add useful loading, empty, stale, and error states.
- Preserve the selected chart range and avoid flashing/resetting charts during background updates.

**Acceptance criteria:**

- A user can identify the current reported source and the freshness of the data at a glance.
- The dashboard clearly indicates when data is stale or unavailable.
- Source transitions and gaps are grounded in persisted timestamps.
- Metric names and units accurately describe the underlying values.
- Historical charts remain usable when the browser refreshes or new samples arrive.

### Phase 2 — Energy and Supply Analytics

**Priority:** High, after Phase 1  
**Goal:** Explain what happened over time.

Planned work:

- Daily consumption summaries and day-over-day comparisons.
- Grid/DG transition counts and observed-runtime estimates.
- Hour-of-day and day-of-week patterns when enough valid history exists.
- Data coverage and missing-observation summaries.
- Date-range filtering for 24 hours, 7 days, 30 days, and longer ranges where useful.
- CSV export of historical snapshots and selected summaries.
- A chronological event history for source changes, connectivity failures, recovery, and service restarts.

**Calculation and data-quality requirements:**

- Calculate consumption from differences between compatible meter readings, not by treating cumulative totals as interval usage.
- Handle meter resets, counter rollovers, invalid readings, and incompatible units explicitly.
- Estimate source duration from adjacent observations, not simply from snapshot counts multiplied by the nominal polling interval.
- Attribute elapsed time to a source only when adjacent observations and the time gap justify that inference.
- Treat long gaps and unknown source codes as unknown time.
- Make it clear that a source transition occurred somewhere between observations; do not claim an exact switching time unless the upstream data supplies it.
- Compare equivalent periods when producing daily or monthly comparisons.
- Do not show precise-looking totals when data coverage is inadequate.

**Acceptance criteria:**

- Every analytical value has a documented calculation and unit.
- Gaps, resets, and invalid observations cannot silently create misleading consumption or runtime values.
- Analytics are reproducible from the persisted data.

### Phase 3 — Balance Intelligence

**Priority:** Medium, after sufficient valid history exists  
**Goal:** Help anticipate a low balance.

Planned work:

- Historical balance depletion over time.
- Average and recent depletion rates.
- An estimate of days until a configurable low-balance threshold.
- Low-balance warnings with an explicit confidence or uncertainty range.
- An explanation of why a forecast changed.
- A clear indication when there is insufficient history to forecast responsibly.

**Requirements:**

- Do not assume every balance change represents electricity consumption; validate the relationship between balance, tariff, fixed charges, and other charges first.
- Distinguish observed balance changes from inferred consumption.
- Use recent and historical trends carefully; identify the window used for a forecast.
- Avoid a forecast when readings are sparse, contradictory, or affected by unexplained adjustments.
- Present estimates as estimates, not guarantees.

**Acceptance criteria:**

- A forecast shows its assumptions, data window, and limitations.
- Insufficient or poor-quality history produces an explicit “not enough data” state rather than a fabricated estimate.
- Forecasts can be compared against subsequent actual observations.

### Phase 4 — Alerts and Operational Polish

**Priority:** Medium, after core analytics are reliable  
**Goal:** Make Electro useful without requiring frequent manual checking.

Potential features:

- Local macOS notifications for stale telemetry.
- Configurable low-balance alerts.
- Optional daily summaries.
- Configurable thresholds and notification quiet hours.
- Automated database backups and a documented restore procedure.
- A measured database retention or aggregation policy.
- Bounded log retention or rotation.
- A settings page for polling interval, freshness threshold, balance threshold, and notification preferences.

**Requirements:**

- Notifications must not fire repeatedly for the same unresolved condition.
- Recovery should clear or resolve the corresponding alert.
- Settings must be validated and their effects documented.
- Backups must be restorable and must not expose credentials or unnecessary sensitive data.
- Do not delete historical data automatically without an explicit retention policy.

## 4. Proposed Information Architecture

Use a compact navigation with four primary views.

### Overview

A glanceable summary:

- Current reported source: Grid/Main Source, DG, or Unknown.
- Current balance, with currency and source timestamp.
- Today's energy metrics, but only where the metric semantics have been validated.
- Last successful capture and freshness.
- A compact source timeline.
- A small recent-events list.
- Collector health presented separately from the upstream telemetry state.

### Analytics

- Energy-consumption trends.
- Daily and monthly comparisons.
- Source duration and transition counts.
- Time-of-day and day-of-week patterns when history supports them.
- Clear chart units, timestamps, and missing-data gaps.
- Date-range controls such as 24h, 7d, and 30d.

### Supply

- Grid/DG/Unknown timeline.
- Source transitions with previous and newly observed states.
- Estimated duration and data-coverage confidence.
- Connectivity interruptions and recovery events.
- Clear distinction between relay status and the reported electricity source.

### History

- Chronological snapshots and events.
- Filtering by date and event type.
- Inspection of a reading's timestamp and relevant raw/reported values.
- CSV export.
- Data-quality and missing-observation indicators.

A Settings view may be added when operational controls are implemented; it should not displace the four core views until there is enough functionality to justify it.

## 5. UI and Interaction Guidelines

- Put the current reported source first in the visual hierarchy.
- Make balance and validated daily metrics easy to scan.
- Use one primary chart at a time where possible; avoid overcrowding the overview.
- Use clear units and labels for every metric.
- Do not rely on red/green alone to communicate state; include text and accessible icons.
- Use responsive layouts: a compact multi-column desktop layout and a single- or two-column mobile layout.
- Preserve chart selection and scroll position during background updates.
- Update changing values in place; avoid full-page refreshes and chart flicker.
- Show an initial loading state only when data is actually loading.
- Provide clear empty, stale, unavailable, and error states.
- Make chart tooltips show timestamps and values.
- Keep destructive operations, data deletion, and database maintenance away from routine dashboard controls.
- Keep the interface visually quiet when the system is healthy.

## 6. Metric Definitions and Data Contracts

Before implementing analytics, maintain a small metric dictionary documenting:

- Display name and unit.
- Upstream field or source table.
- Whether the value is directly reported or derived.
- Calculation formula, if derived.
- Required data and minimum coverage.
- Handling of nulls, resets, outliers, and long gaps.
- Timestamp semantics.
- Known limitations.

Existing ELNet fields that require semantic validation should not be given stronger labels than the evidence supports. In particular:

- Distinguish cumulative meter readings from interval consumption.
- Validate the meanings of energy and charge fields before calculating costs.
- Keep `EBDGStatus` separate from relay status.
- Do not treat unknown source values as confirmed Grid or DG.
- Do not equate the response-received timestamp with the meter's internal measurement timestamp.

## 7. Explicitly Deferred Work

Do not prioritize these until a concrete need exists:

- AI-generated insights before metrics are validated.
- Complex forecasting before adequate history is available.
- A native iOS application; use the responsive web dashboard first.
- Multi-user or multi-meter architecture without a confirmed requirement.
- A frontend-framework migration.
- Streaming infrastructure when polling meets the actual needs.
- A complex tariff/cost calculator before the relevant tariff, fixed-charge, and energy fields are validated.
- New infrastructure components without evidence that the current design is insufficient.

## 8. Suggested First Release: Source Intelligence & Analytics Foundation

The next focused release should implement:

1. Overview redesign around current source, balance, validated daily metrics, and freshness.
2. A source-transition timeline built from persisted observations.
3. Correct labels and units for the existing energy charts.
4. Daily consumption and source-duration summaries only where supported by validated readings.
5. Visible missing-data gaps and basic data-coverage indicators.
6. CSV export for historical snapshots.
7. Responsive behavior verified on desktop and iPhone Safari.

### Release success criteria

A user opening Electro for approximately ten seconds should be able to answer:

- What source is currently reported?
- How fresh is the latest reading?
- What has happened today?
- What energy metrics are reliable enough to interpret?
- How much history and data coverage support these conclusions?

## 9. Definition of Done for Every Enhancement

Every feature should:

- Have a clear user benefit and a bounded scope.
- Use persisted real data, not fabricated or demo readings.
- Define metric semantics and units.
- Handle missing, stale, invalid, and insufficient data.
- Include appropriate automated tests.
- Preserve the local-only security model and read-only upstream boundary.
- Avoid unnecessary dependencies and architectural changes.
- Include documentation for new configuration, calculations, or operational behavior.
- Be validated on the responsive UI where relevant.
- State any assumptions and known limitations.

## 10. Guidance for Coding Agents

When implementing this roadmap:

1. Inspect the current repository and documentation before making changes.
2. Treat this document as future product direction, not a requirement to implement every phase at once.
3. Identify the smallest useful next increment and its dependencies.
4. Verify upstream field semantics and existing database schema before deriving new metrics.
5. Prefer additive, backward-compatible changes.
6. Do not rewrite the working collector or its lifecycle machinery unless a concrete defect requires it.
7. Do not introduce new dependencies or services without explaining why existing components are insufficient.
8. Add tests for calculations, missing data, source transitions, timestamps, and UI/API behavior as applicable.
9. Run the relevant test suite and report actual results.
10. Update the README and other documentation to link to this roadmap, but avoid duplicating the full roadmap across multiple documents.
11. Do not claim that an enhancement is complete or tested unless it was actually implemented and verified.

---

## Decision Rule

Prioritize work in this order:

1. Correctness and trustworthiness of captured data.
2. Clear current status and historical source transitions.
3. Useful energy and supply analytics.
4. Balance forecasting after sufficient history.
5. Alerts, backups, and operational polish.
6. Optional advanced features only when a concrete need is demonstrated.
