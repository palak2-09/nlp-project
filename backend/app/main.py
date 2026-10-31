from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import Settings, get_settings
from app.db.database import initialize_database
from app.services.graph_store import initialize_graph_store, sync_all_documents


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        initialize_database(application.state.settings)
        initialize_graph_store(application.state.settings)
        sync_all_documents(application.state.settings)
        yield

    application = FastAPI(
        title="PaperGraph AI API",
        description="Document ingestion and source-preserving text extraction for PaperGraph AI.",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = configured
    print("\n========== PAPERGRAPH CONFIG ==========")
    print("Provider:", configured.llm_provider)
    print("Model:", configured.llm_model)
    print("API Key Loaded:", bool(configured.llm_api_key))
    print("=======================================\n")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(configured.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )
    application.include_router(router)
    return application


app = create_app()
