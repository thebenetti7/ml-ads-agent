"""
Selectors — Seletores centralizados do painel ML Ads.

IMPORTANTE: Seletores iniciais sao ESTIMATIVAS baseadas no design system Andes do ML.
Precisam ser validados manualmente na primeira execucao real no notebook.

Cada seletor tem lista de fallbacks: primario -> alternativo -> estrutural.
A funcao find() tenta cada um em sequencia.

Para atualizar: rode com headless=False, inspecione os elementos (F12),
e atualize os seletores aqui.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ==================== SELETORES ====================
# Formato: lista de seletores (primeiro que funcionar e usado)

SELECTORS = {
    # --- Lista de campanhas ---
    "campaign_list": {
        "container": [
            "[data-testid='campaigns-list']",
            ".campaigns-list",
            "table.andes-table",
            "table",
        ],
        "row": [
            "[data-testid='campaign-row']",
            "tr.campaign-row",
            ".andes-table__row",
            "tbody tr",
        ],
        "campaign_name": [
            "[data-testid='campaign-name']",
            ".campaign-name a",
            "td:first-child a",
            "td:nth-child(2) a",
        ],
        "campaign_status_toggle": [
            "[data-testid='campaign-status-toggle']",
            ".andes-switch",
            "input[type='checkbox'][role='switch']",
            ".campaign-status .andes-switch__trigger",
        ],
        "campaign_budget": [
            "[data-testid='campaign-budget']",
            ".campaign-budget",
            "td.budget",
        ],
        "campaign_roas": [
            "[data-testid='campaign-roas']",
            ".campaign-roas",
            "td.roas",
        ],
    },

    # --- Config de campanha (pagina individual) ---
    "campaign_settings": {
        "budget_input": [
            "[data-testid='budget-input']",
            "input[name='budget']",
            ".budget-field input",
            ".andes-form-control__field[type='number']",
        ],
        "roas_input": [
            "[data-testid='roas-input']",
            "input[name='roas_target']",
            "input[name='roas']",
            ".roas-field input",
        ],
        "strategy_select": [
            "[data-testid='strategy-select']",
            "select[name='strategy']",
            ".strategy-selector",
        ],
        "save_button": [
            "[data-testid='save-button']",
            "button[type='submit']",
            ".andes-button--loud",
            "button:has-text('Salvar')",
            "button:has-text('Guardar')",
        ],
        "name_input": [
            "[data-testid='campaign-name-input']",
            "input[name='name']",
            "input[name='campaign_name']",
        ],
    },

    # --- Gerenciamento de anuncios ---
    "ad_management": {
        "ad_row": [
            "[data-testid='ad-row']",
            ".ad-item",
            "tr.ad-row",
            ".andes-table__row",
        ],
        "ad_checkbox": [
            "[data-testid='ad-checkbox']",
            "input[type='checkbox']",
            ".andes-checkbox",
        ],
        "add_button": [
            "[data-testid='add-ads-button']",
            "button:has-text('Adicionar')",
            "button:has-text('Agregar')",
            ".add-ads-btn",
        ],
        "remove_button": [
            "[data-testid='remove-ads-button']",
            "button:has-text('Remover')",
            "button:has-text('Eliminar')",
            ".remove-ads-btn",
        ],
        "select_all": [
            "[data-testid='select-all']",
            "th input[type='checkbox']",
            ".select-all-checkbox",
        ],
        "search_input": [
            "[data-testid='search-ads']",
            "input[placeholder*='Buscar']",
            "input[placeholder*='buscar']",
            ".search-input",
        ],
    },

    # --- Criacao de campanha (wizard) ---
    "create_campaign": {
        "create_button": [
            "[data-testid='create-campaign-button']",
            "button:has-text('Criar campanha')",
            "button:has-text('Crear campaña')",
            "a:has-text('Criar campanha')",
        ],
        "next_button": [
            "[data-testid='next-step']",
            "button:has-text('Seguir')",
            "button:has-text('Siguiente')",
            "button:has-text('Continuar')",
        ],
        "finish_button": [
            "[data-testid='finish-button']",
            "button:has-text('Finalizar')",
            "button:has-text('Crear')",
            "button:has-text('Confirmar')",
        ],
        "strategy_visibility": [
            "[data-testid='strategy-visibility']",
            "label:has-text('Visibilidade')",
            "label:has-text('Visibilidad')",
            "[value='VISIBILITY']",
        ],
        "strategy_profitability": [
            "[data-testid='strategy-profitability']",
            "label:has-text('Rentabilidade')",
            "label:has-text('Rentabilidad')",
            "[value='PROFITABILITY']",
        ],
        "strategy_increase": [
            "[data-testid='strategy-increase']",
            "label:has-text('Aumento')",
            "label:has-text('Incremento')",
            "[value='INCREASE']",
        ],
        "product_search": [
            "[data-testid='product-search']",
            "input[placeholder*='produto']",
            "input[placeholder*='producto']",
            ".product-search input",
        ],
        "product_checkbox": [
            "[data-testid='product-checkbox']",
            ".product-item input[type='checkbox']",
            ".andes-checkbox",
        ],
    },

    # --- Elementos comuns ---
    "common": {
        "confirm_dialog": [
            "[data-testid='confirm-dialog']",
            ".andes-modal",
            ".modal",
            "[role='dialog']",
        ],
        "confirm_button": [
            "[data-testid='confirm-button']",
            ".andes-modal button.andes-button--loud",
            "button:has-text('Confirmar')",
            "button:has-text('Sim')",
            "button:has-text('Aceptar')",
        ],
        "cancel_button": [
            "[data-testid='cancel-button']",
            ".andes-modal button.andes-button--quiet",
            "button:has-text('Cancelar')",
        ],
        "spinner": [
            ".andes-spinner",
            "[class*='loading']",
            "[class*='spinner']",
            "[data-testid='loading']",
        ],
        "toast_success": [
            ".andes-snackbar--success",
            ".andes-message--success",
            "[class*='success']",
        ],
        "toast_error": [
            ".andes-snackbar--error",
            ".andes-message--error",
            "[class*='error']",
        ],
    },
}


# ==================== FINDER ====================

async def find(page, section: str, element: str, timeout: int = 5000) -> Optional[object]:
    """
    Tenta encontrar elemento usando fallback chain.

    Args:
        page: pagina Playwright
        section: secao dos seletores (ex: "campaign_list")
        element: nome do elemento (ex: "row")
        timeout: timeout em ms para cada tentativa

    Returns:
        Elemento encontrado ou None
    """
    selectors = SELECTORS.get(section, {}).get(element, [])

    if not selectors:
        logger.error(f"Seletor nao definido: {section}.{element}")
        return None

    for selector in selectors:
        try:
            el = await page.wait_for_selector(selector, timeout=timeout)
            if el:
                logger.debug(f"Encontrado {section}.{element} via: {selector}")
                return el
        except Exception:
            continue

    logger.warning(f"Nenhum seletor funcionou para {section}.{element}")
    return None


async def find_all(page, section: str, element: str, timeout: int = 5000) -> list:
    """
    Encontra TODOS os elementos usando fallback chain.
    Retorna lista (pode ser vazia).
    """
    selectors = SELECTORS.get(section, {}).get(element, [])

    if not selectors:
        logger.error(f"Seletor nao definido: {section}.{element}")
        return []

    for selector in selectors:
        try:
            # Esperar pelo menos 1 aparecer
            await page.wait_for_selector(selector, timeout=timeout)
            elements = await page.query_selector_all(selector)
            if elements:
                logger.debug(f"Encontrados {len(elements)} {section}.{element} via: {selector}")
                return elements
        except Exception:
            continue

    logger.warning(f"Nenhum elemento encontrado para {section}.{element}")
    return []


def get_selector(section: str, element: str) -> str:
    """Retorna o seletor primario (primeiro da lista)."""
    selectors = SELECTORS.get(section, {}).get(element, [])
    return selectors[0] if selectors else ""
