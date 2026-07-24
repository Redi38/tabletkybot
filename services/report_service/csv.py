import csv
import io

from locales.texts import DEFAULT_LANG, get_text
from services.report_service.aggregation import _get_clean_status_text, _prepare_and_sort_records


def create_csv_report(
    records: list[tuple], lang: str = DEFAULT_LANG, user_name: str = "", user_tz: str = "Europe/Kyiv"
) -> io.BytesIO:
    """Generates a lightweight CSV report of medicine intake."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Patient information
    writer.writerow([f"{get_text(lang, 'excel_patient')} {user_name}"])
    writer.writerow([])

    # Column headers
    headers = [
        get_text(lang, "excel_h_num"),
        get_text(lang, "excel_h_name"),
        get_text(lang, "excel_h_dose"),
        get_text(lang, "excel_h_date"),
        get_text(lang, "excel_h_time"),
        get_text(lang, "excel_h_status"),
    ]
    writer.writerow(headers)

    # Use the helper function to prepare the data, accounting for timezone
    sorted_records = _prepare_and_sort_records(records, user_tz)

    # Data
    for row_idx, record in enumerate(sorted_records, start=1):
        name, dosage, remaining_days, taken_dt, status = record

        status_text = _get_clean_status_text(status, lang)

        writer.writerow([row_idx, name, dosage, taken_dt.strftime("%d.%m.%Y"), taken_dt.strftime("%H:%M"), status_text])

    buffer = io.BytesIO(output.getvalue().encode("utf-8"))
    buffer.seek(0)
    return buffer
