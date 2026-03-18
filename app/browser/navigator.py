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
ML_ADS_HOME = "https://www.mercadolivre.com.br/publicidade"
ML_ADS_CAMPAIGNS = "https://www.mercadolivre.com.br/publicidade/campanhas"
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


async def buscar_campanha_por_nome(page, nome: str, max_paginas: int = 30) -> Optional[object]:
    """
    Busca uma campanha pelo nome passando pagina por pagina.
    Usa JS puro para varrer o DOM sem depender de seletores.
    """
    for pagina in range(max_paginas):
        await asyncio.sleep(0.8)

        # JS: varre todos os nos de texto e sobe ate encontrar uma "linha"
        handle = await page.evaluate_handle("""(nome) => {
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
                if (node.textContent.trim() === nome || node.textContent.includes(nome)) {
                    let el = node.parentElement;
                    for (let i = 0; i < 12; i++) {
                        if (!el) break;
                        const tag = el.tagName;
                        const role = el.getAttribute('role') || '';
                        const cls = el.className || '';
                        if (tag === 'TR' || role === 'row' || role === 'listitem' ||
                            cls.includes('row') || cls.includes('item') || cls.includes('campaign')) {
                            return el;
                        }
                        el = el.parentElement;
                    }
                    return node.parentElement;
                }
            }
            return null;
        }""", nome)

        if handle:
            el = handle.as_element()
            if el:
                logger.info(f"Campanha '{nome}' encontrada na pagina {pagina + 1}")
                return el

        # Tentar ir para proxima pagina
        foi = await _ir_proxima_pagina(page)
        if not foi:
            break

    logger.warning(f"Campanha '{nome}' nao encontrada em {max_paginas} paginas")
    return None


async def _ir_proxima_pagina(page) -> bool:
    """Clica no botao de proxima pagina. Retorna False se nao existe."""
    seletores_proximo = [
        "button[aria-label*='próxima' i]",
        "button[aria-label*='next' i]",
        "a[aria-label*='próxima' i]",
        "a[aria-label*='next' i]",
        "[class*='pagination'] button:last-child",
        "[class*='pagination'] li:last-child a",
        ".andes-pagination__button--next",
        "button[data-testid*='next']",
    ]
    for sel in seletores_proximo:
        try:
            btn = await page.query_selector(sel)
            if btn:
                disabled = await btn.get_attribute("disabled")
                if disabled is not None:
                    return False
                aria_disabled = await btn.get_attribute("aria-disabled")
                if aria_disabled == "true":
                    return False
                await btn.click()
                await asyncio.sleep(1.2)
                return True
        except Exception:
            continue
    return False


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
