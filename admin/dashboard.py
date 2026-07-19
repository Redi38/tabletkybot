"""
Dashboard and statistics: the main charts page, its JSON data endpoint, and
the AI usage metrics page + its JSON data endpoint.
"""

from sqladmin import BaseView, expose
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


@app.get("/api/admin/ai-metrics")
async def get_ai_metrics(period: str = "24h"):
    async with SessionLocal() as session:
        summary = await crud.get_ai_metrics_summary(session, period)
        recent = await crud.get_recent_ai_metrics(session, limit=50)
        recent_list = [
            {
                "id": m.id,
                "full_name": full_name,
                "model_used": m.model_used,
                "tool_choice": m.tool_choice,
                "tool_names": m.tool_names or "—",
                "latency_ms": m.latency_ms,
                "status": m.status,
                "created_at": m.created_at.strftime("%d.%m %H:%M:%S"),
            }
            for m, full_name in recent
        ]
        return {"summary": summary, "recent": recent_list}


class AIMetricsView(BaseView):
    name = "AI Metrics"
    icon = "fa-solid fa-chart-line"

    @expose("/admin/ai-metrics-view", methods=["GET"])
    async def ai_metrics_page(self, request: Request):
        return await self.templates.TemplateResponse(
            request,
            "sqladmin/ai_metrics.html",
            context={"request": request},
        )
