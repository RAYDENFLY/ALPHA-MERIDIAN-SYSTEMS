from pathlib import Path


def test_forward_validation_badge_uses_compact_label_and_preserves_raw_status():
    js = Path("src/static/js/positions.js").read_text(encoding="utf-8")
    assert "UNPROVEN_NO_COMPLETED_FORWARD_TRADES': 'UNPROVEN'" in js
    assert "fvBadge.title = fullStatus" in js
    assert "fvBadge.dataset.fullStatus = fullStatus" in js
    assert "max-w-[100px]" in js


def test_forward_validation_card_keeps_progress_separate_and_responsive():
    html = Path("src/api/dashboard.html").read_text(encoding="utf-8")
    start = html.index('id="forwardValidationBadge"')
    card = html[start:html.index('</div>', start) + 6]
    assert 'id="paperTradesCompletedCount"' in html
    assert 'id="forwardValidationNote"' in html
    assert 'max-w-[100px]' in card
