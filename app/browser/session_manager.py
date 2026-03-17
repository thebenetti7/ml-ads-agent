"""
Session Manager — Gerenciador de sessao do Mercado Livre.

Baseado em: PUBLICIDADE-MODULO/skills/browser-automation-ml/scripts/session_manager.py
Mudancas:
- Usa Camoufox em vez de Playwright direto
- Credenciais por conta via env vars (ML_EMAIL_{conta_id}, etc)
- Integrado com TOTPHelper por conta
- Profile dir configuravel
"""

import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

import pyotp

logger = logging.getLogger(__name__)


class MLSessionManager:
    """Gerenciador de sessao do Mercado Livre com login, 2FA, e keep-alive."""

    def __init__(
        self,
        conta_id: int,
        headless: bool = True,
        profiles_dir: str = "./profiles",
        use_camoufox: bool = True,
    ):
        self.conta_id = conta_id
        self.headless = headless
        self.use_camoufox = use_camoufox
        self.profile_dir = Path(profiles_dir) / str(conta_id)
        self.cookies_file = self.profile_dir / "cookies_backup.json"

        self.browser = None
        self.context = None
        self.page = None
        self._keep_alive_task = None

        # Credenciais por conta
        self.email = os.environ.get(f"ML_EMAIL_{conta_id}", "")
        self.senha = os.environ.get(f"ML_PASSWORD_{conta_id}", "")
        self.totp_secret = os.environ.get(f"ML_TOTP_SECRET_{conta_id}", "")

    async def __aenter__(self):
        await self.iniciar()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.fechar()

    # ==================== LIFECYCLE ====================

    async def iniciar(self):
        """Inicia navegador com perfil persistente."""
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        if self.use_camoufox:
            await self._iniciar_camoufox()
        else:
            await self._iniciar_playwright()

        logger.info(f"Sessao iniciada para conta {self.conta_id}")

    async def _iniciar_camoufox(self):
        """Inicia via Camoufox (anti-detect Firefox)."""
        try:
            from camoufox.async_api import AsyncNewBrowser

            self.browser = await AsyncNewBrowser(
                headless=self.headless,
                geoip=True,
            )

            self.context = await self.browser.new_context(
                viewport={"width": 1366, "height": 768},
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                color_scheme="light",
            )

            self.page = await self.context.new_page()
            self.page.set_default_timeout(30000)
            self.page.set_default_navigation_timeout(30000)

        except ImportError:
            logger.warning("Camoufox nao disponivel, fallback para Playwright")
            await self._iniciar_playwright()

    async def _iniciar_playwright(self):
        """Fallback: inicia via Playwright direto."""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self.context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            viewport={"width": 1366, "height": 768},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            color_scheme="light",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
            ],
        )

        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    async def fechar(self):
        """Fecha navegador e salva cookies."""
        if self._keep_alive_task:
            self._keep_alive_task.cancel()
            try:
                await self._keep_alive_task
            except asyncio.CancelledError:
                pass

        if self.context:
            await self._salvar_cookies()
            await self.context.close()

        if self.browser:
            await self.browser.close()

        if hasattr(self, "_playwright") and self._playwright:
            await self._playwright.stop()

        logger.info(f"Sessao fechada para conta {self.conta_id}")

    def esta_ativa(self) -> bool:
        """Verifica se sessao esta ativa."""
        return self.page is not None and not self.page.is_closed()

    # ==================== LOGIN ====================

    async def garantir_login(self, page=None) -> bool:
        """Verifica sessao e faz login se necessario."""
        if page:
            self.page = page

        if await self._sessao_ativa():
            logger.info(f"Sessao ativa para conta {self.conta_id}")
            return True

        logger.info(f"Fazendo login para conta {self.conta_id}...")
        sucesso = await self._login_completo()

        if sucesso:
            await self._salvar_cookies()
            logger.info(f"Login OK para conta {self.conta_id}")
        else:
            logger.error(f"Login FALHOU para conta {self.conta_id}")

        return sucesso

    async def _sessao_ativa(self) -> bool:
        """Verifica se ainda esta logado."""
        try:
            await self.page.goto(
                "https://www.mercadolivre.com.br/my-account",
                wait_until="networkidle",
                timeout=15000,
            )
            await asyncio.sleep(2)
            url = self.page.url
            return "login" not in url and "auth" not in url and "lgz" not in url
        except Exception as e:
            logger.debug(f"Erro verificando sessao: {e}")
            return False

    async def _login_completo(self) -> bool:
        """Login com email, senha e 2FA."""
        if not self.email or not self.senha:
            logger.error(f"Credenciais nao configuradas para conta {self.conta_id}")
            return False

        try:
            # 1. Pagina de login
            await self.page.goto("https://www.mercadolivre.com.br/jms/mlb/lgz/login")
            await self._pausa(2, 4)

            # 2. Email
            await self.page.fill('input[name="user_id"]', self.email)
            await self._pausa(0.5, 1)
            await self.page.click('button[type="submit"]')
            await self.page.wait_for_load_state("networkidle")
            await self._pausa(2, 4)

            # 3. Senha
            campo_senha = await self.page.wait_for_selector(
                'input[type="password"]', timeout=10000
            )
            await campo_senha.fill(self.senha)
            await self._pausa(0.5, 1)
            await self.page.click('button[type="submit"]')
            await self.page.wait_for_load_state("networkidle")
            await self._pausa(3, 5)

            # 4. 2FA se solicitado
            await self._tratar_2fa()

            # 5. Verificar resultado
            await self._pausa(2, 3)
            url = self.page.url
            return "login" not in url and "lgz" not in url

        except Exception as e:
            logger.error(f"Erro no login: {e}")
            return False

    async def _tratar_2fa(self):
        """Preenche codigo TOTP se solicitado."""
        if not self.totp_secret:
            logger.warning("TOTP secret nao configurado para 2FA")
            return

        try:
            campo_2fa = await self.page.wait_for_selector(
                'input[name="otp"], input[inputmode="numeric"], input[name="code"]',
                timeout=8000,
            )

            if campo_2fa:
                totp = pyotp.TOTP(self.totp_secret)
                restante = totp.interval - (int(time.time()) % totp.interval)

                if restante < 5:
                    logger.info(f"TOTP expira em {restante}s, aguardando proximo ciclo")
                    await asyncio.sleep(restante + 1)

                codigo = totp.now()
                logger.info("Inserindo codigo TOTP...")
                await campo_2fa.fill(codigo)
                await self._pausa(0.5, 1)
                await self.page.click('button[type="submit"]')
                await self.page.wait_for_load_state("networkidle")

        except Exception:
            pass  # 2FA nao solicitado

    # ==================== KEEP-ALIVE ====================

    def iniciar_keep_alive(self, intervalo_minutos: int = 120):
        """Inicia keep-alive em background."""
        self._keep_alive_task = asyncio.create_task(
            self._keep_alive_loop(intervalo_minutos)
        )
        logger.info(f"Keep-alive ativo a cada {intervalo_minutos}min para conta {self.conta_id}")

    async def _keep_alive_loop(self, intervalo_min: int):
        """Loop de keep-alive."""
        while True:
            await asyncio.sleep(intervalo_min * 60)
            try:
                await self.page.goto("https://www.mercadolivre.com.br")
                await self._pausa(2, 4)
                await self.page.mouse.wheel(0, random.randint(200, 500))
                await self._pausa(1, 3)
                logger.debug(f"Keep-alive executado para conta {self.conta_id}")

                if not await self._sessao_ativa():
                    logger.warning(f"Sessao perdida! Re-login para conta {self.conta_id}")
                    await self._login_completo()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erro no keep-alive: {e}")

    # ==================== NAVEGACAO ====================

    async def ir_para_ads(self) -> bool:
        """Navega para o painel de publicidade."""
        try:
            await self.page.goto(
                "https://www.mercadolivre.com.br/advertising/home",
                wait_until="networkidle",
                timeout=30000,
            )
            await self._pausa(2, 4)

            if "login" in self.page.url or "lgz" in self.page.url:
                logger.warning("Redirecionado para login ao acessar ads")
                if await self._login_completo():
                    await self.page.goto(
                        "https://www.mercadolivre.com.br/advertising/home",
                        wait_until="networkidle",
                    )
                    await self._pausa(2, 3)
                else:
                    return False

            return True

        except Exception as e:
            logger.error(f"Erro ao navegar para ads: {e}")
            return False

    # ==================== COOKIES ====================

    async def _salvar_cookies(self):
        """Backup de cookies."""
        try:
            cookies = await self.context.cookies()
            self.cookies_file.parent.mkdir(parents=True, exist_ok=True)
            self.cookies_file.write_text(json.dumps(cookies, indent=2))
        except Exception as e:
            logger.debug(f"Erro salvando cookies: {e}")

    async def _restaurar_cookies(self) -> bool:
        """Restaura cookies de backup."""
        try:
            if self.cookies_file.exists():
                cookies = json.loads(self.cookies_file.read_text())
                await self.context.add_cookies(cookies)
                return True
        except Exception as e:
            logger.debug(f"Erro restaurando cookies: {e}")
        return False

    # ==================== UTILS ====================

    async def _pausa(self, min_s: float = 0.5, max_s: float = 2.0):
        """Pausa aleatoria."""
        await asyncio.sleep(random.uniform(min_s, max_s))
