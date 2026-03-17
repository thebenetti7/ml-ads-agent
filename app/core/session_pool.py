"""
Session Pool — Pool de sessoes Camoufox.

Baseado em: PUBLICIDADE-MODULO/skills/ml-ads-orchestrator/app/core/session_pool.py
Mudancas:
- Usa Camoufox como browser principal
- Integrado com MLSessionManager para login/2FA
- Max 1 sessao ativa por vez (economia de RAM)
"""

import logging
from typing import Optional

from app.browser.session_manager import MLSessionManager

logger = logging.getLogger(__name__)


class SessionPool:
    """Pool de sessoes browser — max 1 ativa por vez."""

    def __init__(self, config):
        self.config = config
        self.sessoes: dict[int, MLSessionManager] = {}
        self.sessao_ativa: Optional[int] = None

    async def obter_sessao(self, conta_id: int) -> Optional[MLSessionManager]:
        """
        Obtem ou cria sessao para uma conta.
        Fecha sessao anterior se existir (max 1 ativa).
        """
        # Se ja existe e esta ativa, retorna
        if conta_id in self.sessoes and self.sessoes[conta_id].esta_ativa():
            self.sessao_ativa = conta_id
            return self.sessoes[conta_id]

        # Fechar sessao anterior
        if self.sessao_ativa and self.sessao_ativa != conta_id:
            await self.fechar_sessao(self.sessao_ativa)

        # Criar nova sessao
        session = MLSessionManager(
            conta_id=conta_id,
            headless=self.config.headless,
            profiles_dir=self.config.profiles_dir,
            use_camoufox=(self.config.browser_type == "camoufox"),
        )

        try:
            await session.iniciar()

            # Login
            if not await session.garantir_login():
                logger.error(f"Login falhou para conta {conta_id}")
                await session.fechar()
                return None

            # Keep-alive
            session.iniciar_keep_alive(
                intervalo_minutos=self.config.keep_alive_interval_s // 60
            )

            self.sessoes[conta_id] = session
            self.sessao_ativa = conta_id
            logger.info(f"Sessao aberta para conta {conta_id}")
            return session

        except Exception as e:
            logger.error(f"Erro ao criar sessao para {conta_id}: {e}")
            try:
                await session.fechar()
            except Exception:
                pass
            return None

    async def fechar_sessao(self, conta_id: int):
        """Fecha sessao de uma conta."""
        if conta_id in self.sessoes:
            try:
                await self.sessoes[conta_id].fechar()
            except Exception as e:
                logger.error(f"Erro ao fechar sessao {conta_id}: {e}")
            del self.sessoes[conta_id]

            if self.sessao_ativa == conta_id:
                self.sessao_ativa = None

    async def fechar_todas(self):
        """Fecha todas as sessoes."""
        for conta_id in list(self.sessoes.keys()):
            await self.fechar_sessao(conta_id)

    def status_sessoes(self) -> dict[int, str]:
        """Retorna status de todas as sessoes."""
        return {
            conta_id: "ativa" if session.esta_ativa() else "inativa"
            for conta_id, session in self.sessoes.items()
        }
