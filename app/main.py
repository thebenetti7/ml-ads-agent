"""
FastAPI application entry point — ML Ads Agent.

Baseado em: PUBLICIDADE-MODULO/skills/ml-ads-orchestrator/app/main.py
Mudancas:
- Imports ajustados para nova estrutura
- CLI args: --dry-run, --conta, --single-cycle
- Injecao de guardrails no dashboard
- Graceful shutdown com signal handlers
"""

import sys
import signal
import asyncio
import argparse
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import uvicorn

from app.config import Config
from app.core.state_manager import StateManager
from app.core.guardrails import GuardrailsManager
from app.core.orchestrator import Orchestrator
from app.core.session_pool import SessionPool
from app.api.vps_client import VPSClient
from app.api.dashboard import router as dashboard_router, set_managers

from app.logging_config import setup_logging

logger = logging.getLogger(__name__)

# Globais
orchestrator_task: asyncio.Task = None
orchestrator: Orchestrator = None
shutdown_event = asyncio.Event()


def parse_args():
    """Parse CLI args."""
    parser = argparse.ArgumentParser(description="ML Ads Agent")
    parser.add_argument("--dry-run", action="store_true", help="Modo simulacao")
    parser.add_argument("--conta", type=int, help="Rodar apenas uma conta")
    parser.add_argument("--single-cycle", action="store_true", help="Executar 1 ciclo e sair")
    parser.add_argument("--port", type=int, default=8888, help="Porta HTTP")
    return parser.parse_args()


async def start_orchestrator(config: Config):
    """Inicia o orchestrator como tarefa de background."""
    global orchestrator_task, orchestrator

    logger.info("Iniciando ML Ads Agent...")

    try:
        # State manager
        state_manager = StateManager(base_dir=config.state_dir)

        # Guardrails
        guardrails = GuardrailsManager(config, state_manager)

        # VPS client
        vps_client = VPSClient(
            base_url=config.ecomfluxo_url,
            api_key=config.ecomfluxo_api_key,
        )

        # Session pool (browser)
        session_pool = SessionPool(config)

        # Orchestrator
        orchestrator = Orchestrator(
            config=config,
            vps_client=vps_client,
            state_manager=state_manager,
            guardrails=guardrails,
            session_pool=session_pool,
            shutdown_event=shutdown_event,
        )

        # Injetar no dashboard
        set_managers(state_manager, config, orchestrator, guardrails)

        # Iniciar loop em background
        orchestrator_task = asyncio.create_task(orchestrator.run())
        logger.info(
            f"Agent iniciado: {len(config.contas)} contas, "
            f"dry_run={config.dry_run}"
        )

    except Exception as e:
        logger.error(f"Erro ao iniciar agent: {e}", exc_info=True)
        raise


async def stop_orchestrator():
    """Para o orchestrator gracefully."""
    global orchestrator_task, orchestrator

    logger.info("Parando agent...")

    if orchestrator:
        await orchestrator.shutdown()

        # Fechar sessoes browser
        if orchestrator.session_pool:
            await orchestrator.session_pool.fechar_todas()

    if orchestrator_task:
        orchestrator_task.cancel()
        try:
            await orchestrator_task
        except asyncio.CancelledError:
            pass

    logger.info("Agent parado")


def create_app(config: Config) -> FastAPI:
    """Cria app FastAPI."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await start_orchestrator(config)
        yield
        await stop_orchestrator()

    app = FastAPI(
        title="ML Ads Agent",
        description="Agente de automacao local para Mercado Ads",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS — localhost + Tailscale
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:8888",
            "http://127.0.0.1:8888",
            "http://100.*",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Router
    app.include_router(dashboard_router, prefix="/api", tags=["dashboard"])

    @app.get("/health", tags=["health"])
    async def health_check():
        return {
            "status": "ok",
            "orchestrator_running": (
                orchestrator_task is not None and not orchestrator_task.done()
            ),
        }

    @app.get("/", response_class=HTMLResponse, tags=["dashboard"])
    async def serve_dashboard():
        dashboard_path = Path(__file__).parent / "static" / "dashboard.html"
        if not dashboard_path.exists():
            return "<h1>Dashboard not found</h1>"
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return f.read()

    return app


def handle_signal(signum, frame):
    """Handler para graceful shutdown."""
    logger.info(f"Recebido sinal {signum}, encerrando...")
    shutdown_event.set()


# ==================== CRIAR APP ====================

def _init_app():
    """Cria app ao ser importado pelo uvicorn."""
    try:
        config = Config.from_file("config.json")
        config.criar_diretorios()
        setup_logging(log_dir=config.log_dir, console=True)
        return create_app(config), config
    except Exception as e:
        logging.error(f"Erro ao criar app: {e}")
        # App minimo para mostrar erro no browser
        fallback = FastAPI(title="ML Ads Agent - ERRO")

        @fallback.get("/")
        async def erro_page():
            return HTMLResponse(
                f"<h1>Erro ao iniciar</h1><pre>{e}</pre>"
                "<p>Verifique config.json e .env</p>"
            )

        @fallback.get("/health")
        async def health():
            return {"status": "error", "message": str(e)}

        return fallback, None


app, _config = _init_app()


def main():
    args = parse_args()

    # Override config se possivel
    if _config:
        if args.dry_run:
            _config.dry_run = True
            logger.info("Modo dry-run ativado via CLI")

        if args.conta:
            if args.conta not in _config.contas:
                logger.error(f"Conta {args.conta} nao encontrada em config.json")
                sys.exit(1)
            _config.contas = [args.conta]
            logger.info(f"Rodando apenas conta {args.conta}")

    # Signal handlers
    signal.signal(signal.SIGINT, handle_signal)
    try:
        signal.signal(signal.SIGTERM, handle_signal)
    except (OSError, AttributeError):
        pass

    # Executar
    logger.info(f"Dashboard: http://localhost:{args.port}")
    logger.info("Para parar: Ctrl+C ou crie arquivo STOP")

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=args.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
