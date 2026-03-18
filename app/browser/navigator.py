"""
Navigator — Funcoes de navegacao no painel ML Ads.

Helpers para navegar entre paginas do painel de publicidade do Mercado Livre.
Usado pelo browser_executor para encontrar e interagir com campanhas/anuncios.
"""

import asyncio
import logging
import random
from typing import Optional

logger = logging.getLogger(__name__)

# URLs do painel ML Ads
ML_ADS_HOME = "https://ads.mercadolivre.com.br/"
ML_ADS_CAMPAIGNS = "https://ads.mercadolivre.com.br/"
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
    """Navega para a lista de campanhas de Product Ads."""
    try:
        await page.goto(ML_ADS_HOME, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        if await _detectar_login_redirect(page):
            return False

        # Se ja esta na pagina de campanhas, ok
        url_atual = page.url.lower()
        if "product-ads" in url_atual and ("campaign" in url_atual or "product-ads" in url_atual):
            logger.info(f"Ja esta na pagina de Product Ads: {page.url}")
            await _aguardar_campanhas_carregar(page)
            return True

        # Aguardar SPA carregar (ate 8s)
        await asyncio.sleep(4)

        logger.info(f"Na pagina: {page.url} — buscando link 'Ir para Product Ads'")

        # Log diagnostico: listar todos os hrefs com 'ads' ou 'product' na pagina
        todos_links = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                txt: (a.innerText || a.textContent || '').trim().substring(0, 60),
                href: a.href,
            })).filter(x => x.href && (
                x.href.includes('product') || x.href.includes('ads') ||
                x.txt.toLowerCase().includes('product') || x.txt.toLowerCase().includes('ads')
            )).slice(0, 10);
        }""")
        logger.info(f"Links encontrados na pagina: {todos_links}")

        # Pegar o href do link "Ir para Product Ads"
        href = await page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a[href]'));
            for (const a of links) {
                const txt = (a.innerText || a.textContent || '').trim();
                if (txt.includes('Product Ads')) {
                    return a.href;
                }
            }
            // Fallback: qualquer link com 'product-ads' no href
            for (const a of links) {
                if (a.href && a.href.includes('product-ads')) {
                    return a.href;
                }
            }
            return null;
        }""")

        if href:
            logger.info(f"Link encontrado: {href}")
            await page.goto(href, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)
            await _aguardar_campanhas_carregar(page)
            logger.info(f"Navegou para: {page.url}")
            return True

        logger.warning(f"Link 'Ir para Product Ads' nao encontrado. URL atual: {page.url}")
        return False

    except Exception as e:
        logger.error(f"Erro ao navegar para campanhas: {e}")
        return False


async def _aguardar_campanhas_carregar(page, timeout_s: int = 15):
    """Aguarda a lista de campanhas aparecer no DOM (linhas de tabela)."""
    import time as _time
    deadline = _time.time() + timeout_s
    while _time.time() < deadline:
        try:
            contagem = await page.evaluate("""() => {
                const sels = ['tr', '[role="row"]', '[class*="campaign"]', '[class*="row"]'];
                for (const s of sels) {
                    const els = document.querySelectorAll(s);
                    if (els.length > 2) return els.length;
                }
                return 0;
            }""")
            if contagem > 2:
                logger.info(f"Campanhas carregadas na pagina ({contagem} linhas)")
                await asyncio.sleep(0.5)
                return
        except Exception:
            pass
        await asyncio.sleep(1)
    logger.warning("Timeout aguardando campanhas carregar — continuando mesmo assim")


async def varrer_paginas_e_processar(page, human, nomes_alvo: set, pausar: bool, max_paginas: int = 50) -> dict:
    """
    Abre lista de campanhas, vai pagina por pagina.
    Em cada pagina, verifica quais campanhas estao na lista alvo e pausa/ativa.
    Retorna {nome: True/False} com resultado de cada uma.

    Esse e o modo eficiente: uma unica passagem por todas as paginas,
    processando todos os matches de uma vez (como um humano faria com planilha).
    """
    resultados = {}
    restantes = set(nomes_alvo)
    verbo = "pausar" if pausar else "ativar"

    for pagina in range(max_paginas):
        if not restantes:
            break

        await asyncio.sleep(0.8)

        # JS: coleta todos os textos de linhas visíveis na página
        dados_pagina = await page.evaluate("""() => {
            const linhas = [];
            const candidatos = document.querySelectorAll(
                'tr, [role="row"], [class*="row"], [class*="campaign"], li'
            );
            for (const el of candidatos) {
                const txt = el.innerText || '';
                if (txt.trim().length > 0 && txt.trim().length < 500) {
                    linhas.push(txt.trim());
                }
            }
            return linhas;
        }""")

        # Log diagnostico: primeiros 3 textos e URL atual
        logger.info(
            f"Pagina {pagina+1} | URL: {page.url} | {len(dados_pagina)} linhas | "
            f"Buscando: {list(restantes)[:5]} | "
            f"Exemplos: {[t[:60] for t in dados_pagina[:3]]}"
        )

        # Verificar quais nomes da lista aparecem nessa página
        matches_pagina = []
        for texto in dados_pagina:
            for nome in list(restantes):
                if nome in texto:
                    matches_pagina.append(nome)
                    break

        if matches_pagina:
            logger.info(f"Pagina {pagina+1}: encontradas {len(matches_pagina)} campanhas — {matches_pagina}")

        # Para cada match, encontrar o elemento e clicar no toggle
        for nome in matches_pagina:
            try:
                handle = await page.evaluate_handle("""(nome) => {
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    let node;
                    while ((node = walker.nextNode())) {
                        if (node.textContent.includes(nome)) {
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

                linha = handle.as_element() if handle else None
                if not linha:
                    continue

                # Encontrar toggle na linha
                toggle = await _encontrar_toggle(linha)
                if not toggle:
                    logger.warning(f"Toggle nao encontrado para '{nome}'")
                    resultados[nome] = False
                    continue

                # Verificar estado atual e clicar se necessário
                esta_ativo = await _toggle_ativo(toggle)
                if pausar and not esta_ativo:
                    logger.info(f"'{nome}' ja esta pausada")
                    resultados[nome] = True
                    restantes.discard(nome)
                    continue
                if not pausar and esta_ativo:
                    logger.info(f"'{nome}' ja esta ativa")
                    resultados[nome] = True
                    restantes.discard(nome)
                    continue

                await human.clicar_elemento(toggle)
                await asyncio.sleep(random.uniform(3, 5))

                # Confirmar dialog se aparecer
                await _confirmar_dialog_nav(page, human)
                await asyncio.sleep(0.5)

                logger.info(f"Campanha '{nome}' {verbo} com sucesso")
                resultados[nome] = True
                restantes.discard(nome)

            except Exception as e:
                logger.error(f"Erro ao {verbo} '{nome}': {e}")
                resultados[nome] = False

        # Próxima página
        foi = await _ir_proxima_pagina(page)
        if not foi:
            logger.info(f"Ultima pagina ({pagina+1}). Restantes nao encontradas: {restantes}")
            break

    # Marcar restantes como não encontradas
    for nome in restantes:
        if nome not in resultados:
            resultados[nome] = False

    return resultados


async def _encontrar_toggle(linha) -> Optional[object]:
    # Seletor real: input[data-testid="switch-status"] dentro da linha
    seletores = [
        "input[data-testid='switch-status']",
        "input.andes-switch__input[role='switch']",
        "label.campaign-status-switch input",
        "label.andes-switch input",
        "input[role='switch']",
        ".andes-switch__input",
    ]
    for sel in seletores:
        try:
            el = await linha.query_selector(sel)
            if el:
                return el
        except Exception:
            continue
    return None


async def _toggle_ativo(toggle) -> bool:
    try:
        # O input tem checked="" quando ativo, ausente quando pausado
        checked = await toggle.get_attribute("checked")
        if checked is not None:
            return True  # atributo presente = ativo
        # Verificar via propriedade JS
        is_checked = await toggle.evaluate("el => el.checked")
        return bool(is_checked)
    except Exception:
        return False


async def _confirmar_dialog_nav(page, human) -> None:
    try:
        seletores = [
            "button[class*='confirm']", "button[class*='primary']",
            ".andes-modal button", "button:has-text('Confirmar')",
            "button:has-text('Pausar')", "button:has-text('OK')",
        ]
        for sel in seletores:
            btn = await page.query_selector(sel)
            if btn:
                await human.clicar_elemento(btn)
                await asyncio.sleep(0.5)
                return
    except Exception:
        pass


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
    """Clica no botao 'Seguinte' da paginacao. Retorna False se nao existe ou esta desabilitado."""
    try:
        # Seletor real: <a class="andes-pagination__link">Seguinte</a>
        btn = await page.evaluate_handle("""() => {
            const links = Array.from(document.querySelectorAll('a.andes-pagination__link'));
            return links.find(a => (a.innerText || '').trim() === 'Seguinte') || null;
        }""")
        el = btn.as_element() if btn else None
        if el:
            disabled = await el.get_attribute("disabled")
            aria_disabled = await el.get_attribute("aria-disabled")
            if disabled is not None or aria_disabled == "true":
                return False
            await el.click()
            await asyncio.sleep(1.5)
            return True
    except Exception:
        pass

    # Fallbacks adicionais
    seletores_proximo = [
        "button[aria-label*='próxima' i]",
        "button[aria-label*='next' i]",
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
                await btn.click()
                await asyncio.sleep(1.5)
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
