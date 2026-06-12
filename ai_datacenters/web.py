"""FastAPI app for ai-datacenters: project table + on-demand Grok check buttons."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, db, grok

log = logging.getLogger("ai_datacenters.web")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(PROJECT_ROOT / "web" / "templates"))


def create_app() -> FastAPI:
    cfg = config.load()
    db_p = config.db_path(cfg)
    db.init(db_p)

    app = FastAPI(title="AI Datacenters", docs_url=None, redoc_url=None)
    app.state.cfg = cfg
    app.state.db_path = db_p

    static_dir = PROJECT_ROOT / "web" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        with db.connect(app.state.db_path) as conn:
            projects = db.list_projects(conn)
        return TEMPLATES.TemplateResponse(
            "index.html", {"request": request, "projects": projects}
        )

    @app.get("/api/projects")
    def api_projects() -> JSONResponse:
        with db.connect(app.state.db_path) as conn:
            return JSONResponse(db.list_projects(conn))

    @app.post("/api/discover")
    def api_discover() -> JSONResponse:
        try:
            result = grok.discover_projects(
                app.state.cfg, existing_slugs=_existing_slugs(app.state.db_path)
            )
        except grok.GrokError as e:
            raise HTTPException(status_code=502, detail=str(e))

        added = 0
        with db.connect(app.state.db_path) as conn:
            for p in result["projects"]:
                slug = (p.get("slug") or "").strip().lower()
                if not slug or not p.get("name"):
                    continue
                _, inserted = db.upsert_project(conn, {**p, "slug": slug})
                if inserted:
                    added += 1
            db.record_discovery(
                conn,
                prompt=result["prompt"],
                response_text=result["text"],
                citations=result["citations"],
                projects_added=added,
                model=result["model"],
                tokens_in=result["tokens_in"],
                tokens_out=result["tokens_out"],
                cost_usd=result["cost_usd"],
            )
            projects = db.list_projects(conn)
        return JSONResponse(
            {
                "added": added,
                "total": len(projects),
                "cost_usd": result["cost_usd"],
                "projects": projects,
            }
        )

    @app.post("/api/check/{slug}")
    def api_check(slug: str) -> JSONResponse:
        with db.connect(app.state.db_path) as conn:
            project = db.get_project(conn, slug)
        if not project:
            raise HTTPException(status_code=404, detail=f"no project with slug {slug!r}")

        try:
            result = grok.check_project(app.state.cfg, project, date.today().isoformat())
        except grok.GrokError as e:
            raise HTTPException(status_code=502, detail=str(e))

        parsed = result.get("parsed") or {}
        new_status = parsed.get("new_status") if isinstance(parsed, dict) else None
        summary = parsed.get("summary") if isinstance(parsed, dict) else None

        with db.connect(app.state.db_path) as conn:
            db.record_check(
                conn,
                project_id=project["id"],
                prompt=result["prompt"],
                response_text=result["text"],
                citations=result["citations"],
                parsed=parsed if isinstance(parsed, dict) else None,
                model=result["model"],
                tokens_in=result["tokens_in"],
                tokens_out=result["tokens_out"],
                cost_usd=result["cost_usd"],
            )
            db.apply_check_to_project(conn, project["id"], new_status, summary)
            updated = db.get_project(conn, slug)

        return JSONResponse(
            {
                "project": updated,
                "parsed": parsed,
                "citations": result["citations"],
                "cost_usd": result["cost_usd"],
            }
        )

    @app.get("/api/project/{slug}/history")
    def api_history(slug: str) -> JSONResponse:
        with db.connect(app.state.db_path) as conn:
            project = db.get_project(conn, slug)
            if not project:
                raise HTTPException(status_code=404, detail=f"no project {slug!r}")
            checks = db.recent_checks(conn, project["id"], limit=20)
        return JSONResponse({"project": project, "checks": checks})

    return app


def _existing_slugs(db_path: Path) -> list[str]:
    with db.connect(db_path) as conn:
        return [r["slug"] for r in db.list_projects(conn)]


app = create_app()
