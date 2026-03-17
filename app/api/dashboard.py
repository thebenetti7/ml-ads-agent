"""
API de dashboard local — status, changelog, sessoes, kill switch.

Baseado em: PUBLICIDADE-MODULO/skills/ml-ads-orchestrator/app/api/dashboard.py
+ rotas de agent status de agent_client.py
Mudancas:
- Imports ajustados para nova estrutura
- Config fields ajustados para Config unificado
- Adicionada rota /api/agent/status
- Adicionada rota /api/dashboard/guardrails
"""

import psutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

import logging

from app.models import AgentStatus, StatusAgente

logger = logging.getLogger(__name__)

router = APIRouter()

# Injetados pelo main.py
_state_manager = None
_config = None
_orchestrator = None
_guardrails = None


def set_managers(state_manager, config, orchestrator, guardrails=None):
    """Define instancias para uso do router."""
    global _state_manager, _config, _orchestrator, _guardrails
    _state_manager = state_manager
    _config = config
    _orchestrator = orchestrator
    _guardrails = guardrails


# ==================== AGENT STATUS ====================

@router.get("/agent/status")
async def obter_status_agente():
    """Retorna status completo do agente."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Agente nao inicializado")

    try:
        uptime = (
            (datetime.now() - _orchestrator.tempo_inicio).total_seconds() / 60
        )

        # Coletar metricas de recursos
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.1)

        # Coletar status de sessoes
        sessoes = {}
        for conta_id in _config.contas:
            estado = _state_manager.carregar_estado(conta_id)
            sessoes[conta_id] = estado.get("sessao_status", "fechada")

        # Conta atual
        conta_atual = None
        if hasattr(_orchestrator, "conta_atual"):
            conta_atual = _orchestrator.conta_atual

        # Acoes/erros hoje
        processadas = 0
        erros = 0
        for conta_id in _config.contas:
            estado = _state_manager.carregar_estado(conta_id)
            processadas += estado.get("acoes_hoje", 0)
            erros += len(estado.get("erros", []))

        # Ultima acao
        ultima_acao = None
        ultima_acao_tempo = None
        for conta_id in _config.contas:
            estado = _state_manager.carregar_estado(conta_id)
            ts = estado.get("ultima_acao")
            if ts and (ultima_acao_tempo is None or ts > ultima_acao_tempo):
                ultima_acao_tempo = ts
                ultima_acao = ts

        return AgentStatus(
            status=getattr(_orchestrator, "status", StatusAgente.PARADO),
            uptime_minutos=uptime,
            conta_atual=conta_atual,
            ultima_acao=ultima_acao,
            ultima_acao_tempo=(
                datetime.fromisoformat(ultima_acao_tempo)
                if ultima_acao_tempo
                else None
            ),
            processadas_hoje=processadas,
            erros_hoje=erros,
            sessoes=sessoes,
            memoria_mb=mem.used / (1024 * 1024),
            cpu_percent=cpu,
            dry_run=_config.dry_run,
            modo="vps" if hasattr(_orchestrator, "vps_client") and _orchestrator.vps_client.online else "offline",
        ).model_dump(mode="json")

    except Exception as e:
        logger.error(f"Erro ao obter status do agente: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== DASHBOARD ====================

@router.get("/dashboard/status")
async def obter_status_dashboard():
    """Retorna status resumido do dashboard."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Agente nao inicializado")

    try:
        uptime = (
            (datetime.now() - _orchestrator.tempo_inicio).total_seconds() / 60
        )

        dentro_horario = True
        if _guardrails:
            dentro_horario, _ = _guardrails.dentro_horario_operacao()

        kill_switch = False
        if _guardrails:
            kill_switch = _guardrails.kill_switch_ativo()

        return {
            "status": getattr(_orchestrator, "status", StatusAgente.PARADO).value,
            "uptime_minutos": uptime,
            "conta_atual": getattr(_orchestrator, "conta_atual", None),
            "kill_switch_ativo": kill_switch,
            "dentro_horario": dentro_horario,
            "timestamp": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error(f"Erro ao obter status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/changelog")
async def obter_changelog_dia():
    """Retorna changelog de hoje."""
    if not _state_manager:
        raise HTTPException(status_code=503, detail="State manager nao inicializado")

    try:
        changelog = _state_manager.carregar_changelog()
        return {
            "data": datetime.now().date().isoformat(),
            "total": len(changelog),
            "acoes": changelog,
        }

    except Exception as e:
        logger.error(f"Erro ao carregar changelog: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/sessoes")
async def obter_status_sessoes():
    """Retorna status das sessoes de cada conta."""
    if not _state_manager:
        raise HTTPException(status_code=503, detail="State manager nao inicializado")

    try:
        sessoes = {}
        for conta_id in _config.contas:
            estado = _state_manager.carregar_estado(conta_id)
            sessoes[str(conta_id)] = {
                "status": estado.get("sessao_status", "fechada"),
                "aberta_em": estado.get("sessao_aberta_em"),
                "ultima_acao": estado.get("ultima_acao"),
            }
        return sessoes

    except Exception as e:
        logger.error(f"Erro ao obter status de sessoes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/dashboard/kill-switch")
async def toggle_kill_switch(ativar: bool):
    """Ativa ou desativa o kill switch."""
    try:
        if _guardrails:
            if ativar:
                _guardrails.ativar_kill_switch("Ativado via dashboard")
                return {"status": "ativado"}
            else:
                _guardrails.desativar_kill_switch()
                return {"status": "desativado"}
        else:
            # Fallback: manipular arquivo diretamente
            stop_file = Path("./STOP")
            if ativar:
                stop_file.write_text(f"{datetime.now().isoformat()}: Ativado via dashboard")
                return {"status": "ativado"}
            else:
                if stop_file.exists():
                    stop_file.unlink()
                return {"status": "desativado"}

    except Exception as e:
        logger.error(f"Erro ao toggle kill switch: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/config")
async def obter_configuracao():
    """Retorna configuracao atual (sem credenciais)."""
    if not _config:
        raise HTTPException(status_code=503, detail="Configuracao nao carregada")

    try:
        # Formatar work blocks para exibicao
        blocos = [
            f"{b[0]:02d}:{b[1]:02d}-{b[2]:02d}:{b[3]:02d}"
            for b in _config.work_blocks
        ]

        return {
            "contas": _config.contas,
            "browser_type": _config.browser_type,
            "headless": _config.headless,
            "work_blocks": blocos,
            "variacao_minutos": _config.variacao_minutos,
            "max_acoes_hora": _config.max_acoes_hora,
            "max_acoes_dia": _config.max_acoes_dia,
            "max_acoes_ciclo": _config.max_acoes_ciclo,
            "entre_acoes_min_ms": _config.entre_acoes_min_ms,
            "entre_acoes_max_ms": _config.entre_acoes_max_ms,
            "dry_run": _config.dry_run,
        }

    except Exception as e:
        logger.error(f"Erro ao obter configuracao: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/erros")
async def obter_erros_recentes(conta_id: int = None, limit: int = 20):
    """Retorna erros recentes."""
    if not _state_manager:
        raise HTTPException(status_code=503, detail="State manager nao inicializado")

    try:
        if conta_id is None:
            erros_todos = []
            for cid in _config.contas:
                estado = _state_manager.carregar_estado(cid)
                erros = estado.get("erros", [])
                for erro in erros:
                    erro["conta_id"] = cid
                erros_todos.extend(erros)

            erros_todos.sort(
                key=lambda x: x.get("timestamp", ""),
                reverse=True,
            )
            return erros_todos[:limit]
        else:
            estado = _state_manager.carregar_estado(conta_id)
            erros = estado.get("erros", [])
            return sorted(
                erros,
                key=lambda x: x.get("timestamp", ""),
                reverse=True,
            )[:limit]

    except Exception as e:
        logger.error(f"Erro ao carregar erros: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/guardrails")
async def obter_guardrails():
    """Retorna relatorio dos guardrails."""
    if not _guardrails:
        raise HTTPException(status_code=503, detail="Guardrails nao inicializados")

    try:
        return _guardrails.relatorio()
    except Exception as e:
        logger.error(f"Erro ao obter guardrails: {e}")
        raise HTTPException(status_code=500, detail=str(e))
