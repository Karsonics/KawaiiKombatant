"""Register core API routes and only the web routes backed by local assets."""

from pathlib import Path


def build_health_payload(engine) -> dict:
    """Build the health response using only public status fields."""
    return {
        "status": "ok",
        "sessions": engine.get_session_count(),
        "tts_enabled": engine.tts_enabled,
        "long_term_memory": engine.markdown_memory_status,
    }


def create_health_handler(engine_provider):
    """Create the async health endpoint around a supplied engine provider."""

    async def health():
        return build_health_payload(engine_provider())

    return health


def register_api_routes(
    app,
    web_dir: Path,
    *,
    static_files_factory,
    logger,
    root_handler,
    settings_handler,
    health_handler,
    websocket_handler,
) -> None:
    """Register routes without requiring optional frontend assets to exist."""
    if web_dir.is_dir():
        app.mount(
            "/static", static_files_factory(directory=str(web_dir)), name="web_static"
        )

        models_dir = web_dir / "models"
        if models_dir.is_dir():
            app.mount(
                "/models",
                static_files_factory(directory=str(models_dir)),
                name="web_models",
            )
        else:
            logger.warning(
                "Live2D models directory not found at %s; /models route disabled",
                models_dir,
            )

        if (web_dir / "index.html").is_file():
            app.get("/")(root_handler)

        if (web_dir / "settings.html").is_file():
            app.get("/settings")(settings_handler)

        logger.info("Web UI static files mounted from %s", web_dir)
    else:
        logger.warning(
            "Web assets directory not found at %s; frontend routes disabled", web_dir
        )
        logger.warning("Live2D models directory not found; /models route disabled")

    app.get("/health")(health_handler)
    app.websocket("/ws")(websocket_handler)
