"""
app/utils/period.py
Helpers for calculating the previous week's billing period (Mon–Sun).
"""
from datetime import date, timedelta


def previous_week_period(reference: date | None = None) -> tuple[date, date]:
    today = reference or date.today()
    monday_this_week = today - timedelta(days=today.weekday())
    monday_prev = monday_this_week - timedelta(weeks=1)
    sunday_prev = monday_prev + timedelta(days=6)
    return monday_prev, sunday_prev


def format_period(start: date, end: date) -> str:
    return f"{start.strftime('%d/%m')} a {end.strftime('%d/%m/%Y')}"
