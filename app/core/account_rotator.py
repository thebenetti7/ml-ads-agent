"""
Account Rotator — Gerenciador de Multiplas Contas

Baseado em: PUBLICIDADE-MODULO/skills/ml-ads-orchestrator/scripts/account_rotator.py
Mudancas:
- Usa Config unificado (contas como list[int], env vars via config.get_conta_env)
- Removido import dinamico de session_manager (sera feito pelo orchestrator)
- Removido import dinamico de MercadoAdsClient
- Simplificado: foco no round-robin e balanceamento
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class AccountRotator:
    """Gerenciador de multiplas contas com round-robin."""

    def __init__(self, config, state_manager):
        self.config = config
        self.state_manager = state_manager
        self.contas = list(config.contas)  # list[int]
        self.indice_atual = 0
        self.tokens_expiry: dict[int, datetime] = {}

        logger.info(f"AccountRotator: {len(self.contas)} contas")

        if not self.contas:
            logger.warning("NENHUMA conta configurada!")

    # ==================== ROTACAO ====================

    def proxima_conta(self) -> Optional[int]:
        """Retorna proxima conta no round-robin."""
        if not self.contas:
            return None

        conta = self.contas[self.indice_atual]
        self.indice_atual = (self.indice_atual + 1) % len(self.contas)
        logger.info(f"Proxima conta: {conta}")
        return conta

    def proxima_conta_menos_carregada(self) -> Optional[int]:
        """Retorna conta com menos acoes hoje."""
        if not self.contas:
            return None

        menos_carregada = None
        min_acoes = float("inf")

        for conta_id in self.contas:
            acoes = self.state_manager.acoes_hoje_count(conta_id)
            if acoes < min_acoes:
                min_acoes = acoes
                menos_carregada = conta_id

        logger.info(f"Conta menos carregada: {menos_carregada} ({min_acoes} acoes)")
        return menos_carregada

    def rotacionar(self, modo: str = "round_robin") -> Optional[int]:
        """Rotaciona com base em estrategia."""
        if modo == "round_robin":
            return self.proxima_conta()
        elif modo == "menos_carregada":
            return self.proxima_conta_menos_carregada()
        return self.proxima_conta()

    # ==================== TOKENS ====================

    def get_access_token(self, conta_id: int) -> Optional[str]:
        """Obtem access token de env var."""
        return self.config.get_conta_env(conta_id, "TOKEN")

    def get_refresh_token(self, conta_id: int) -> Optional[str]:
        """Obtem refresh token de env var."""
        return self.config.get_conta_env(conta_id, "REFRESH")

    def token_precisa_refresh(self, conta_id: int) -> bool:
        """Verifica se token esta proximo de expirar."""
        if conta_id not in self.tokens_expiry:
            return True  # Nunca refreshado, assumir que precisa
        return datetime.now() >= self.tokens_expiry[conta_id]

    def registrar_token_refresh(self, conta_id: int, novo_token: str, novo_refresh: str):
        """Registra que token foi refreshado."""
        os.environ[f"ML_TOKEN_{conta_id}"] = novo_token
        os.environ[f"ML_REFRESH_{conta_id}"] = novo_refresh
        self.tokens_expiry[conta_id] = datetime.now() + timedelta(hours=5)
        logger.info(f"Token refreshado para conta {conta_id}")

    # ==================== VALIDACAO ====================

    def validar_contas(self) -> tuple[bool, list[str]]:
        """Valida que todas as contas tem credenciais."""
        problemas = []

        for conta_id in self.contas:
            email = self.config.get_conta_env(conta_id, "EMAIL")
            token = self.config.get_conta_env(conta_id, "TOKEN")

            if not email:
                problemas.append(f"Conta {conta_id}: ML_EMAIL_{conta_id} nao definido")
            if not token:
                problemas.append(f"Conta {conta_id}: ML_TOKEN_{conta_id} nao definido")

        if problemas:
            for p in problemas:
                logger.warning(p)
            return False, problemas

        logger.info("Validacao de contas OK")
        return True, []

    # ==================== RESUMO ====================

    def resumo(self) -> dict:
        """Retorna resumo de todas as contas."""
        info = {
            "total_contas": len(self.contas),
            "proxima_indice": self.indice_atual,
            "contas": {},
        }

        for conta_id in self.contas:
            estado = self.state_manager.carregar_estado(conta_id)
            info["contas"][conta_id] = {
                "acoes_hoje": estado.get("acoes_hoje", 0),
                "sessao": estado.get("sessao_status", "fechada"),
                "alertas": len(estado.get("alertas", [])),
                "erros": len(estado.get("erros", [])),
                "ultima_acao": estado.get("ultima_acao"),
            }

        return info

    def status_resumido(self) -> str:
        """String de status para logs."""
        proxima = self.contas[self.indice_atual] if self.contas else None
        return f"Contas: {len(self.contas)} | Proxima: {proxima or 'nenhuma'}"
