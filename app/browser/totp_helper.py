"""
TOTP Helper — Gerador de codigos 2FA para Mercado Livre.

Copiado de: PUBLICIDADE-MODULO/skills/browser-automation-ml/scripts/totp_helper.py
Mudanca: suporte a secret por conta (env var ML_TOTP_SECRET_{conta_id}).
"""

import asyncio
import os
import time

import pyotp


class TOTPHelper:
    """Gerenciador de codigos TOTP para 2FA do ML."""

    def __init__(self, secret: str = None, conta_id: int = None):
        if secret:
            self.secret = secret
        elif conta_id:
            self.secret = os.environ.get(f"ML_TOTP_SECRET_{conta_id}", "")
        else:
            self.secret = os.environ.get("ML_TOTP_SECRET", "")

        if not self.secret:
            raise ValueError(
                "TOTP secret nao encontrado. "
                "Configure ML_TOTP_SECRET_{conta_id} no .env"
            )
        self.totp = pyotp.TOTP(self.secret)

    def gerar(self) -> str:
        """Gera codigo TOTP atual (6 digitos)."""
        return self.totp.now()

    def verificar(self, codigo: str) -> bool:
        """Verifica se um codigo e valido."""
        return self.totp.verify(codigo, valid_window=1)

    def tempo_restante(self) -> int:
        """Segundos ate o proximo codigo."""
        return self.totp.interval - (int(time.time()) % self.totp.interval)

    def seguro_para_enviar(self, margem_segundos: int = 5) -> bool:
        """Retorna True se o codigo tem tempo suficiente."""
        return self.tempo_restante() > margem_segundos

    async def gerar_seguro(self) -> str:
        """Gera codigo garantindo que nao vai expirar durante envio."""
        if not self.seguro_para_enviar():
            espera = self.tempo_restante() + 1
            await asyncio.sleep(espera)
        return self.gerar()
