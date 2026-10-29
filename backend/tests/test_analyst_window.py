"""Analyst window inference: last_days read from the task wording when the
plan did not pass it explicitly (regression: live reviewer rejected analyses
because 'последние 30 дней' was silently ignored and window stayed null)."""

from app.agents.analyst import _infer_last_days


def test_infers_window_from_russian_goal():
    assert _infer_last_days("Проанализируй продажи за последние 30 дней") == 30


def test_infers_window_from_english_goal():
    assert _infer_last_days("Analyze the last 30 days of sales") == 30
    assert _infer_last_days("metrics for 90 days of orders") == 90


def test_no_window_mentioned_returns_none():
    assert _infer_last_days("Проанализируй все продажи по товарам") is None
    assert _infer_last_days("") is None
    assert _infer_last_days(None) is None


def test_absurd_values_rejected():
    assert _infer_last_days("last 99999 days") is None
