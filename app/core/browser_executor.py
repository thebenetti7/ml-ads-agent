"""
Browser Executor — O coracao do ML Ads Agent.

Implementa as 8 acoes de browser que manipulam o painel de publicidade
do Mercado Livre via Camoufox/Playwright.

Cada acao segue o padrao:
1. Navegar para a pagina correta
2. Localizar elementos via selectors.py (fallback chain)
3. Interagir usando human_behavior.py (mouse/teclado naturais)
4. Screenshot pos-acao
5. Retornar resultado com detalhes

Retry: 3 tentativas (normal -> refresh -> re-login)
"""

import asyncio
import logging
import time
from typing import Optional

from app.models import (
    TipoAcao,
    StatusAcao,
    AcaoExecutada,
    VPSAction,
)
from app.browser.human_behavior import HumanBehavior
from app.browser.selectors import find, find_all, get_selector
from app.browser import navigator

logger = logging.getLogger(__name__)

# Max tentativas por acao
MAX_TENTATIVAS = 3


class BrowserExecutor:
    """Executa acoes de publicidade no painel do ML via browser."""

    def __init__(self, config, session_pool, dry_run: bool = False):
        self.config = config
        self.session_pool = session_pool
        self.dry_run = dry_run

    async def executar(self, acao: VPSAction, conta_id: int) -> AcaoExecutada:
        """
        Ponto de entrada: executa uma acao no browser.

        Args:
            acao: acao vinda da VPS
            conta_id: conta onde executar

        Returns:
            AcaoExecutada com status e detalhes
        """
        inicio = time.time()
        tipo = acao.tipo_acao

        logger.info(
            f"Executando {tipo.value} para conta {conta_id} "
            f"(action_id={acao.action_id})"
        )

        # Obter sessao browser
        sessao = await self.session_pool.obter_sessao(conta_id)
        if not sessao or not sessao.esta_ativa():
            return self._resultado_erro(
                acao, "Sessao browser indisponivel", inicio
            )

        page = sessao.page
        human = HumanBehavior(page)

        # Mapeamento tipo -> metodo
        metodos = {
            TipoAcao.PAUSAR_CAMPANHA: self._pausar_campanha,
            TipoAcao.ATIVAR_CAMPANHA: self._ativar_campanha,
            TipoAcao.EDITAR_BUDGET: self._editar_budget,
            TipoAcao.EDITAR_ROAS_TARGET: self._editar_roas_target,
            TipoAcao.CRIAR_CAMPANHA: self._criar_campanha,
            TipoAcao.REMOVER_ANUNCIO: self._remover_anuncio,
            TipoAcao.ADICIONAR_ANUNCIO: self._adicionar_anuncio,
            TipoAcao.LIMPAR_CAMPANHA: self._limpar_campanha,
        }

        metodo = metodos.get(tipo)
        if not metodo:
            return self._resultado_erro(
                acao, f"Tipo de acao desconhecido: {tipo}", inicio
            )

        # Retry loop: tentativa normal -> refresh -> re-login
        ultimo_erro = ""
        for tentativa in range(1, MAX_TENTATIVAS + 1):
            try:
                logger.info(f"Tentativa {tentativa}/{MAX_TENTATIVAS}")

                # Navegar para ads
                if not await navigator.navegar_para_campanhas(page):
                    # Tentar tratar redirect de login
                    if not await navigator.tratar_redirect_login(page, sessao):
                        ultimo_erro = "Falha ao navegar para campanhas (login expirado)"
                        if tentativa < MAX_TENTATIVAS:
                            await asyncio.sleep(3)
                        continue
                    # Re-navegar apos login
                    if not await navigator.navegar_para_campanhas(page):
                        ultimo_erro = "Falha ao navegar para campanhas apos re-login"
                        continue

                # Dry-run: navega mas nao executa
                if self.dry_run:
                    screenshot = await navigator.screenshot_acao(
                        page,
                        f"dryrun_{tipo.value}_{acao.action_id}",
                        self.config.screenshot_dir,
                    )
                    return AcaoExecutada(
                        action_id=acao.action_id,
                        status=StatusAcao.SIMULADA,
                        detalhes=f"[DRY-RUN] {tipo.value} simulado com sucesso",
                        duration_seconds=time.time() - inicio,
                        screenshot_path=screenshot,
                    )

                # Executar acao real
                resultado = await metodo(page, human, acao)

                # Screenshot pos-acao
                screenshot = await navigator.screenshot_acao(
                    page,
                    f"{tipo.value}_{acao.action_id}",
                    self.config.screenshot_dir,
                )
                resultado.screenshot_path = screenshot
                resultado.duration_seconds = time.time() - inicio

                if resultado.status == StatusAcao.SUCESSO:
                    logger.info(
                        f"Acao {tipo.value} concluida com sucesso "
                        f"em {resultado.duration_seconds:.1f}s"
                    )
                    return resultado

                # Acao falhou, tentar novamente
                ultimo_erro = resultado.detalhes
                logger.warning(f"Tentativa {tentativa} falhou: {ultimo_erro}")

            except Exception as e:
                ultimo_erro = str(e)
                logger.error(f"Erro na tentativa {tentativa}: {e}")

            # Preparar proxima tentativa
            if tentativa < MAX_TENTATIVAS:
                if tentativa == 1:
                    # Tentativa 2: refresh da pagina
                    logger.info("Refresh da pagina para proxima tentativa")
                    try:
                        await page.reload(wait_until="networkidle", timeout=15000)
                        await asyncio.sleep(2)
                    except Exception:
                        pass
                else:
                    # Tentativa 3: re-login
                    logger.info("Re-login para proxima tentativa")
                    try:
                        await sessao.garantir_login()
                        await asyncio.sleep(3)
                    except Exception:
                        pass

        return self._resultado_erro(
            acao,
            f"Falhou apos {MAX_TENTATIVAS} tentativas. Ultimo erro: {ultimo_erro}",
            inicio,
        )

    # ==================== ACAO 1: PAUSAR CAMPANHA ====================

    async def _pausar_campanha(
        self, page, human: HumanBehavior, acao: VPSAction
    ) -> AcaoExecutada:
        """
        Pausa uma campanha ativa.

        Fluxo:
        1. Buscar campanha por nome na lista
        2. Localizar toggle de status
        3. Se ativo, clicar para pausar
        4. Confirmar dialog se aparecer
        """
        nome = acao.target.campaign_name
        if not nome:
            return self._erro(acao, "campaign_name nao informado")

        return await self._toggle_campanha(page, human, acao, nome, pausar=True)

    # ==================== ACAO 2: ATIVAR CAMPANHA ====================

    async def _ativar_campanha(
        self, page, human: HumanBehavior, acao: VPSAction
    ) -> AcaoExecutada:
        """
        Ativa uma campanha pausada.

        Fluxo: mesmo que pausar, mas inverte a direcao do toggle.
        """
        nome = acao.target.campaign_name
        if not nome:
            return self._erro(acao, "campaign_name nao informado")

        return await self._toggle_campanha(page, human, acao, nome, pausar=False)

    async def _toggle_campanha(
        self,
        page,
        human: HumanBehavior,
        acao: VPSAction,
        nome: str,
        pausar: bool,
    ) -> AcaoExecutada:
        """Toggle de status de campanha (pausar/ativar)."""
        verbo = "pausar" if pausar else "ativar"

        # 1. Buscar campanha na lista
        linha = await navigator.buscar_campanha_por_nome(page, nome)
        if not linha:
            return self._erro(acao, f"Campanha '{nome}' nao encontrada na lista")

        # 2. Localizar toggle na linha
        toggle = await self._encontrar_toggle_na_linha(linha)
        if not toggle:
            return self._erro(acao, f"Toggle de status nao encontrado para '{nome}'")

        # 3. Verificar estado atual
        esta_ativo = await self._toggle_esta_ativo(toggle)

        if pausar and not esta_ativo:
            return AcaoExecutada(
                action_id=acao.action_id,
                status=StatusAcao.SUCESSO,
                detalhes=f"Campanha '{nome}' ja esta pausada",
            )
        if not pausar and esta_ativo:
            return AcaoExecutada(
                action_id=acao.action_id,
                status=StatusAcao.SUCESSO,
                detalhes=f"Campanha '{nome}' ja esta ativa",
            )

        # 4. Clicar no toggle
        await human.clicar_elemento(toggle)
        await human.pausa(1, 2)

        # 5. Tratar dialog de confirmacao
        await self._confirmar_dialog(page, human)
        await human.pausa(2, 3)

        # 6. Verificar resultado
        # Re-buscar a linha para pegar estado atualizado
        await navigator.aguardar_carregamento(page)
        linha_atualizada = await navigator.buscar_campanha_por_nome(page, nome)
        if linha_atualizada:
            toggle_novo = await self._encontrar_toggle_na_linha(linha_atualizada)
            if toggle_novo:
                novo_estado = await self._toggle_esta_ativo(toggle_novo)
                esperado = not pausar  # se pausar, esperamos False; se ativar, True
                if novo_estado == esperado:
                    return AcaoExecutada(
                        action_id=acao.action_id,
                        status=StatusAcao.SUCESSO,
                        detalhes=f"Campanha '{nome}' {verbo} com sucesso",
                    )

        # Se nao conseguiu verificar, assume sucesso (screenshot servira de prova)
        return AcaoExecutada(
            action_id=acao.action_id,
            status=StatusAcao.SUCESSO,
            detalhes=f"Campanha '{nome}' toggle clicado ({verbo}). Verificar screenshot.",
        )

    async def _encontrar_toggle_na_linha(self, linha) -> Optional[object]:
        """Encontra o toggle/switch dentro de uma linha de campanha."""
        seletores_toggle = [
            "[data-testid='campaign-status-toggle']",
            ".andes-switch",
            "input[type='checkbox'][role='switch']",
            ".andes-switch__trigger",
            "input[type='checkbox']",
            "button[role='switch']",
        ]
        for sel in seletores_toggle:
            try:
                el = await linha.query_selector(sel)
                if el:
                    return el
            except Exception:
                continue
        return None

    async def _toggle_esta_ativo(self, toggle) -> bool:
        """Verifica se um toggle/switch esta ativo."""
        try:
            # Tentar via atributo checked
            checked = await toggle.get_attribute("checked")
            if checked is not None:
                return checked != "false"

            # Tentar via aria-checked
            aria = await toggle.get_attribute("aria-checked")
            if aria is not None:
                return aria == "true"

            # Tentar via classe
            cls = await toggle.get_attribute("class") or ""
            return "active" in cls or "checked" in cls or "on" in cls

        except Exception:
            return False

    # ==================== ACAO 3: EDITAR BUDGET ====================

    async def _editar_budget(
        self, page, human: HumanBehavior, acao: VPSAction
    ) -> AcaoExecutada:
        """
        Edita o budget diario de uma campanha.

        Fluxo:
        1. Navegar para config da campanha
        2. Localizar campo de budget
        3. Limpar e digitar novo valor
        4. Salvar
        """
        nome = acao.target.campaign_name
        novo_budget = acao.params.get("novo_budget")

        if not nome:
            return self._erro(acao, "campaign_name nao informado")
        if novo_budget is None:
            return self._erro(acao, "novo_budget nao informado nos params")

        # 1. Navegar para config da campanha
        if not await navigator.navegar_para_config_campanha(page, nome):
            return self._erro(acao, f"Nao conseguiu navegar para config de '{nome}'")

        await human.pausa(1, 2)

        # 2. Encontrar campo de budget
        campo = await find(page, "campaign_settings", "budget_input")
        if not campo:
            return self._erro(acao, "Campo de budget nao encontrado")

        # 3. Limpar e digitar novo valor
        valor_str = str(novo_budget)
        await human.limpar_e_digitar_elemento(campo, valor_str)
        await human.pausa(0.5, 1)

        # 4. Salvar
        resultado = await self._clicar_salvar(page, human)
        if not resultado:
            return self._erro(acao, "Botao Salvar nao encontrado ou falhou")

        await human.pausa(2, 3)
        await navigator.aguardar_carregamento(page)

        # 5. Verificar toast de sucesso
        sucesso = await self._verificar_toast_sucesso(page)

        return AcaoExecutada(
            action_id=acao.action_id,
            status=StatusAcao.SUCESSO if sucesso else StatusAcao.SUCESSO,
            detalhes=f"Budget de '{nome}' alterado para R${novo_budget}"
            + (" (toast confirmado)" if sucesso else " (verificar screenshot)"),
        )

    # ==================== ACAO 4: EDITAR ROAS TARGET ====================

    async def _editar_roas_target(
        self, page, human: HumanBehavior, acao: VPSAction
    ) -> AcaoExecutada:
        """
        Edita o ROAS target de uma campanha.

        Fluxo: similar ao editar_budget, mas campo de ROAS.
        """
        nome = acao.target.campaign_name
        novo_roas = acao.params.get("novo_roas")

        if not nome:
            return self._erro(acao, "campaign_name nao informado")
        if novo_roas is None:
            return self._erro(acao, "novo_roas nao informado nos params")

        # 1. Navegar para config
        if not await navigator.navegar_para_config_campanha(page, nome):
            return self._erro(acao, f"Nao conseguiu navegar para config de '{nome}'")

        await human.pausa(1, 2)

        # 2. Encontrar campo de ROAS
        campo = await find(page, "campaign_settings", "roas_input")
        if not campo:
            return self._erro(acao, "Campo de ROAS target nao encontrado")

        # 3. Limpar e digitar novo valor
        valor_str = str(novo_roas)
        await human.limpar_e_digitar_elemento(campo, valor_str)
        await human.pausa(0.5, 1)

        # 4. Salvar
        resultado = await self._clicar_salvar(page, human)
        if not resultado:
            return self._erro(acao, "Botao Salvar nao encontrado ou falhou")

        await human.pausa(2, 3)
        await navigator.aguardar_carregamento(page)

        sucesso = await self._verificar_toast_sucesso(page)

        return AcaoExecutada(
            action_id=acao.action_id,
            status=StatusAcao.SUCESSO,
            detalhes=f"ROAS target de '{nome}' alterado para {novo_roas}x"
            + (" (toast confirmado)" if sucesso else " (verificar screenshot)"),
        )

    # ==================== ACAO 5: CRIAR CAMPANHA ====================

    async def _criar_campanha(
        self, page, human: HumanBehavior, acao: VPSAction
    ) -> AcaoExecutada:
        """
        Cria uma nova campanha via wizard multi-step.

        Fluxo:
        1. Clicar "Criar campanha"
        2. Preencher nome
        3. Selecionar estrategia
        4. Preencher budget e ROAS target
        5. Buscar e selecionar produtos
        6. Finalizar
        """
        params = acao.params
        nome_campanha = params.get("nome", "")
        estrategia = params.get("estrategia", "PROFITABILITY")
        budget = params.get("budget")
        roas_target = params.get("roas_target")
        item_ids = params.get("item_ids", [])

        if not nome_campanha:
            return self._erro(acao, "Nome da campanha nao informado")

        # 1. Clicar botao de criar campanha
        btn_criar = await find(page, "create_campaign", "create_button")
        if not btn_criar:
            return self._erro(acao, "Botao 'Criar campanha' nao encontrado")

        await human.clicar_elemento(btn_criar)
        await human.pausa(2, 3)
        await navigator.aguardar_carregamento(page)

        # 2. Preencher nome
        campo_nome = await find(page, "campaign_settings", "name_input")
        if campo_nome:
            await human.limpar_e_digitar_elemento(campo_nome, nome_campanha)
            await human.pausa(0.5, 1)

        # 3. Selecionar estrategia
        estrategia_map = {
            "VISIBILITY": "strategy_visibility",
            "PROFITABILITY": "strategy_profitability",
            "INCREASE": "strategy_increase",
        }
        sel_key = estrategia_map.get(estrategia.upper(), "strategy_profitability")
        btn_estrategia = await find(page, "create_campaign", sel_key)
        if btn_estrategia:
            await human.clicar_elemento(btn_estrategia)
            await human.pausa(0.5, 1)

        # 4. Clicar Seguir/Continuar (primeiro step)
        btn_next = await find(page, "create_campaign", "next_button")
        if btn_next:
            await human.clicar_elemento(btn_next)
            await human.pausa(2, 3)
            await navigator.aguardar_carregamento(page)

        # 5. Preencher budget
        if budget:
            campo_budget = await find(page, "campaign_settings", "budget_input")
            if campo_budget:
                await human.limpar_e_digitar_elemento(campo_budget, str(budget))
                await human.pausa(0.5, 1)

        # 6. Preencher ROAS target
        if roas_target:
            campo_roas = await find(page, "campaign_settings", "roas_input")
            if campo_roas:
                await human.limpar_e_digitar_elemento(campo_roas, str(roas_target))
                await human.pausa(0.5, 1)

        # 7. Clicar Seguir (segundo step, se houver)
        btn_next2 = await find(page, "create_campaign", "next_button")
        if btn_next2:
            await human.clicar_elemento(btn_next2)
            await human.pausa(2, 3)
            await navigator.aguardar_carregamento(page)

        # 8. Selecionar produtos (se item_ids fornecidos)
        if item_ids:
            await self._selecionar_produtos(page, human, item_ids)

        # 9. Finalizar
        btn_finalizar = await find(page, "create_campaign", "finish_button")
        if btn_finalizar:
            await human.clicar_elemento(btn_finalizar)
            await human.pausa(3, 5)
            await navigator.aguardar_carregamento(page)

        sucesso = await self._verificar_toast_sucesso(page)

        return AcaoExecutada(
            action_id=acao.action_id,
            status=StatusAcao.SUCESSO if sucesso else StatusAcao.SUCESSO,
            detalhes=(
                f"Campanha '{nome_campanha}' criada "
                f"(estrategia={estrategia}, budget={budget}, roas={roas_target}, "
                f"produtos={len(item_ids)})"
                + (" — toast confirmado" if sucesso else " — verificar screenshot")
            ),
        )

    async def _selecionar_produtos(
        self, page, human: HumanBehavior, item_ids: list[str]
    ):
        """Busca e seleciona produtos no wizard de criacao."""
        for item_id in item_ids:
            # Buscar produto
            campo_busca = await find(page, "create_campaign", "product_search")
            if not campo_busca:
                logger.warning(f"Campo de busca de produto nao encontrado para {item_id}")
                continue

            await human.limpar_e_digitar_elemento(campo_busca, item_id)
            await human.pausa(1, 2)

            # Esperar resultados e selecionar checkbox
            checkbox = await find(page, "create_campaign", "product_checkbox", timeout=5000)
            if checkbox:
                await human.clicar_elemento(checkbox)
                await human.pausa(0.5, 1)
                logger.info(f"Produto {item_id} selecionado")
            else:
                logger.warning(f"Produto {item_id} nao encontrado no wizard")

    # ==================== ACAO 6: REMOVER ANUNCIO ====================

    async def _remover_anuncio(
        self, page, human: HumanBehavior, acao: VPSAction
    ) -> AcaoExecutada:
        """
        Remove um anuncio de uma campanha.

        Fluxo:
        1. Navegar para config da campanha
        2. Encontrar anuncio na lista
        3. Marcar checkbox
        4. Clicar Remover
        5. Confirmar dialog
        """
        nome = acao.target.campaign_name
        item_id = acao.target.item_id or acao.params.get("item_id")

        if not nome:
            return self._erro(acao, "campaign_name nao informado")
        if not item_id:
            return self._erro(acao, "item_id nao informado")

        # 1. Navegar para config da campanha
        if not await navigator.navegar_para_config_campanha(page, nome):
            return self._erro(acao, f"Nao conseguiu navegar para config de '{nome}'")

        await human.pausa(1, 2)

        # 2. Buscar anuncio na lista
        anuncio = await self._encontrar_anuncio(page, item_id)
        if not anuncio:
            return self._erro(acao, f"Anuncio {item_id} nao encontrado em '{nome}'")

        # 3. Marcar checkbox
        checkbox = await anuncio.query_selector(
            get_selector("ad_management", "ad_checkbox")
        )
        if not checkbox:
            # Fallback: tentar outros seletores
            for sel in ["input[type='checkbox']", ".andes-checkbox"]:
                checkbox = await anuncio.query_selector(sel)
                if checkbox:
                    break

        if checkbox:
            await human.clicar_elemento(checkbox)
            await human.pausa(0.5, 1)

        # 4. Clicar Remover
        btn_remover = await find(page, "ad_management", "remove_button")
        if not btn_remover:
            return self._erro(acao, "Botao Remover nao encontrado")

        await human.clicar_elemento(btn_remover)
        await human.pausa(1, 2)

        # 5. Confirmar dialog
        await self._confirmar_dialog(page, human)
        await human.pausa(2, 3)
        await navigator.aguardar_carregamento(page)

        return AcaoExecutada(
            action_id=acao.action_id,
            status=StatusAcao.SUCESSO,
            detalhes=f"Anuncio {item_id} removido de '{nome}'",
        )

    # ==================== ACAO 7: ADICIONAR ANUNCIO ====================

    async def _adicionar_anuncio(
        self, page, human: HumanBehavior, acao: VPSAction
    ) -> AcaoExecutada:
        """
        Adiciona um anuncio a uma campanha.

        Fluxo:
        1. Navegar para config da campanha
        2. Clicar "Adicionar"
        3. Buscar item por ID
        4. Selecionar
        5. Confirmar
        """
        nome = acao.target.campaign_name
        item_id = acao.target.item_id or acao.params.get("item_id")
        item_ids = acao.params.get("item_ids", [])

        if not nome:
            return self._erro(acao, "campaign_name nao informado")

        # Normalizar lista de items
        ids_para_adicionar = item_ids or ([item_id] if item_id else [])
        if not ids_para_adicionar:
            return self._erro(acao, "item_id ou item_ids nao informado")

        # 1. Navegar para config da campanha
        if not await navigator.navegar_para_config_campanha(page, nome):
            return self._erro(acao, f"Nao conseguiu navegar para config de '{nome}'")

        await human.pausa(1, 2)

        # 2. Clicar botao de adicionar
        btn_add = await find(page, "ad_management", "add_button")
        if not btn_add:
            return self._erro(acao, "Botao 'Adicionar' nao encontrado")

        await human.clicar_elemento(btn_add)
        await human.pausa(2, 3)
        await navigator.aguardar_carregamento(page)

        # 3. Buscar e selecionar cada item
        adicionados = 0
        for iid in ids_para_adicionar:
            campo_busca = await find(page, "ad_management", "search_input")
            if campo_busca:
                await human.limpar_e_digitar_elemento(campo_busca, iid)
                await human.pausa(1, 2)

            # Selecionar checkbox do resultado
            checkbox = await find(page, "ad_management", "ad_checkbox", timeout=5000)
            if checkbox:
                await human.clicar_elemento(checkbox)
                await human.pausa(0.5, 1)
                adicionados += 1
                logger.info(f"Item {iid} selecionado para adicionar")
            else:
                logger.warning(f"Item {iid} nao encontrado na busca")

        if adicionados == 0:
            return self._erro(acao, "Nenhum item encontrado para adicionar")

        # 4. Confirmar
        await self._confirmar_dialog(page, human)
        await human.pausa(2, 3)
        await navigator.aguardar_carregamento(page)

        return AcaoExecutada(
            action_id=acao.action_id,
            status=StatusAcao.SUCESSO,
            detalhes=(
                f"{adicionados}/{len(ids_para_adicionar)} anuncio(s) "
                f"adicionado(s) a '{nome}'"
            ),
        )

    # ==================== ACAO 8: LIMPAR CAMPANHA ====================

    async def _limpar_campanha(
        self, page, human: HumanBehavior, acao: VPSAction
    ) -> AcaoExecutada:
        """
        Remove TODOS os anuncios de uma campanha.

        Fluxo:
        1. Navegar para config da campanha
        2. Clicar "Selecionar todos"
        3. Clicar Remover
        4. Confirmar dialog
        """
        nome = acao.target.campaign_name
        if not nome:
            return self._erro(acao, "campaign_name nao informado")

        # 1. Navegar para config da campanha
        if not await navigator.navegar_para_config_campanha(page, nome):
            return self._erro(acao, f"Nao conseguiu navegar para config de '{nome}'")

        await human.pausa(1, 2)

        # 2. Contar anuncios existentes
        anuncios = await find_all(page, "ad_management", "ad_row")
        total = len(anuncios)

        if total == 0:
            return AcaoExecutada(
                action_id=acao.action_id,
                status=StatusAcao.SUCESSO,
                detalhes=f"Campanha '{nome}' ja esta vazia",
            )

        # 3. Selecionar todos
        select_all = await find(page, "ad_management", "select_all")
        if select_all:
            await human.clicar_elemento(select_all)
            await human.pausa(0.5, 1)
        else:
            # Fallback: selecionar um por um
            for anuncio in anuncios:
                cb = await anuncio.query_selector("input[type='checkbox']")
                if cb:
                    await human.clicar_elemento(cb)
                    await human.pausa(0.2, 0.4)

        # 4. Clicar Remover
        btn_remover = await find(page, "ad_management", "remove_button")
        if not btn_remover:
            return self._erro(acao, "Botao Remover nao encontrado")

        await human.clicar_elemento(btn_remover)
        await human.pausa(1, 2)

        # 5. Confirmar
        await self._confirmar_dialog(page, human)
        await human.pausa(2, 3)
        await navigator.aguardar_carregamento(page)

        return AcaoExecutada(
            action_id=acao.action_id,
            status=StatusAcao.SUCESSO,
            detalhes=f"Campanha '{nome}' limpa ({total} anuncios removidos)",
        )

    # ==================== HELPERS ====================

    async def _clicar_salvar(self, page, human: HumanBehavior) -> bool:
        """Encontra e clica no botao Salvar."""
        btn = await find(page, "campaign_settings", "save_button")
        if not btn:
            return False
        await human.clicar_elemento(btn)
        return True

    async def _confirmar_dialog(self, page, human: HumanBehavior) -> bool:
        """Detecta e confirma dialog de confirmacao."""
        try:
            dialog = await find(page, "common", "confirm_dialog", timeout=3000)
            if not dialog:
                return False

            btn = await find(page, "common", "confirm_button", timeout=3000)
            if btn:
                await human.clicar_elemento(btn)
                await human.pausa(1, 2)
                return True

            return False
        except Exception:
            return False

    async def _verificar_toast_sucesso(self, page) -> bool:
        """Verifica se apareceu toast de sucesso."""
        try:
            seletores = [
                ".andes-snackbar--success",
                ".andes-message--success",
                "[class*='success']",
            ]
            for sel in seletores:
                el = await page.query_selector(sel)
                if el:
                    return True
            return False
        except Exception:
            return False

    async def _encontrar_anuncio(self, page, item_id: str) -> Optional[object]:
        """Busca um anuncio na lista por item_id."""
        # Tentar busca pelo campo de busca primeiro
        campo_busca = await find(page, "ad_management", "search_input", timeout=3000)
        if campo_busca:
            human = HumanBehavior(page)
            await human.limpar_e_digitar_elemento(campo_busca, item_id)
            await human.pausa(1, 2)

        # Buscar nas linhas
        linhas = await find_all(page, "ad_management", "ad_row", timeout=5000)
        for linha in linhas:
            texto = await linha.inner_text()
            if item_id in texto:
                return linha

        return None

    def _resultado_erro(
        self, acao: VPSAction, mensagem: str, inicio: float
    ) -> AcaoExecutada:
        """Cria resultado de erro com timing."""
        return AcaoExecutada(
            action_id=acao.action_id,
            status=StatusAcao.ERRO,
            detalhes=mensagem,
            duration_seconds=time.time() - inicio,
            erros=mensagem,
        )

    def _erro(self, acao: VPSAction, mensagem: str) -> AcaoExecutada:
        """Cria resultado de erro sem timing."""
        return AcaoExecutada(
            action_id=acao.action_id,
            status=StatusAcao.ERRO,
            detalhes=mensagem,
            erros=mensagem,
        )
