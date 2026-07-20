"""
Log viewer: tails the bot's and admin's own log files, with optional
level/search filtering, plus a download endpoint that streams the full
(optionally filtered) file without loading it entirely into memory.
"""

import os
from datetime import datetime

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqladmin import BaseView, expose
from starlette.requests import Request

from admin.app import app

LOG_DIR = os.getenv("LOG_DIR", "/app/logs")
LOG_FILES = {
    "bot": os.path.join(LOG_DIR, "bot.log"),
    "admin": os.path.join(LOG_DIR, "admin.log"),
}

_MAX_LINES = 1000


def _tail_lines(path: str, max_lines: int, chunk_size: int = 65536) -> list[str]:
    """
    Efficiently reads the last max_lines lines of a file WITHOUT loading the
    whole file into memory — reads chunks from the end of the file until
    enough lines have been collected.
    """
    if not os.path.exists(path):
        return []

    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        data = b""
        read_size = 0

        while read_size < file_size and data.count(b"\n") <= max_lines:
            read_size = min(read_size + chunk_size, file_size)
            f.seek(file_size - read_size)
            data = f.read(read_size)

        lines = data.decode("utf-8", errors="replace").splitlines()
        return lines[-max_lines:]


def _log_line_matches(line: str, level: str = "", search: str = "") -> bool:
    """Shared filter predicate used by both the JSON viewer and the download endpoint."""
    if level and f"| {level.upper()} |" not in line:
        return False
    if search and search.lower() not in line.lower():
        return False
    return True


@app.get("/api/admin/logs")
async def get_admin_logs(source: str = "bot", lines: int = 200, level: str = "", search: str = "") -> dict:
    """
    JSON with the latest log lines.
    source: "bot" | "admin"
    lines: how many lines to return (hard-capped at _MAX_LINES)
    level: "" | "INFO" | "WARNING" | "ERROR" — log level filter
    search: arbitrary text to search for (case-insensitive substring)
    """
    path = LOG_FILES.get(source)
    if not path:
        return {"error": "invalid source", "lines": []}

    lines = max(1, min(lines, _MAX_LINES))

    raw_lines = _tail_lines(path, max_lines=lines * 3 if (level or search) else lines)

    if level or search:
        raw_lines = [ln for ln in raw_lines if _log_line_matches(ln, level, search)]

    return {"source": source, "lines": raw_lines[-lines:]}


@app.get("/api/admin/logs/download")
async def download_logs(source: str = "bot", level: str = "", search: str = ""):
    """
    Downloads the full log file (optionally filtered by level/search) as a
    plain text attachment. Unlike /api/admin/logs, this is not capped by
    _MAX_LINES — it streams the whole matching content so nothing is lost
    when investigating an incident.
    """
    path = LOG_FILES.get(source)
    if not path:
        raise HTTPException(status_code=400, detail="Invalid log source")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Log file not found")

    def iter_file():
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            if not level and not search:
                # Whole file, streamed in chunks — avoids loading it all into memory
                while chunk := f.read(65536):
                    yield chunk
            else:
                for line in f:
                    if _log_line_matches(line, level, search):
                        yield line

    suffix_parts = [source]
    if level:
        suffix_parts.append(level.lower())
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"logs_{'_'.join(suffix_parts)}_{timestamp}.log"

    return StreamingResponse(
        iter_file(),
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class LogsView(BaseView):
    """
    Custom page in the Admin Panel sidebar. Data is loaded via JS
    through /api/admin/logs — the page itself only renders the template.
    """

    name = "Logs"
    icon = "fa-solid fa-file-lines"

    @expose("/logs-view", methods=["GET"])
    async def logs_page(self, request: Request):
        return await self.templates.TemplateResponse(
            request,
            "sqladmin/logs.html",
            context={"request": request},
        )
