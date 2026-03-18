"""
Cliente HTTP para comunicacao com o VPS Ecomfluxo.

Endpoints reais da VPS (modulo publicidade):
- GET  /api/publicidade/worker/queue?account_id=X  → buscar trabalho pendente
- POST /api/publicidade/worker/results              → reportar resultados
Auth: Bearer {WORKER_API_KEY}

Formato de trabalho retornado pela VPS:
{
    "work": {
        "work_id": "uuid",
        "account_id": 673355109,
        "work_type": "rules_cycle" | "session_renew",
        "payload": [
            {"campaign_id": "123", "account_id": 673355109, "action_type": "pause"},
            {"campaign_id": "456", "account_id": 673355109, "action_type": "budget_change", "new_value": 100.0},
            {"campaign_id": "789", "account_id": 673355109, "action_type": "roas_change", "new_value": 15.0}
        ]
    }
}
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional

import httpx

from app.models import VPSAction, AcaoExecutada, TipoAcao, StatusAcao

logger = logging.getLogger(__name__)

# Mapeamento action_type VPS → TipoAcao local
_ACTION_MAP = {
    "pause": TipoAcao.PAUSAR_CAMPANHA,
    "activate": TipoAcao.ATIVAR_CAMPANHA,
    "budget_change": TipoAcao.EDITAR_BUDGET,
    "roas_change": TipoAcao.EDITAR_ROAS_TARGET,
    "create_campaign": TipoAcao.CRIAR_CAMPANHA,
    "remove_ad": TipoAcao.REMOVER_ANUNCIO,
    "add_ad": TipoAcao.ADICIONAR_ANUNCIO,
    "clear_campaign": TipoAcao.LIMPAR_CAMPANHA,
}


class VPSClient:
    """Cliente HTTP para API do Ecomfluxo (VPS)."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self._online = True

        # Buffer de trabalho atual (um work_id por conta por ciclo)
        self._current_work: dict[int, dict] = {}   # conta_id → {work_id, total, results}
        # Mapa action_id → {campaign_id, action_type_str}
        self._action_meta: dict[str, dict] = {}

    @property
    def online(self) -> bool:
        return self._online

    # ==================== RETRY ====================

    async def _request(
        self,
        method: str,
        path: str,
        max_retries: int = 3,
        **kwargs,
    ) -> Optional[httpx.Response]:
        """Faz request com retry e backoff exponencial."""
        url = f"{self.base_url}{path}"

        for tentativa in range(max_retries):
            try:
                response = await self.client.request(method, url, **kwargs)

                if response.status_code == 401:
                    logger.error("Erro 401: WORKER_API_KEY invalida — verifique ECOMFLUXO_API_KEY no .env")
                    return response

                if response.status_code == 429:
                    wait = min(60, 2 ** tentativa * 5)
                    logger.warning(f"Rate limited (429), aguardando {wait}s")
                    await asyncio.sleep(wait)
                    continue

                if response.status_code >= 500:
                    wait = min(60, 2 ** tentativa * 3)
                    logger.warning(f"Erro {response.status_code}, retry em {wait}s")
                    await asyncio.sleep(wait)
                    continue

                self._online = True
                return response

            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                self._online = False
                wait = min(60, 2 ** tentativa * 5)
                logger.warning(f"VPS inacessivel ({e.__class__.__name__}), retry em {wait}s")
                if tentativa < max_retries - 1:
                    await asyncio.sleep(wait)

            except Exception as e:
                logger.error(f"Erro inesperado na request: {e}")
                if tentativa < max_retries - 1:
                    await asyncio.sleep(2 ** tentativa)

        return None

    # ==================== ACOES ====================

    async def buscar_acoes_pendentes(
        self, conta_id: int, limit: int = 10
    ) -> list[VPSAction]:
        """Busca trabalho pendente para uma conta na fila da VPS."""
        response = await self._request(
            "GET",
            "/api/publicidade/worker/queue",
            params={"user_id": conta_id},
        )

        if response is None or response.status_code != 200:
            return []

        try:
            data = response.json()
            work = data.get("work")
            if not work:
                return []

            work_id = work.get("work_id", str(uuid.uuid4()))
            work_type = work.get("work_type", "rules_cycle")
            payload = work.get("payload", [])

            # session_renew: sinalizar para renovar sessao, sem acoes de browser
            if work_type == "session_renew":
                logger.info(f"[VPS] Trabalho session_renew recebido para conta {conta_id}")
                # Marcar como done imediatamente
                await self._reportar_work(work_id, "done", [])
                return []

            # rules_cycle: parsear acoes do payload
            if not isinstance(payload, list):
                logger.warning(f"[VPS] Payload invalido para work_id={work_id}")
                await self._reportar_work(work_id, "failed", [], error="Payload invalido")
                return []

            acoes = []
            for i, item in enumerate(payload[:limit]):
                action_type_str = item.get("action_type", "")
                tipo = _ACTION_MAP.get(action_type_str)
                if not tipo:
                    logger.warning(f"[VPS] action_type desconhecido: {action_type_str}")
                    continue

                aid = f"{work_id}__{i}"
                acao = VPSAction(
                    action_id=aid,
                    action_type=tipo,
                    priority="MEDIA",
                    target={
                        "campaign_id": item.get("campaign_id", ""),
                        "campaign_name": item.get("campaign_name", ""),
                        "item_id": item.get("item_id", ""),
                    },
                    params={
                        "novo_budget": item.get("new_value") if action_type_str == "budget_change" else None,
                        "novo_roas": item.get("new_value") if action_type_str == "roas_change" else None,
                        "item_ids": item.get("item_ids", []),
                    },
                    regra_origem=item.get("rule", "VPS"),
                    tentativas=0,
                    max_tentativas=3,
                )
                # Guardar meta para usar no report
                self._action_meta[aid] = {
                    "campaign_id": item.get("campaign_id", ""),
                    "action_type": action_type_str,
                }
                acoes.append(acao)

            # Registrar work atual para batch-report depois
            self._current_work[conta_id] = {
                "work_id": work_id,
                "total": len(acoes),
                "results": [],
            }

            logger.info(f"[VPS] {len(acoes)} acoes recebidas para conta {conta_id} (work_id={work_id})")
            return acoes

        except Exception as e:
            logger.error(f"Erro ao parsear trabalho da VPS: {e}")
            return []

    async def reportar_acao(self, resultado: AcaoExecutada) -> bool:
        """Acumula resultado de uma acao e reporta batch ao VPS quando completo."""
        # Extrair work_id do action_id (formato: work_id__index)
        action_id = resultado.action_id or ""
        if "__" in action_id:
            work_id = action_id.rsplit("__", 1)[0]
        else:
            work_id = action_id

        # Encontrar conta_id pelo work_id
        conta_id = None
        for cid, work in self._current_work.items():
            if work["work_id"] == work_id:
                conta_id = cid
                break

        if conta_id is None:
            logger.warning(f"[VPS] work_id={work_id} nao encontrado no buffer")
            return False

        work = self._current_work[conta_id]
        meta = self._action_meta.pop(action_id, {})
        work["results"].append({
            "campaign_id": meta.get("campaign_id", ""),
            "action_type": meta.get("action_type", ""),
            "success": resultado.status == StatusAcao.SUCESSO,
            "error": resultado.erros if isinstance(resultado.erros, str) else None,
            "duration_ms": int((resultado.duration_seconds or 0) * 1000),
        })

        # Quando todas as acoes do work estiverem prontas, reportar
        if len(work["results"]) >= work["total"]:
            status = "done" if all(r["success"] for r in work["results"]) else "failed"
            ok = await self._reportar_work(work_id, status, work["results"])
            del self._current_work[conta_id]
            return ok

        return True

    async def _reportar_work(
        self,
        work_id: str,
        status: str,
        actions_results: list,
        error: str = None,
    ) -> bool:
        """Envia resultados de um work_id completo para a VPS."""
        response = await self._request(
            "POST",
            "/api/publicidade/worker/results",
            json={
                "work_id": work_id,
                "status": status,
                "actions_results": actions_results,
                "error_msg": error,
            },
        )

        if response is None:
            return False

        ok = response.status_code in (200, 201, 204)
        if ok:
            logger.info(f"[VPS] Resultado reportado: work_id={work_id} status={status}")
        else:
            logger.warning(f"[VPS] Falha ao reportar resultado: {response.status_code}")
        return ok

    async def abandonar_work_atual(self, conta_id: int) -> bool:
        """Reporta trabalho em andamento como failed (chamado em caso de erro de processamento)."""
        work = self._current_work.get(conta_id)
        if not work:
            return True
        work_id = work["work_id"]
        logger.warning(f"[VPS] Abandonando work_id={work_id} para conta {conta_id} (erro de processamento)")
        ok = await self._reportar_work(
            work_id, "failed", work.get("results", []),
            error="Abandonado pelo agente (excecao durante processamento)"
        )
        self._current_work.pop(conta_id, None)
        return ok

    # ==================== HEARTBEAT (no-op — VPS nao tem endpoint) ====================

    async def enviar_heartbeat(self, heartbeat) -> Optional[dict]:
        """VPS nao tem endpoint de heartbeat — operacao ignorada."""
        return None

    # ==================== ALERTAS (no-op — VPS nao tem endpoint) ====================

    async def enviar_alerta(self, alerta) -> bool:
        """VPS nao tem endpoint de alertas — operacao ignorada."""
        return False

    # ==================== CONFIG / CONTAS (no-op) ====================

    async def buscar_config(self) -> Optional[dict]:
        return None

    async def buscar_contas(self) -> Optional[list]:
        return None

    # ==================== LIFECYCLE ====================

    async def fechar(self):
        """Fecha cliente HTTP."""
        await self.client.aclose()
