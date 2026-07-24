from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from locales.texts import get_text


def _prepare_and_sort_records(records: list[tuple], user_tz_str: str) -> list[tuple]:
    """Helper function for parsing dates, converting to local time, and sorting records."""
    parsed_records = []

    try:
        user_tz = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        user_tz = ZoneInfo("Europe/Kyiv")

    for r in records:
        name, dosage, remaining_days, taken_at, status = r

        taken_dt = taken_at if isinstance(taken_at, datetime) else datetime.fromisoformat(str(taken_at))

        if taken_dt.tzinfo is None:
            taken_dt = taken_dt.replace(tzinfo=timezone.utc)

        local_dt = taken_dt.astimezone(user_tz)

        parsed_records.append((name, dosage, remaining_days, local_dt, status))

    # Sort by date descending (newest on top)
    return sorted(parsed_records, key=lambda x: x[3], reverse=True)


def _get_clean_status_text(status: str, lang: str) -> str:
    """Gets the status text and strips emojis from it."""
    raw_status_text = (
        get_text(lang, "excel_status_taken") if status == "taken" else get_text(lang, "excel_status_skipped")
    )

    return raw_status_text.replace("✅", "").replace("❌", "").replace("⏭️", "").strip()


def _aggregate_medicine_stats(sorted_records: list[tuple]) -> list[dict]:
    """
    Shared aggregation: taken/missed counts, % adherence, and the datetime of
    the last taken dose per (name, dosage) pair — computed from already
    timezone-adjusted, sorted records. Used by BOTH the Excel "By medicine"
    sheet and the bot's text /stats summary, so the two can never diverge.
    """
    med_stats: dict[tuple, dict] = {}

    for name, dosage, _, taken_dt, status in sorted_records:
        key = (name, dosage)
        if key not in med_stats:
            med_stats[key] = {"name": name, "dosage": dosage, "taken": 0, "missed": 0, "last_dt": None}
        if status == "taken":
            med_stats[key]["taken"] += 1
            if med_stats[key]["last_dt"] is None or taken_dt > med_stats[key]["last_dt"]:
                med_stats[key]["last_dt"] = taken_dt
        elif status in ("missed", "skipped"):
            med_stats[key]["missed"] += 1

    result = []
    for (name, dosage), data in sorted(med_stats.items(), key=lambda x: x[0][0].lower()):
        taken = data["taken"]
        missed = data["missed"]
        total = taken + missed
        pct = round(taken / total * 100, 1) if total > 0 else 0.0
        result.append(
            {
                "name": name,
                "dosage": dosage,
                "taken": taken,
                "missed": missed,
                "total": total,
                "pct": pct,
                "last_dt": data["last_dt"],
            }
        )
    return result


def get_medicine_stats_summary(records: list[tuple], user_tz: str = "Europe/Kyiv") -> list[dict]:
    """
    Public entry point: takes raw (name, dosage, remaining_days, taken_at,
    status) records — as returned by crud.get_medicine_records_for_report —
    and returns per-medicine stats sorted alphabetically by name. Each item:
    {"name", "dosage", "taken", "missed", "total", "pct", "last_dt"}.

    Used by the bot's /stats text summary. The Excel report's "By medicine"
    sheet reuses the same _aggregate_medicine_stats function internally, so
    both surfaces are guaranteed to show identical numbers.
    """
    sorted_records = _prepare_and_sort_records(records, user_tz)
    return _aggregate_medicine_stats(sorted_records)
