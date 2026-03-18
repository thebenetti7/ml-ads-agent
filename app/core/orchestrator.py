"""
Orchestrator — Loop Principal UNIFICADO

Merge de:
- app/core/orchestrator.py (stub original)
- scripts/orchestrator.py (loop real com ciclos)

2 modos de operacao:
1. Modo VPS (primario): Poll acoes pendentes -> executar -> reportar
2. Modo Offline (fallback): Decision engine local -> so regras CRITICAS R01-R04

Ciclo:
    check kill switch -> check horario -> check recursos ->
    heartbeat -> processar contas -> sleep 60-120s
"""

import asyncio
import logging
import random
from datetime import datetime
from typing import Optional

import psutil

from app.models import (
    StatusAgente, StatusAcao, Heartbeat, RecursosSistema, ContaStatus, AcaoExecutada
)
from app.config import Config
from app.core.state_manager import StateManager
from app.core.guardrails import GuardrailsManager
from app.core.decision_engine import DecisionEngine
from app.core.account_rotator import AccountRotator
from app.api.vps_client import VPSClient
from app.core.browser_executor import BrowserExecutor
from app.core.session_pool import SessionPool
from app.browser.verifier import ActionVerifier

logger = logging.getLogger(__name__)


class Orchestrator:
    """Loop principal do agente de ads."""

    def __init__(
        self,
        config: Config,
        vps_client: VPSClient,
        state_manager: StateManager,
        guardrails: GuardrailsManager,
        session_pool: Optional["SessionPool"] = None,
        shutdown_event: Optional[asyncio.Event] = None,
    ):
        self.config = config
        self.vps_client = vps_client
        self.state_manager = state_manager
        self.guardrails = guardrails
        self.shutdown_event = shutdown_event or asyncio.Event()

        self.decision_engine = DecisionEngine(config, state_manager, dry_run=config.dry_run)
        self.account_rotator = AccountRotator(config, state_manager)

        # Browser executor + session pool
        self.session_pool = session_pool or SessionPool(config)
        self.browser_executor = BrowserExecutor(
            config=config,
            session_pool=self.session_pool,
            dry_run=config.dry_run,
        )

        self.status = StatusAgente.INICIANDO
        self.tempo_inicio = datetime.now()
        self.conta_atual: Optional[int] = None
        self.ciclo_num = 0

    # ==================== LOOP PRINCIPAL ====================

    async def run(self):
        """Loop principal do agente."""
        self.status = StatusAgente.EXECUTANDO
        logger.info(
            f"Orchestrator iniciado: {len(self.config.contas)} contas, "
            f"dry_run={self.config.dry_run}"
        )

        try:
            while not self.shutdown_event.is_set():
                self.ciclo_num += 1
                logger.info(f"=== CICLO #{self.ciclo_num} ===")

                # Reset diario (midnight)
                self.state_manager.reset_diario()

                # 1. Kill switch
                if self.guardrails.kill_switch_ativo():
                    logger.warning("Kill switch ativo, aguardando...")
                    self.status = StatusAgente.PAUSADO
                    await self._sleep(60)
                    continue

                # 2. Horario
                ok, motivo = self.guardrails.dentro_horario_operacao()
                if not ok:
                    logger.info(f"Fora do horario: {motivo}")
                    self.status = StatusAgente.PAUSADO
                    await self._sleep(120)
                    continue

                # 3. Recursos
                ok, detalhes = self.guardrails.recursos_suficientes()
                if not ok:
                    logger.warning(f"Recursos insuficientes: {detalhes.get('motivos', [])}")
                    self.status = StatusAgente.PAUSADO
                    await self._sleep(60)
                    continue

                self.status = StatusAgente.EXECUTANDO

                # 4. Heartbeat
                await self._enviar_heartbeat()

                # 5. Processar contas
                await self._processar_contas()

                # 6. Sleep entre ciclos
                sleep_s = random.randint(
                    self.config.sleep_min_s,
                    self.config.sleep_max_s,
                )
                logger.info(f"Dormindo {sleep_s}s ate proximo ciclo")
                await self._sleep(sleep_s)

        except asyncio.CancelledError:
            logger.info("Orchestrator cancelado")
        except Exception as e:
            logger.error(f"Erro critico no orchestrator: {e}", exc_info=True)
        finally:
            self.status = StatusAgente.ENCERRANDO
            logger.info("Orchestrator encerrado")

    # ==================== PROCESSAR CONTAS ====================

    async def _processar_contas(self):
        """Processa todas as contas no ciclo."""
        for i, conta_id in enumerate(self.config.contas):
            if self.shutdown_event.is_set():
                break

            self.conta_atual = conta_id
            logger.info(f"--- Conta {conta_id} ({i+1}/{len(self.config.contas)}) ---")

            # Verificar limites
            atingido, motivo = self.guardrails.limite_acoes_atingido(conta_id)
            if atingido:
                logger.info(f"Conta {conta_id}: {motivo}")
                continue

            # Snapshot pre-ciclo
            self.state_manager.snapshot_pre_ciclo(conta_id)

            try:
                if self.vps_client.online:
                    await self._processar_modo_vps(conta_id)
                else:
                    await self._processar_modo_offline(conta_id)
            except Exception as e:
                logger.error(f"Erro ao processar conta {conta_id}: {e}")
                self.state_manager.registrar_erro(conta_id, str(e))

            # Delay entre contas
            if i < len(self.config.contas) - 1:
                delay = random.randint(
                    self.config.entre_contas_min_s,
                    self.config.entre_contas_max_s,
                )
                logger.debug(f"Delay entre contas: {delay}s")
                await self._sleep(delay)

    # ==================== MODO VPS ====================

    async def _processar_modo_vps(self, conta_id: int):
        """Modo primario: buscar acoes pendentes da VPS e executar."""
        acoes = await self.vps_client.buscar_acoes_pendentes(
            conta_id=conta_id,
            limit=self.config.max_acoes_ciclo,
        )

        if not acoes:
            logger.debug(f"Nenhuma acao pendente para {conta_id}")
            return

        logger.info(f"{len(acoes)} acoes pendentes para {conta_id}")

        # Ordenar por prioridade
        acoes.sort(key=lambda a: a.prioridade_ordem)

        # Separar acoes de toggle (pausar/ativar) das demais para processamento em lote
        _TOGGLE_TIPOS = {
            "pausar_campanha", "ativar_campanha",
            "PAUSAR_CAMPANHA", "ATIVAR_CAMPANHA",
        }
        toggle_acoes = []
        outras_acoes = []
        for a in acoes:
            tipo_str = a.action_type if isinstance(a.action_type, str) else a.action_type.value
            if tipo_str.lower() in ("pausar_campanha", "ativar_campanha"):
                toggle_acoes.append(a)
            else:
                outras_acoes.append(a)

        acoes_executadas = 0

        # 1. Executar toggles em lote (uma unica varredura de paginas)
        if toggle_acoes and not self.shutdown_event.is_set():
            # Filtrar pelo guardrail
            toggle_ok = []
            for acao in toggle_acoes:
                ok, motivo = self.guardrails.pode_executar(
                    tipo_acao=acao.action_type.lower() if isinstance(acao.action_type, str) else acao.action_type.value.lower(),
                    conta_id=conta_id,
                    params=acao.params,
                )
                if not ok:
                    logger.info(f"Acao {acao.action_id} bloqueada: {motivo}")
                    await self.vps_client.reportar_acao(AcaoExecutada(
                        action_id=acao.action_id,
                        status=StatusAcao.CANCELADA,
                        detalhes=f"Bloqueada por guardrail: {motivo}",
                    ))
                else:
                    toggle_ok.append(acao)

            if toggle_ok:
                logger.info(f"Executando {len(toggle_ok)} acoes de toggle em lote")
                inicio = datetime.now()
                resultados_batch = await self.browser_executor.executar_batch_toggle(
                    toggle_ok, conta_id
                )
                duracao_total = (datetime.now() - inicio).total_seconds()

                for acao, resultado in zip(toggle_ok, resultados_batch):
                    resultado.duration_seconds = resultado.duration_seconds or duracao_total / len(toggle_ok)
                    await self.vps_client.reportar_acao(resultado)
                    self.state_manager.registrar_acao(
                        conta_id=conta_id,
                        action_id=acao.action_id,
                        rule_id=acao.regra_origem,
                        action_type=acao.action_type if isinstance(acao.action_type, str) else acao.action_type.value,
                        campaign_id=acao.target.campaign_id or "",
                        campaign_name=acao.target.campaign_name or "",
                        params=acao.params,
                        status=resultado.status.value,
                        duration_ms=int((resultado.duration_seconds or 0) * 1000),
                    )
                    acoes_executadas += 1

        # 2. Executar demais acoes individualmente
        for acao in outras_acoes:
            if self.shutdown_event.is_set():
                break

            ok, motivo = self.guardrails.pode_executar(
                tipo_acao=acao.action_type.lower() if isinstance(acao.action_type, str) else acao.action_type.value.lower(),
                conta_id=conta_id,
                params=acao.params,
            )

            if not ok:
                logger.info(f"Acao {acao.action_id} bloqueada: {motivo}")
                await self.vps_client.reportar_acao(AcaoExecutada(
                    action_id=acao.action_id,
                    status=StatusAcao.CANCELADA,
                    detalhes=f"Bloqueada por guardrail: {motivo}",
                ))
                continue

            inicio = datetime.now()
            resultado = await self._executar_acao(conta_id, acao)
            duracao = (datetime.now() - inicio).total_seconds()

            resultado.duration_seconds = duracao
            await self.vps_client.reportar_acao(resultado)

            self.state_manager.registrar_acao(
                conta_id=conta_id,
                action_id=acao.action_id,
                rule_id=acao.regra_origem,
                action_type=acao.action_type if isinstance(acao.action_type, str) else acao.action_type.value,
                campaign_id=acao.target.campaign_id or "",
                campaign_name=acao.target.campaign_name or "",
                params=acao.params,
                status=resultado.status.value,
                duration_ms=int(duracao * 1000),
            )

            acoes_executadas += 1

            delay_ms = random.randint(
                self.config.entre_acoes_min_ms,
                self.config.entre_acoes_max_ms,
            )
            await asyncio.sleep(delay_ms / 1000)

        logger.info(f"Conta {conta_id}: {acoes_executadas}/{len(acoes)} acoes executadas")

    # ==================== MODO OFFLINE ====================

    async def _processar_modo_offline(self, conta_id: int):
        """Modo fallback: decision engine local, so regras CRITICAS."""
        logger.info(f"Modo OFFLINE para {conta_id} (apenas R01-R04)")
        self.status = StatusAgente.OFFLINE

        # Nota: em modo offline, nao temos dados da API ML
        # O decision engine precisaria de dados de campanhas/anuncios
        # que nao estao disponiveis sem conexao com VPS
        # Por enquanto, apenas registrar que estamos offline
        self.state_manager.alertar(
            conta_id, "AVISO", "Operando em modo offline — VPS inacessivel"
        )

    # ==================== EXECUTAR ACAO ====================

    async def _executar_acao(self, conta_id: int, acao) -> AcaoExecutada:
        """Executa uma acao via browser e verifica via API."""
        logger.info(
            f"Executando: {acao.action_type} - {acao.target.campaign_name or acao.action_id}"
        )

        # Executar via browser (dry-run tratado dentro do executor)
        resultado = await self.browser_executor.executar(acao, conta_id)

        # Verificacao pos-acao via API (se sucesso e nao dry-run)
        if resultado.status == StatusAcao.SUCESSO and not self.config.dry_run:
            try:
                verifier = ActionVerifier(conta_id)
                verificacao = await verifier.verificar_acao(
                    tipo_acao=acao.action_type.lower(),
                    campaign_id=acao.target.campaign_id,
                    campaign_name=acao.target.campaign_name,
                    item_id=acao.target.item_id,
                    expected_value=acao.params.get("novo_budget") or acao.params.get("novo_roas"),
                )
                resultado.verificado_api = verificacao.get("verificado", False)
                resultado.logs.append(f"API: {verificacao.get('detalhes', '')}")
            except Exception as e:
                resultado.logs.append(f"Verificacao API falhou: {e}")

        return resultado

    # ==================== HEARTBEAT ====================

    async def _enviar_heartbeat(self):
        """Envia heartbeat ao VPS."""
        try:
            mem = psutil.virtual_memory()
            cpu = psutil.cpu_percent(interval=0.1)
            try:
                disco = psutil.disk_usage("C:\\")
            except Exception:
                disco = psutil.disk_usage("/")

            # Contar acoes e erros de hoje
            total_acoes = 0
            total_erros = 0
            contas_status = []

            for conta_id in self.config.contas:
                estado = self.state_manager.carregar_estado(conta_id)
                total_acoes += estado.get("acoes_hoje", 0)
                total_erros += len(estado.get("erros", []))
                contas_status.append(ContaStatus(
                    conta_id=conta_id,
                    ativa=True,
                    sessao_browser_aberta=estado.get("sessao_status") == "aberta",
                ))

            uptime = (datetime.now() - self.tempo_inicio).total_seconds() / 60

            heartbeat = Heartbeat(
                status=self.status,
                recursos=RecursosSistema(
                    ram_percent=mem.percent,
                    cpu_percent=cpu,
                    disco_livre_gb=disco.free / (1024 ** 3),
                ),
                contas=contas_status,
                total_executadas_hoje=total_acoes,
                total_sucesso=total_acoes,  # Aproximacao
                total_falhas=total_erros,
                uptime_minutos=uptime,
            )

            resultado = await self.vps_client.enviar_heartbeat(heartbeat)

            if resultado:
                # Processar comandos do VPS (kill, config update, etc.)
                await self._processar_comando_vps(resultado)

        except Exception as e:
            logger.debug(f"Erro ao enviar heartbeat: {e}")

    async def _processar_comando_vps(self, resposta: dict):
        """Processa comando recebido do VPS via heartbeat."""
        comando = resposta.get("comando")
        if not comando:
            return

        if comando == "kill":
            logger.warning("Comando KILL recebido do VPS")
            self.guardrails.ativar_kill_switch("Comando remoto do VPS")
        elif comando == "pause":
            logger.info("Comando PAUSE recebido do VPS")
            self.status = StatusAgente.PAUSADO
        elif comando == "resume":
            logger.info("Comando RESUME recebido do VPS")
            self.status = StatusAgente.EXECUTANDO
        elif comando == "config_update":
            logger.info("Config update recebido do VPS")
            # TODO: Recarregar config

    # ==================== HELPERS ====================

    async def _sleep(self, seconds: int):
        """Sleep interruptivel pelo shutdown_event."""
        try:
            await asyncio.wait_for(
                self.shutdown_event.wait(),
                timeout=seconds,
            )
        except asyncio.TimeoutError:
            pass  # Timeout normal

    async def shutdown(self):
        """Graceful shutdown."""
        self.status = StatusAgente.ENCERRANDO
        self.shutdown_event.set()
        await self.vps_client.fechar()
        logger.info("Orchestrator shutdown completo")
