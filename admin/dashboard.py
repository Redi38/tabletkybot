"""
Dashboard and statistics: the main charts page and its JSON data endpoint.
"""

from starlette.requests import Request

from admin.app import SessionLocal, admin, app
from database import crud


@app.get("/admin/dashboard")
async def admin_dashboard(request: Request):
    """Renders the main dashboard page with charts."""
    async with SessionLocal() as session:
        stats = await crud.get_global_intake_stats(session)

    return await admin.templates.TemplateResponse(
        request,
        "sqladmin/index.html",
        context={"stats": stats},
    )


@app.get("/api/admin/stats")
async def get_admin_stats(period: str = "all"):
    """API endpoint that returns dynamic JSON for the charts depending on the selected period."""
    async with SessionLocal() as session:
        stats = await crud.get_dashboard_stats(session, period)
        return stats
