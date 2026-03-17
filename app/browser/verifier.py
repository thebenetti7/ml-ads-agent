"""
Verifier — Verificacao pos-acao via API do Mercado Livre.

Apos cada acao de browser, verifica via API se a alteracao foi aplicada.
Usa os tokens de acesso por conta (ML_TOKEN_{conta_id}).

Endpoints usados:
- GET /advertising/MLB/product_ads/campaigns/{campaign_id} — status, budget, roas
- GET /advertising/MLB/advertisers/{adv}/product_ads/campaigns/search — busca por nome
- GET /advertising/MLB/product_ads/ads/{item_id} — status de anuncio
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ML_API_BASE = "https://api.mercadolibre.com"
SITE = "MLB"
TIMEOUT = 15


class ActionVerifier:
    """Verifica resultados de acoes via API do Mercado Livre."""

    def __init__(self, conta_id: int):
        self.conta_id = conta_id
        self.token = os.environ.get(f"ML_TOKEN_{conta_id}", "")
        self.advertiser_id = str(conta_id)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "api-version": "2",
        }

    async def _get(self, path: str) -> Optional[dict]:
        """GET request na API do ML."""
        if not self.token:
            logger.warning(f"Token nao configurado para conta {self.conta_id}")
            return None

        url = f"{ML_API_BASE}{path}"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(url, headers=self._headers())

                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 401:
                    logger.warning(f"Token expirado para conta {self.conta_id}")
                elif resp.status_code == 404:
                    logger.warning(f"Recurso nao encontrado: {path}")
                else:
                    logger.warning(
                        f"API retornou {resp.status_code} para {path}: "
                        f"{resp.text[:200]}"
                    )

        except Exception as e:
            logger.error(f"Erro na API ML: {e}")

        return None

    # ==================== VERIFICACOES ====================

    async def verificar_pausa(self, campaign_id: str) -> Optional[bool]:
        """
        Verifica se campanha esta pausada.

        Returns:
            True se pausada, False se ativa, None se erro
        """
        data = await self._get(
            f"/advertising/{SITE}/product_ads/campaigns/{campaign_id}"
        )
        if not data:
            return None

        status = data.get("status", "")
        logger.info(f"Status campanha {campaign_id}: {status}")
        return status == "paused"

    async def verificar_ativacao(self, campaign_id: str) -> Optional[bool]:
        """
        Verifica se campanha esta ativa.

        Returns:
            True se ativa, False se pausada, None se erro
        """
        data = await self._get(
            f"/advertising/{SITE}/product_ads/campaigns/{campaign_id}"
        )
        if not data:
            return None

        status = data.get("status", "")
        logger.info(f"Status campanha {campaign_id}: {status}")
        return status == "active"

    async def verificar_budget(
        self, campaign_id: str, expected: float, tolerance: float = 0.01
    ) -> Optional[bool]:
        """
        Verifica se budget da campanha bate com o esperado.

        Args:
            campaign_id: ID da campanha
            expected: budget esperado
            tolerance: tolerancia para comparacao float

        Returns:
            True se bate, False se diferente, None se erro
        """
        data = await self._get(
            f"/advertising/{SITE}/product_ads/campaigns/{campaign_id}"
        )
        if not data:
            return None

        actual = data.get("budget")
        if actual is None:
            return None

        match = abs(float(actual) - expected) <= tolerance
        logger.info(
            f"Budget campanha {campaign_id}: "
            f"esperado={expected}, atual={actual}, match={match}"
        )
        return match

    async def verificar_roas(
        self, campaign_id: str, expected: float, tolerance: float = 0.1
    ) -> Optional[bool]:
        """
        Verifica se ROAS target da campanha bate com o esperado.

        Returns:
            True se bate, False se diferente, None se erro
        """
        data = await self._get(
            f"/advertising/{SITE}/product_ads/campaigns/{campaign_id}"
        )
        if not data:
            return None

        actual = data.get("roas_target")
        if actual is None:
            return None

        match = abs(float(actual) - expected) <= tolerance
        logger.info(
            f"ROAS target campanha {campaign_id}: "
            f"esperado={expected}, atual={actual}, match={match}"
        )
        return match

    async def verificar_campanha_criada(
        self, campaign_name: str
    ) -> Optional[dict]:
        """
        Busca campanha por nome para verificar se foi criada.

        Returns:
            Dados da campanha se encontrada, None se nao
        """
        data = await self._get(
            f"/advertising/{SITE}/advertisers/{self.advertiser_id}"
            f"/product_ads/campaigns/search?limit=50&offset=0"
        )
        if not data:
            return None

        results = data.get("results", data) if isinstance(data, dict) else data
        if isinstance(results, list):
            for camp in results:
                name = camp.get("name", "")
                if campaign_name.lower() in name.lower():
                    logger.info(f"Campanha '{campaign_name}' encontrada: id={camp.get('id')}")
                    return camp

        logger.warning(f"Campanha '{campaign_name}' nao encontrada via API")
        return None

    async def verificar_anuncio_na_campanha(
        self, campaign_id: str, item_id: str
    ) -> Optional[bool]:
        """
        Verifica se um item esta na campanha.

        Returns:
            True se presente, False se ausente, None se erro
        """
        data = await self._get(
            f"/advertising/{SITE}/advertisers/{self.advertiser_id}"
            f"/product_ads/ads/search?filters[campaign_id]={campaign_id}"
            f"&filters[item_id]={item_id}&limit=1"
        )
        if not data:
            return None

        results = data.get("results", data) if isinstance(data, dict) else data
        if isinstance(results, list):
            found = len(results) > 0
            logger.info(
                f"Item {item_id} na campanha {campaign_id}: "
                f"{'encontrado' if found else 'nao encontrado'}"
            )
            return found

        return None

    async def verificar_anuncio_removido(
        self, campaign_id: str, item_id: str
    ) -> Optional[bool]:
        """
        Verifica se um item foi removido da campanha.

        Returns:
            True se removido (nao esta la), False se ainda presente, None se erro
        """
        presente = await self.verificar_anuncio_na_campanha(campaign_id, item_id)
        if presente is None:
            return None
        return not presente

    async def verificar_campanha_vazia(self, campaign_id: str) -> Optional[bool]:
        """
        Verifica se campanha nao tem anuncios.

        Returns:
            True se vazia, False se tem anuncios, None se erro
        """
        data = await self._get(
            f"/advertising/{SITE}/advertisers/{self.advertiser_id}"
            f"/product_ads/ads/search?filters[campaign_id]={campaign_id}&limit=1"
        )
        if not data:
            return None

        results = data.get("results", data) if isinstance(data, dict) else data
        if isinstance(results, list):
            vazia = len(results) == 0
            logger.info(
                f"Campanha {campaign_id}: "
                f"{'vazia' if vazia else f'{len(results)}+ anuncios'}"
            )
            return vazia

        return None

    # ==================== VERIFICACAO COMPLETA ====================

    async def verificar_acao(
        self,
        tipo_acao: str,
        campaign_id: Optional[str] = None,
        campaign_name: Optional[str] = None,
        item_id: Optional[str] = None,
        expected_value: Optional[float] = None,
    ) -> dict:
        """
        Verificacao generica pos-acao.

        Args:
            tipo_acao: tipo da acao executada
            campaign_id: ID da campanha (se disponivel)
            campaign_name: nome da campanha
            item_id: ID do item (para acoes de anuncio)
            expected_value: valor esperado (budget ou ROAS)

        Returns:
            dict com {verificado: bool, detalhes: str}
        """
        if not self.token:
            return {
                "verificado": False,
                "detalhes": "Token nao configurado, verificacao via API indisponivel",
            }

        try:
            if tipo_acao == "pausar_campanha" and campaign_id:
                result = await self.verificar_pausa(campaign_id)
                if result is True:
                    return {"verificado": True, "detalhes": "Campanha confirmada como pausada"}
                elif result is False:
                    return {"verificado": False, "detalhes": "Campanha ainda aparece como ativa"}

            elif tipo_acao == "ativar_campanha" and campaign_id:
                result = await self.verificar_ativacao(campaign_id)
                if result is True:
                    return {"verificado": True, "detalhes": "Campanha confirmada como ativa"}
                elif result is False:
                    return {"verificado": False, "detalhes": "Campanha ainda aparece como pausada"}

            elif tipo_acao == "editar_budget" and campaign_id and expected_value:
                result = await self.verificar_budget(campaign_id, expected_value)
                if result is True:
                    return {"verificado": True, "detalhes": f"Budget confirmado: R${expected_value}"}
                elif result is False:
                    return {"verificado": False, "detalhes": "Budget nao corresponde ao esperado"}

            elif tipo_acao == "editar_roas_target" and campaign_id and expected_value:
                result = await self.verificar_roas(campaign_id, expected_value)
                if result is True:
                    return {"verificado": True, "detalhes": f"ROAS target confirmado: {expected_value}x"}
                elif result is False:
                    return {"verificado": False, "detalhes": "ROAS target nao corresponde ao esperado"}

            elif tipo_acao == "criar_campanha" and campaign_name:
                result = await self.verificar_campanha_criada(campaign_name)
                if result:
                    return {"verificado": True, "detalhes": f"Campanha encontrada: id={result.get('id')}"}
                else:
                    return {"verificado": False, "detalhes": "Campanha nao encontrada via API"}

            elif tipo_acao == "remover_anuncio" and campaign_id and item_id:
                result = await self.verificar_anuncio_removido(campaign_id, item_id)
                if result is True:
                    return {"verificado": True, "detalhes": f"Item {item_id} confirmado como removido"}
                elif result is False:
                    return {"verificado": False, "detalhes": f"Item {item_id} ainda presente na campanha"}

            elif tipo_acao == "adicionar_anuncio" and campaign_id and item_id:
                result = await self.verificar_anuncio_na_campanha(campaign_id, item_id)
                if result is True:
                    return {"verificado": True, "detalhes": f"Item {item_id} confirmado na campanha"}
                elif result is False:
                    return {"verificado": False, "detalhes": f"Item {item_id} nao encontrado na campanha"}

            elif tipo_acao == "limpar_campanha" and campaign_id:
                result = await self.verificar_campanha_vazia(campaign_id)
                if result is True:
                    return {"verificado": True, "detalhes": "Campanha confirmada como vazia"}
                elif result is False:
                    return {"verificado": False, "detalhes": "Campanha ainda tem anuncios"}

        except Exception as e:
            logger.error(f"Erro na verificacao pos-acao: {e}")

        return {
            "verificado": False,
            "detalhes": "Verificacao inconclusiva (dados insuficientes ou erro de API)",
        }
