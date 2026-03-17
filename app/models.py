"""
Modelos Pydantic e Enums do ML Ads Agent.

Merge de:
- PUBLICIDADE-MODULO/skills/ml-ads-orchestrator/app/models.py
- Contrato VPS (api-contract.md)
"""

from datetime import datetime
from typing import Optional, Any
from enum import Enum

from pydantic import BaseModel, Field


# ============================================================
# ENUMS
# ============================================================

class StatusAcao(str, Enum):
    PENDENTE = "pendente"
    EXECUTANDO = "executando"
    SUCESSO = "sucesso"
    ERRO = "erro"
    CANCELADA = "cancelada"
    IGNORADA = "ignorada"
    SIMULADA = "simulada"  # dry-run


class TipoAcao(str, Enum):
    PAUSAR_CAMPANHA = "pausar_campanha"
    ATIVAR_CAMPANHA = "ativar_campanha"
    EDITAR_BUDGET = "editar_budget"
    EDITAR_ROAS_TARGET = "editar_roas_target"
    CRIAR_CAMPANHA = "criar_campanha"
    REMOVER_ANUNCIO = "remover_anuncio"
    ADICIONAR_ANUNCIO = "adicionar_anuncio"
    LIMPAR_CAMPANHA = "limpar_campanha"


class Prioridade(str, Enum):
    CRITICA = "CRITICA"
    ALTA = "ALTA"
    MEDIA = "MEDIA"
    BAIXA = "BAIXA"


class StatusAgente(str, Enum):
    PARADO = "parado"
    INICIANDO = "iniciando"
    EXECUTANDO = "executando"
    PAUSADO = "pausado"
    OFFLINE = "offline"  # VPS inacessivel, modo local
    ENCERRANDO = "encerrando"


class StatusSessao(str, Enum):
    FECHADA = "fechada"
    ABRINDO = "abrindo"
    ABERTA = "aberta"
    EM_USO = "em_uso"
    FECHANDO = "fechando"


# ============================================================
# MODELOS — ACOES VPS
# ============================================================

class VPSActionTarget(BaseModel):
    """Target de uma acao vinda da VPS."""
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    site: str = "MLB"
    item_id: Optional[str] = None
    item_title: Optional[str] = None


class VPSAction(BaseModel):
    """Acao pendente recebida da VPS."""
    action_id: str
    action_type: str  # mapeado para TipoAcao
    priority: str = "MEDIA"  # CRITICA, ALTA, MEDIA, BAIXA
    status: str = "PENDENTE"
    target: VPSActionTarget = Field(default_factory=VPSActionTarget)
    params: dict[str, Any] = Field(default_factory=dict)
    regra_origem: str = ""
    notas: str = ""
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    tentativas: int = 0
    max_tentativas: int = 3

    @property
    def tipo_acao(self) -> TipoAcao:
        """Converte action_type string para TipoAcao enum."""
        mapping = {
            "PAUSAR_CAMPANHA": TipoAcao.PAUSAR_CAMPANHA,
            "ATIVAR_CAMPANHA": TipoAcao.ATIVAR_CAMPANHA,
            "EDITAR_BUDGET": TipoAcao.EDITAR_BUDGET,
            "EDITAR_ROAS_TARGET": TipoAcao.EDITAR_ROAS_TARGET,
            "CRIAR_CAMPANHA": TipoAcao.CRIAR_CAMPANHA,
            "REMOVER_ANUNCIO": TipoAcao.REMOVER_ANUNCIO,
            "ADICIONAR_ANUNCIO": TipoAcao.ADICIONAR_ANUNCIO,
            "LIMPAR_CAMPANHA": TipoAcao.LIMPAR_CAMPANHA,
        }
        return mapping.get(self.action_type.upper(), TipoAcao.PAUSAR_CAMPANHA)

    @property
    def prioridade_ordem(self) -> int:
        """Retorna ordem numerica para sorting (menor = mais urgente)."""
        ordem = {"CRITICA": 1, "ALTA": 2, "MEDIA": 3, "BAIXA": 4}
        return ordem.get(self.priority, 3)


# ============================================================
# MODELOS — RESULTADO DE EXECUCAO
# ============================================================

class AcaoExecutada(BaseModel):
    """Resultado reportado para a VPS."""
    action_id: str
    status: StatusAcao
    detalhes: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    duration_seconds: float = 0.0
    verificado_api: bool = False
    screenshot_path: Optional[str] = None
    logs: list[str] = Field(default_factory=list)
    erros: Optional[str] = None


# ============================================================
# MODELOS — HEARTBEAT E ALERTAS
# ============================================================

class RecursosSistema(BaseModel):
    """Metricas de recursos do sistema."""
    ram_percent: float = 0.0
    cpu_percent: float = 0.0
    disco_livre_gb: float = 0.0


class ContaStatus(BaseModel):
    """Status de uma conta no heartbeat."""
    conta_id: int
    ativa: bool = True
    sessao_browser_aberta: bool = False
    autenticado: bool = False


class Heartbeat(BaseModel):
    """Heartbeat enviado ao VPS."""
    agent_id: str = "ml-ads-agent-local"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    status: StatusAgente = StatusAgente.EXECUTANDO
    recursos: RecursosSistema = Field(default_factory=RecursosSistema)
    contas: list[ContaStatus] = Field(default_factory=list)
    total_executadas_hoje: int = 0
    total_sucesso: int = 0
    total_falhas: int = 0
    uptime_minutos: float = 0.0
    erros: list[dict] = Field(default_factory=list)


class Alerta(BaseModel):
    """Alerta enviado ao VPS."""
    tipo: str  # "erro", "aviso", "info", "critico"
    severidade: str = "MEDIA"  # CRITICA, ALTA, MEDIA, BAIXA
    mensagem: str
    conta_id: Optional[int] = None
    detalhe_tecnico: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# MODELOS — AGENT STATUS (DASHBOARD)
# ============================================================

class AgentStatus(BaseModel):
    """Status completo do agente para o dashboard."""
    status: StatusAgente
    uptime_minutos: float = 0.0
    conta_atual: Optional[int] = None
    ultima_acao: Optional[str] = None
    ultima_acao_tempo: Optional[datetime] = None
    processadas_hoje: int = 0
    erros_hoje: int = 0
    sessoes: dict[int, str] = Field(default_factory=dict)
    memoria_mb: float = 0.0
    cpu_percent: float = 0.0
    dry_run: bool = False
    modo: str = "vps"  # vps ou offline
