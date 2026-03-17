"""
Navigator — Funcoes de navegacao no painel ML Ads.

Helpers para navegar entre paginas do painel de publicidade do Mercado Livre.
Usado pelo browser_executor para encontrar e interagir com campanhas/anuncios.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# URLs do painel ML Ads
ML_ADS_HOME = "https://www.mercadolivre.com.br/advertising/home"
ML_ADS_CAMPAIGNS = "https://www.mercadolivre.com.br/advertising/product-ads/campaigns"
ML_LOGIN = "https://www.mercadolivre.com.br/jms/mlb/lgz/login"


async def navegar_para_ads(page) -> bool:
    """
    Navega para a home do painel de ads.

    Returns:
        True se chegou na pagina corretamente.
    """
    try:
        await page.goto(ML_ADS_HOME, wait_until="networkidle", timeout=30000)
        await aguardar_carregamento(page)

        # Verificar se foi redirecionado para login
        if await _detectar_login_redirect(page):
            logger.warning("Redirecionado para login")
            return False

        logger.info("Navegou para painel de ads")
        return True

    except Exception as e:
        logger.error(f"Erro ao navegar para ads: {e}")
        return False


async def navegar_para_campanhas(page) -> bool:
    """Navega para a lista de campanhas."""
    try:
        await page.goto(ML_ADS_CAMPAIGNS, wait_until="networkidle", timeout=30000)
        await aguardar_carregamento(page)

        if await _detectar_login_redirect(page):
            return False

        logger.info("Navegou para lista de campanhas")
        return True

    except Exception as e:
        logger.error(f"Erro ao navegar para campanhas: {e}")
        return False


async def buscar_campanha_por_nome(page, nome: str, max_scroll: int = 5) -> Optional[object]:
    """
    Busca uma campanha na lista pelo nome.
    Faz scroll progressivo se necessario.

    Args:
        page: pagina Playwright
        nome: nome (ou parte) da campanha
        max_scroll: maximo de scrolls para buscar

    Returns:
        Elemento da campanha ou None
    """
    for tentativa in range(max_scroll):
        # Buscar todas as linhas de campanha visiveis
        # Seletores serao definidos em selectors.py (Fase 3)
        linhas = await page.query_selector_all("[data-testid='campaign-row'], .campaign-row, tr[class*='campaign']")

        for linha in linhas:
            texto = await linha.inner_text()
            if nome.lower() in texto.lower():
                logger.info(f"Campanha encontrada: {nome}")
                return linha

        # Scroll para baixo para carregar mais
        await page.evaluate("window.scrollBy(0, 500)")
        await asyncio.sleep(0.5)

    logger.warning(f"Campanha nao encontrada: {nome}")
    return None


async def navegar_para_config_campanha(page, campaign_name: str) -> bool:
    """
    Navega para a pagina de configuracao de uma campanha especifica.

    Args:
        page: pagina Playwright
        campaign_name: nome da campanha

    Returns:
        True se chegou na pagina de config
    """
    # Primeiro, ir para lista de campanhas
    if not await navegar_para_campanhas(page):
        return False

    # Buscar e clicar na campanha
    elemento = await buscar_campanha_por_nome(page, campaign_name)
    if not elemento:
        return False

    try:
        # Clicar no nome da campanha (geralmente e um link)
        link = await elemento.query_selector("a, [role='link']")
        if link:
            await link.click()
        else:
            await elemento.click()

        await aguardar_carregamento(page)
        logger.info(f"Navegou para config da campanha: {campaign_name}")
        return True

    except Exception as e:
        logger.error(f"Erro ao navegar para config de {campaign_name}: {e}")
        return False


async def aguardar_carregamento(page, timeout_ms: int = 10000):
    """
    Aguarda a pagina terminar de carregar.
    Espera spinners/loading sumirem.
    """
    try:
        # Esperar spinners comuns sumirem
        spinners = [
            ".andes-spinner",
            "[class*='loading']",
            "[class*='spinner']",
            "[data-testid='loading']",
        ]

        for seletor in spinners:
            try:
                spinner = await page.query_selector(seletor)
                if spinner:
                    await page.wait_for_selector(
                        seletor,
                        state="hidden",
                        timeout=timeout_ms,
                    )
            except Exception:
                pass  # Spinner nao encontrado, OK

        # Esperar network ficar idle
        await asyncio.sleep(0.5)

    except Exception as e:
        logger.debug(f"Timeout ao aguardar carregamento: {e}")


async def tratar_redirect_login(page, session_manager) -> bool:
    """
    Detecta e trata redirect para pagina de login.

    Args:
        page: pagina Playwright
        session_manager: instancia do MLSessionManager

    Returns:
        True se conseguiu re-logar
    """
    if not await _detectar_login_redirect(page):
        return True  # Nao houve redirect

    logger.warning("Detectado redirect para login, re-autenticando...")

    try:
        sucesso = await session_manager.garantir_login(page)
        if sucesso:
            logger.info("Re-login bem sucedido")
            return True
        else:
            logger.error("Falha no re-login")
            return False
    except Exception as e:
        logger.error(f"Erro no re-login: {e}")
        return False


async def _detectar_login_redirect(page) -> bool:
    """Verifica se a pagina atual e a pagina de login."""
    try:
        url = page.url
        if "login" in url.lower() or "lgz" in url.lower():
            return True

        # Verificar elementos de login
        login_form = await page.query_selector(
            "input[name='user_id'], #user_id, .login-form"
        )
        return login_form is not None

    except Exception:
        return False


async def screenshot_acao(page, nome: str, screenshot_dir: str = "./screenshots") -> Optional[str]:
    """
    Tira screenshot da acao executada.

    Args:
        page: pagina Playwright
        nome: nome descritivo para o arquivo
        screenshot_dir: diretorio de screenshots

    Returns:
        Caminho do arquivo ou None
    """
    from pathlib import Path
    from datetime import datetime

    try:
        dir_path = Path(screenshot_dir)
        dir_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_limpo = "".join(c if c.isalnum() or c in "-_" else "_" for c in nome)
        filepath = dir_path / f"{timestamp}_{nome_limpo}.png"

        await page.screenshot(path=str(filepath), full_page=False)
        logger.info(f"Screenshot salvo: {filepath.name}")
        return str(filepath)

    except Exception as e:
        logger.error(f"Erro ao tirar screenshot: {e}")
        return None
