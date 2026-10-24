# Phase 3.6 — Browser Validation & Intelligence API Test Isolation

## Browser environment

No supported browser automation framework or browser runtime is available in the repository environment. No browser was installed. Therefore no screenshot or visual interaction result is claimed.

Static validation remained possible through DOM assertions and JavaScript syntax checks. Browser-only behavior such as rendered badges, layout, and console errors remains unverified.

## Deterministic validation

The CFTC freshness model is tested at exact 0, 10, 11, 21, and 22 day boundaries, plus missing, malformed, and future timestamps. Freshness is derived from the observation date; retrieval time is not used.

The existing Market Participant integration assertions cover the retail state container, retail methodology/timestamp rendering hooks, weekly CFTC labeling, and null-safe CFTC state handling. Provider responses continue to use mocked tests.

## Intelligence API test diagnosis

`tests/test_intelligence_api.py::TestIntelligenceAPI::test_market_context_endpoint` timed out during pytest collection, before the test body executed. This indicates an import-time application initialization or runtime dependency setup problem rather than an assertion-level endpoint failure. The current test imports the full FastAPI application, whose module-level initialization creates live-capable market-data services.

No production startup behavior was changed to accommodate the test. The test was not disabled or marked as passing. Safe isolation requires a test-app factory or dependency-injection seam around application initialization, which is outside this focused validation change and needs separate approval because it affects the application test boundary.

## Result and boundaries

No live exchange stress tests were performed. No Astel, Model A/B, OOS, strategy, execution, risk, journal, or paper-trading behavior was changed.
