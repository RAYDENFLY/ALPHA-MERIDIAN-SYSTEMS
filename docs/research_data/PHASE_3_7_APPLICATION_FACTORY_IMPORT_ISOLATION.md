# Phase 3.7 — Application Import & Intelligence API Test Isolation

## Diagnosis

The prior timeout occurred during pytest collection because `tests/test_intelligence_api.py` imported `src.api.app` at module scope. Importing the application performs substantial module-level construction, including database/schema recovery and paper execution state recovery. A bounded `python -X importtime -c "import src.api.app"` diagnostic completed successfully in approximately 7.7 seconds and emitted recovery logs. It did not show a scheduler starting or a live network request.

The immediate cause was eager test-time application import combined with heavy module initialization, not a proven live exchange request or background polling loop.

## Isolation change

The test now imports the application inside its fixture, avoiding application initialization during collection. The market-context endpoint test overrides only `market_context_service.get_market_context` with a deterministic fixture object. The real FastAPI route, request parsing, response serialization, and schema assertions remain exercised.

No application factory was introduced because the current target was isolated safely at the test dependency boundary without changing production lifecycle behavior.

## Results

- Target intelligence API test: passed in 8.01 seconds.
- Combined target intelligence, research, and Market Participant suite: `13 passed`.
- JavaScript syntax validation: passed.
- Python compilation: passed.

The complete legacy `tests/test_intelligence_api.py` file still times out because its other endpoint tests invoke unmocked runtime market-data paths. Those tests remain unresolved and were not disabled or falsely marked as passing.

## Production verification and boundaries

The production application entry point and routes were not changed. Import diagnostics showed existing initialization behavior remains intact. No live exchange stress tests were performed. No Astel, Model A/B, OOS, strategy, execution, risk, journal, or paper-trading behavior was changed.
