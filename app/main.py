"""应用入口：python -m app.main 或 uvicorn app.main:app --port 8000"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import api, config, db, kb

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


def create_app(db_path: str | None = None, seed_faq: bool = True) -> FastAPI:
    app = FastAPI(
        title="星驰汽车 4S 店 AI 客服工作台",
        version="0.1.0",
        description="FAQ 知识库 + 检索增强问答 + 人工工单 + 运营看板 + 知识回写闭环",
    )
    app.state.db_path = db_path or config.DB_PATH

    conn = db.connect(app.state.db_path)
    db.init_db(conn)
    if seed_faq and conn.execute("SELECT COUNT(*) FROM faq").fetchone()[0] == 0:
        kb.seed_from_file(conn, config.FAQ_FILE)
    conn.close()

    app.include_router(api.router)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
