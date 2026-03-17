"""
Configuracao unificada do ML Ads Agent.

Merge de:
- PUBLICIDADE-MODULO/skills/ml-ads-orchestrator/app/config.py
- PUBLICIDADE-MODULO/skills/ml-ads-orchestrator/scripts/orchestrator.py (Config dataclass)
"""

import json
import os
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class ContaInfo:
    """Configuracao por conta ML."""
    margem: float
    roas_breakeven: float
    descricao: str = ""


@dataclass
class Config:
    """Configuracao completa do agente."""

    # === VPS (Ecomfluxo) ===
    ecomfluxo_url: str = ""
    ecomfluxo_api_key: str = ""

    # === Mercado Livre OAuth ===
    ml_client_id: str = ""
    ml_client_secret: str = ""

    # === Contas ativas ===
    contas: list[int] = field(default_factory=list)
    contas_config: dict[int, ContaInfo] = field(default_factory=dict)

    # === Browser ===
    browser_type: str = "camoufox"
    headless: bool = True
    slow_motion_ms: int = 100
    timeout_ms: int = 30000

    # === Operacao ===
    work_blocks: list[tuple[int, int, int, int]] = field(
        default_factory=lambda: [(8, 0, 12, 0), (13, 30, 18, 0), (19, 0, 22, 0)]
    )
    variacao_minutos: int = 30
    timezone: str = "America/Sao_Paulo"
    keep_alive_interval_s: int = 7200  # 2h
    poll_interval_s: int = 90
    sleep_min_s: int = 60
    sleep_max_s: int = 120

    # === Guardrails ===
    max_acoes_dia: int = 50
    max_acoes_hora: int = 20
    max_acoes_ciclo: int = 5
    variacao_budget_max_pct: float = 15.0
    variacao_roas_max_pct: float = 10.0
    roas_min: float = 1.0
    roas_max: float = 35.0
    ram_min_livre_mb: int = 500
    cpu_max_pct: int = 80
    disco_min_livre_mb: int = 1000

    # === Delays entre acoes ===
    entre_acoes_min_ms: int = 3000
    entre_acoes_max_ms: int = 10000
    entre_contas_min_s: int = 180
    entre_contas_max_s: int = 600

    # === Diretorios ===
    state_dir: str = "./state"
    profiles_dir: str = "./profiles"
    screenshot_dir: str = "./screenshots"
    log_dir: str = "./logs"

    # === Flags ===
    dry_run: bool = False

    @classmethod
    def from_file(cls, filepath: str = "config.json") -> "Config":
        """Carrega config de JSON + env vars."""
        config_path = Path(filepath)

        if not config_path.exists():
            raise FileNotFoundError(
                f"Arquivo nao encontrado: {filepath}\n"
                "Copie config.json.example para config.json"
            )

        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Env vars sobrescrevem JSON (secrets nunca no JSON)
        ecomfluxo_url = os.getenv("ECOMFLUXO_URL", "")
        ecomfluxo_api_key = os.getenv("ECOMFLUXO_API_KEY", "")
        ml_client_id = os.getenv("ML_CLIENT_ID", "")
        ml_client_secret = os.getenv("ML_CLIENT_SECRET", "")

        # Validar obrigatorios (avisar mas nao crashar)
        if not ecomfluxo_url:
            logger.warning("ECOMFLUXO_URL nao configurado — modo offline")
            ecomfluxo_url = "http://localhost"
        if not ecomfluxo_api_key:
            logger.warning("ECOMFLUXO_API_KEY nao configurado — modo offline")
            ecomfluxo_api_key = "placeholder"

        contas = data.get("contas", [])
        if not contas:
            raise ValueError("Nenhuma conta configurada em config.json")

        # Parse contas_config
        contas_config = {}
        for conta_id_str, info in data.get("contas_config", {}).items():
            conta_id = int(conta_id_str)
            contas_config[conta_id] = ContaInfo(
                margem=info["margem"],
                roas_breakeven=info["roas_breakeven"],
                descricao=info.get("descricao", ""),
            )

        # Parse browser
        browser_cfg = data.get("browser", {})

        # Parse operacao
        op_cfg = data.get("operacao", {})
        work_blocks_raw = op_cfg.get("work_blocks", [(8, 0, 12, 0), (13, 30, 18, 0), (19, 0, 22, 0)])
        work_blocks = [tuple(b) for b in work_blocks_raw]

        # Parse guardrails
        guard_cfg = data.get("guardrails", {})

        # Parse delays
        delay_cfg = data.get("delays", {})

        # Parse diretorios
        dir_cfg = data.get("diretorios", {})

        config = cls(
            ecomfluxo_url=ecomfluxo_url,
            ecomfluxo_api_key=ecomfluxo_api_key,
            ml_client_id=ml_client_id,
            ml_client_secret=ml_client_secret,
            contas=contas,
            contas_config=contas_config,
            # Browser
            browser_type=browser_cfg.get("type", "camoufox"),
            headless=browser_cfg.get("headless", True),
            slow_motion_ms=browser_cfg.get("slow_motion_ms", 100),
            timeout_ms=browser_cfg.get("timeout_ms", 30000),
            # Operacao
            work_blocks=work_blocks,
            variacao_minutos=op_cfg.get("variacao_minutos", 30),
            timezone=op_cfg.get("timezone", "America/Sao_Paulo"),
            keep_alive_interval_s=op_cfg.get("keep_alive_interval_s", 7200),
            poll_interval_s=op_cfg.get("poll_interval_s", 90),
            sleep_min_s=op_cfg.get("sleep_min_s", 60),
            sleep_max_s=op_cfg.get("sleep_max_s", 120),
            # Guardrails
            max_acoes_dia=guard_cfg.get("max_acoes_dia", 50),
            max_acoes_hora=guard_cfg.get("max_acoes_hora", 20),
            max_acoes_ciclo=guard_cfg.get("max_acoes_ciclo", 5),
            variacao_budget_max_pct=guard_cfg.get("variacao_budget_max_pct", 15.0),
            variacao_roas_max_pct=guard_cfg.get("variacao_roas_max_pct", 10.0),
            roas_min=guard_cfg.get("roas_min", 1.0),
            roas_max=guard_cfg.get("roas_max", 35.0),
            ram_min_livre_mb=guard_cfg.get("ram_min_livre_mb", 500),
            cpu_max_pct=guard_cfg.get("cpu_max_pct", 80),
            disco_min_livre_mb=guard_cfg.get("disco_min_livre_mb", 1000),
            # Delays
            entre_acoes_min_ms=delay_cfg.get("entre_acoes_min_ms", 3000),
            entre_acoes_max_ms=delay_cfg.get("entre_acoes_max_ms", 10000),
            entre_contas_min_s=delay_cfg.get("entre_contas_min_s", 180),
            entre_contas_max_s=delay_cfg.get("entre_contas_max_s", 600),
            # Diretorios
            state_dir=dir_cfg.get("state", "./state"),
            profiles_dir=dir_cfg.get("profiles", "./profiles"),
            screenshot_dir=dir_cfg.get("screenshots", "./screenshots"),
            log_dir=dir_cfg.get("logs", "./logs"),
            # Flags
            dry_run=data.get("dry_run", False),
        )

        logger.info(f"Config carregada: {len(contas)} contas, dry_run={config.dry_run}")
        return config

    def criar_diretorios(self):
        """Cria diretorios necessarios."""
        for d in [self.state_dir, self.profiles_dir, self.screenshot_dir, self.log_dir]:
            Path(d).mkdir(parents=True, exist_ok=True)

    def get_conta_info(self, conta_id: int) -> ContaInfo:
        """Retorna info de uma conta ou default."""
        return self.contas_config.get(conta_id, ContaInfo(margem=0.08, roas_breakeven=12.5))

    def get_conta_env(self, conta_id: int, key: str) -> str:
        """Busca env var por conta. Ex: get_conta_env(673355109, 'EMAIL') -> ML_EMAIL_673355109."""
        return os.getenv(f"ML_{key}_{conta_id}", "")
