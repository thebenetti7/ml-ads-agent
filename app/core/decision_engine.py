"""
Decision Engine — Motor de Decisao com 13 Regras (R01-R13)

Baseado em: PUBLICIDADE-MODULO/skills/ml-ads-orchestrator/scripts/decision_engine.py
Mudancas:
- Imports ajustados para nova estrutura
- Removido __main__ test runner (testes em tests/)
- Usa Config unificado para margens por conta
- Action dataclass mantida como esta
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Action:
    """Acao recomendada pelo motor de decisao."""
    rule_id: str          # R01, R02, ..., R13
    action_type: str      # pausar, remover, aumentar_budget, criar_campanha, etc
    campaign_id: str      # Identificador da campanha
    campaign_name: str    # Nome da campanha
    params: dict          # Parametros da acao
    priority: int         # 1=critica, 2=otimizacao, 3=limpeza, 4=criacao
    description: str      # Descricao legivel
    cooldown_days: int = 7


class DecisionEngine:
    """Motor de decisao com 13 regras para automacao de ads."""

    def __init__(self, config, state_manager, dry_run: bool = False):
        self.config = config
        self.state_manager = state_manager
        self.dry_run = dry_run

        self.rules = {
            "R01": self.R01_pausar_roas_negativo,
            "R02": self.R02_remover_anuncio_zero_vendas,
            "R03": self.R03_pausar_excesso_budget,
            "R04": self.R04_verificar_anuncios_hold,
            "R05": self.R05_aumentar_budget_impressoes,
            "R06": self.R06_aumentar_budget_roas_alto,
            "R07": self.R07_aumentar_roas_target,
            "R08": self.R08_mudar_estrategia_cpc,
            "R09": self.R09_remover_campanha_pausada,
            "R10": self.R10_mover_anuncio_baixa_impressao,
            "R11": self.R11_consolidar_campanha,
            "R12": self.R12_criar_campanha_recomendados,
            "R13": self.R13_adicionar_produtos_curva_a,
        }

    def evaluate(
        self,
        campanhas: list,
        anuncios: list,
        estado: dict,
        conta_id: int,
        only_critical: bool = False,
    ) -> list[Action]:
        """
        Avalia regras e retorna acoes ordenadas por prioridade.

        Args:
            campanhas: dados de campanhas (API response)
            anuncios: dados de anuncios (API response)
            estado: estado persistente da conta
            conta_id: ID da conta (para buscar margem)
            only_critical: se True, executa apenas R01-R04 (modo offline)
        """
        logger.info(f"Avaliando {len(campanhas)} campanhas, {len(anuncios)} anuncios")

        # Buscar config da conta
        conta_info = self.config.get_conta_info(conta_id)

        todas_acoes = []

        rules_to_run = self.rules
        if only_critical:
            rules_to_run = {k: v for k, v in self.rules.items() if k in ("R01", "R02", "R03", "R04")}

        for rule_id, rule_func in rules_to_run.items():
            try:
                acoes_regra = rule_func(campanhas, anuncios, estado, conta_info)
                todas_acoes.extend(acoes_regra)
                if acoes_regra:
                    logger.debug(f"Regra {rule_id}: {len(acoes_regra)} acao(es)")
            except Exception as e:
                logger.error(f"Erro ao executar regra {rule_id}: {e}")

        todas_acoes.sort(key=lambda a: a.priority)

        logger.info(f"Total de acoes recomendadas: {len(todas_acoes)}")
        return todas_acoes

    # ==================== REGRAS CRITICAS (R01-R04) ====================

    def R01_pausar_roas_negativo(self, campanhas, anuncios, estado, conta_info) -> list:
        """R01: ROAS < 1x com 150+ cliques -> PAUSAR."""
        acoes = []
        for camp in campanhas:
            camp_id = str(camp.get("id", ""))
            camp_name = camp.get("name", camp_id)
            metrics = camp.get("metrics", {})
            roas = float(metrics.get("roas", 0) or 0)
            clicks = int(metrics.get("clicks", 0) or 0)
            status = camp.get("status")

            if not self.state_manager.cooldown_ok("R01", camp_id, 7):
                continue

            if roas < 1.0 and clicks >= 150 and status == "active":
                acoes.append(Action(
                    rule_id="R01", action_type="pausar",
                    campaign_id=camp_id, campaign_name=camp_name,
                    params={"novo_status": "paused"},
                    priority=1,
                    description=f"ROAS {roas:.2f}x < 1.0 com {clicks} cliques",
                    cooldown_days=7,
                ))
        return acoes

    def R02_remover_anuncio_zero_vendas(self, campanhas, anuncios, estado, conta_info) -> list:
        """R02: 200+ cliques, 0 vendas em 14 dias -> REMOVER."""
        acoes = []
        for ad in anuncios:
            ad_id = str(ad.get("item_id", ""))
            camp_id = str(ad.get("campaign_id", ""))
            camp_name = self._camp_name(campanhas, camp_id)
            clicks = int(ad.get("metrics", {}).get("clicks", 0) or 0)
            vendas = int(ad.get("metrics", {}).get("direct_items_quantity", 0) or 0)

            if not self.state_manager.cooldown_ok("R02", ad_id, 14):
                continue

            if clicks >= 200 and vendas == 0:
                acoes.append(Action(
                    rule_id="R02", action_type="remover",
                    campaign_id=camp_id, campaign_name=camp_name,
                    params={"item_id": ad_id},
                    priority=1,
                    description=f"Anuncio {ad_id}: {clicks} cliques, 0 vendas",
                    cooldown_days=14,
                ))
        return acoes

    def R03_pausar_excesso_budget(self, campanhas, anuncios, estado, conta_info) -> list:
        """R03: Budget total > R$500/dia -> PAUSAR menor ROAS."""
        acoes = []
        budget_total = sum(
            float(c.get("metrics", {}).get("cost", 0) or 0) for c in campanhas
        )
        limite = 500.0

        if budget_total > limite:
            campanhas_ord = sorted(
                campanhas,
                key=lambda c: float(c.get("metrics", {}).get("roas", 999) or 999),
            )
            for camp in campanhas_ord:
                if camp.get("status") == "paused":
                    continue
                camp_id = str(camp.get("id", ""))
                camp_name = camp.get("name", camp_id)
                camp_cost = float(camp.get("metrics", {}).get("cost", 0) or 0)

                acoes.append(Action(
                    rule_id="R03", action_type="pausar",
                    campaign_id=camp_id, campaign_name=camp_name,
                    params={"novo_status": "paused"},
                    priority=1,
                    description=f"Excesso budget: R${budget_total:.2f} > R${limite:.2f}",
                    cooldown_days=0,
                ))
                budget_total -= camp_cost
                if budget_total <= limite:
                    break
        return acoes

    def R04_verificar_anuncios_hold(self, campanhas, anuncios, estado, conta_info) -> list:
        """R04: Anuncio em status 'hold' por 3+ dias -> VERIFICAR."""
        acoes = []
        for ad in anuncios:
            ad_id = str(ad.get("item_id", ""))
            status = ad.get("status")
            camp_id = str(ad.get("campaign_id", ""))
            camp_name = self._camp_name(campanhas, camp_id)

            if status != "hold":
                continue
            if not self.state_manager.cooldown_ok("R04", ad_id, 3):
                continue

            acoes.append(Action(
                rule_id="R04", action_type="verificar_estoque",
                campaign_id=camp_id, campaign_name=camp_name,
                params={"item_id": ad_id, "acao_se_sem_estoque": "pausar"},
                priority=1,
                description=f"Anuncio {ad_id} em 'hold' 3+ dias",
                cooldown_days=3,
            ))
        return acoes

    # ==================== REGRAS DE OTIMIZACAO (R05-R08) ====================

    def R05_aumentar_budget_impressoes(self, campanhas, anuncios, estado, conta_info) -> list:
        """R05: lost_impression > 30% e ROAS > target*1.3 -> BUDGET +15%."""
        acoes = []
        for camp in campanhas:
            camp_id = str(camp.get("id", ""))
            camp_name = camp.get("name", camp_id)
            metrics = camp.get("metrics", {})
            lost_imp = float(metrics.get("lost_impression_share_by_budget", 0) or 0)
            roas = float(metrics.get("roas", 0) or 0)
            roas_target = float(camp.get("roas_target", 2.0) or 2.0)
            budget = float(camp.get("budget", 0) or 0)

            if camp.get("status") != "active" or budget <= 0:
                continue
            if not self.state_manager.cooldown_ok("R05", camp_id, 7):
                continue

            if lost_imp > 30 and roas > roas_target * 1.3:
                novo = budget * 1.15
                acoes.append(Action(
                    rule_id="R05", action_type="aumentar_budget",
                    campaign_id=camp_id, campaign_name=camp_name,
                    params={"novo_budget": novo, "budget_atual": budget, "percentual": 15},
                    priority=2,
                    description=f"Lost impression {lost_imp:.1f}% > 30%, ROAS {roas:.2f}x > target*1.3. Budget -> R${novo:.2f}",
                    cooldown_days=7,
                ))
        return acoes

    def R06_aumentar_budget_roas_alto(self, campanhas, anuncios, estado, conta_info) -> list:
        """R06: ROAS > target*2 e impression_share < 40% -> BUDGET +20%."""
        acoes = []
        for camp in campanhas:
            camp_id = str(camp.get("id", ""))
            camp_name = camp.get("name", camp_id)
            metrics = camp.get("metrics", {})
            roas = float(metrics.get("roas", 0) or 0)
            roas_target = float(camp.get("roas_target", 2.0) or 2.0)
            imp_share = float(metrics.get("impression_share", 0) or 0)
            budget = float(camp.get("budget", 0) or 0)

            if camp.get("status") != "active" or budget <= 0:
                continue
            if not self.state_manager.cooldown_ok("R06", camp_id, 7):
                continue

            if roas > roas_target * 2 and imp_share < 40:
                novo = budget * 1.20
                acoes.append(Action(
                    rule_id="R06", action_type="aumentar_budget",
                    campaign_id=camp_id, campaign_name=camp_name,
                    params={"novo_budget": novo, "budget_atual": budget, "percentual": 20},
                    priority=2,
                    description=f"ROAS {roas:.2f}x > target*2, impression_share {imp_share:.1f}% < 40%. Budget -> R${novo:.2f}",
                    cooldown_days=7,
                ))
        return acoes

    def R07_aumentar_roas_target(self, campanhas, anuncios, estado, conta_info) -> list:
        """R07: ROAS entre break-even e target -> ROAS target +10%."""
        acoes = []
        roas_breakeven = conta_info.roas_breakeven

        for camp in campanhas:
            camp_id = str(camp.get("id", ""))
            camp_name = camp.get("name", camp_id)
            roas = float(camp.get("metrics", {}).get("roas", 0) or 0)
            roas_target = float(camp.get("roas_target", 2.0) or 2.0)

            if camp.get("status") != "active":
                continue
            if not self.state_manager.cooldown_ok("R07", camp_id, 14):
                continue

            if roas_breakeven <= roas <= roas_target:
                novo = roas_target * 1.10
                acoes.append(Action(
                    rule_id="R07", action_type="aumentar_roas_target",
                    campaign_id=camp_id, campaign_name=camp_name,
                    params={"novo_roas_target": novo, "roas_atual": roas_target, "percentual": 10},
                    priority=2,
                    description=f"ROAS {roas:.2f}x entre break-even {roas_breakeven:.2f}x e target {roas_target:.2f}x. Novo target: {novo:.2f}x",
                    cooldown_days=14,
                ))
        return acoes

    def R08_mudar_estrategia_cpc(self, campanhas, anuncios, estado, conta_info) -> list:
        """R08: CPC alto + CVR baixa -> estrategia PROFITABILITY."""
        acoes = []
        for camp in campanhas:
            camp_id = str(camp.get("id", ""))
            camp_name = camp.get("name", camp_id)
            metrics = camp.get("metrics", {})
            cpc = float(metrics.get("cpc", 0) or 0)
            cvr = float(metrics.get("cvr", 0) or 0)
            strategy = camp.get("strategy", "VISIBILITY")

            if camp.get("status") != "active" or strategy == "PROFITABILITY":
                continue
            if not self.state_manager.cooldown_ok("R08", camp_id, 7):
                continue

            if cpc > 2.0 and cvr < 3.0:
                acoes.append(Action(
                    rule_id="R08", action_type="mudar_estrategia",
                    campaign_id=camp_id, campaign_name=camp_name,
                    params={"nova_estrategia": "PROFITABILITY"},
                    priority=2,
                    description=f"CPC {cpc:.2f} alto, CVR {cvr:.2f}% baixa. Mudar para PROFITABILITY",
                    cooldown_days=7,
                ))
        return acoes

    # ==================== REGRAS DE LIMPEZA (R09-R11) ====================

    def R09_remover_campanha_pausada(self, campanhas, anuncios, estado, conta_info) -> list:
        """R09: Campanha pausada 30+ dias -> REMOVER/ARQUIVAR."""
        acoes = []
        for camp in campanhas:
            camp_id = str(camp.get("id", ""))
            camp_name = camp.get("name", camp_id)
            status = camp.get("status")
            last_updated = camp.get("last_updated", datetime.now().isoformat())

            if status != "paused":
                continue

            if isinstance(last_updated, str):
                try:
                    last_updated = datetime.fromisoformat(last_updated)
                except Exception:
                    last_updated = datetime.now()

            dias_pausada = (datetime.now() - last_updated).days

            if not self.state_manager.cooldown_ok("R09", camp_id, 30):
                continue

            if dias_pausada >= 30:
                acoes.append(Action(
                    rule_id="R09", action_type="remover_campanha",
                    campaign_id=camp_id, campaign_name=camp_name,
                    params={},
                    priority=3,
                    description=f"Campanha pausada {dias_pausada} dias. Arquivar.",
                    cooldown_days=30,
                ))
        return acoes

    def R10_mover_anuncio_baixa_impressao(self, campanhas, anuncios, estado, conta_info) -> list:
        """R10: < 50 impressoes em 30 dias -> mover para VISIBILITY."""
        acoes = []
        for ad in anuncios:
            ad_id = str(ad.get("item_id", ""))
            camp_id = str(ad.get("campaign_id", ""))
            camp_name = self._camp_name(campanhas, camp_id)
            impressoes = int(ad.get("metrics", {}).get("prints", 0) or 0)

            if not self.state_manager.cooldown_ok("R10", ad_id, 30):
                continue

            if impressoes < 50:
                acoes.append(Action(
                    rule_id="R10", action_type="mover_anuncio",
                    campaign_id=camp_id, campaign_name=camp_name,
                    params={"item_id": ad_id, "destino": "VISIBILITY"},
                    priority=3,
                    description=f"Anuncio {ad_id}: {impressoes} impressoes em 30 dias",
                    cooldown_days=30,
                ))
        return acoes

    def R11_consolidar_campanha(self, campanhas, anuncios, estado, conta_info) -> list:
        """R11: Campanha com <= 1 anuncio ativo -> CONSOLIDAR."""
        acoes = []
        for camp in campanhas:
            camp_id = str(camp.get("id", ""))
            camp_name = camp.get("name", camp_id)
            anuncios_camp = [a for a in anuncios if str(a.get("campaign_id", "")) == camp_id]
            ativos = [a for a in anuncios_camp if a.get("status") == "active"]

            if not self.state_manager.cooldown_ok("R11", camp_id, 30):
                continue

            if len(anuncios_camp) > 1 and len(ativos) <= 1:
                acoes.append(Action(
                    rule_id="R11", action_type="consolidar_campanha",
                    campaign_id=camp_id, campaign_name=camp_name,
                    params={"anuncios_ativos": len(ativos), "total": len(anuncios_camp)},
                    priority=3,
                    description=f"Campanha com {len(ativos)}/{len(anuncios_camp)} ativos. Consolidar.",
                    cooldown_days=30,
                ))
        return acoes

    # ==================== REGRAS DE CRIACAO (R12-R13) ====================

    def R12_criar_campanha_recomendados(self, campanhas, anuncios, estado, conta_info) -> list:
        """R12: 5+ anuncios idle recomendados -> CRIAR campanha."""
        acoes = []
        recomendados = [
            a for a in anuncios
            if a.get("recommended") and a.get("status") == "idle"
        ]

        if not self.state_manager.cooldown_ok("R12", "novos-recomendados", 7):
            return acoes

        if len(recomendados) >= 5:
            acoes.append(Action(
                rule_id="R12", action_type="criar_campanha",
                campaign_id="", campaign_name="Novos Recomendados",
                params={
                    "name": "Novos Recomendados",
                    "strategy": "VISIBILITY",
                    "budget": 50.0,
                    "anuncios": [a.get("item_id") for a in recomendados[:20]],
                },
                priority=4,
                description=f"Criar campanha para {len(recomendados)} anuncios recomendados",
                cooldown_days=7,
            ))
        return acoes

    def R13_adicionar_produtos_curva_a(self, campanhas, anuncios, estado, conta_info) -> list:
        """R13: Produtos Curva A sem ads -> ADICIONAR a 'Estrelas'."""
        acoes = []
        ads_com_campanha = set(str(a.get("item_id", "")) for a in anuncios if a.get("campaign_id"))
        curva_a = estado.get("produtos_curva_a", [])
        sem_ads = [p for p in curva_a if p not in ads_com_campanha]

        if not self.state_manager.cooldown_ok("R13", "curva-a", 7):
            return acoes

        if sem_ads:
            camp_estrelas = next(
                (c for c in campanhas if "Estrelas" in c.get("name", "")),
                None,
            )
            acoes.append(Action(
                rule_id="R13", action_type="adicionar_anuncios",
                campaign_id=camp_estrelas.get("id", "") if camp_estrelas else "",
                campaign_name=camp_estrelas.get("name", "Estrelas") if camp_estrelas else "Estrelas",
                params={
                    "anuncios": sem_ads[:10],
                    "criar_campanha_se_nao_existe": camp_estrelas is None,
                },
                priority=4,
                description=f"Adicionar {len(sem_ads)} produtos Curva A a 'Estrelas'",
                cooldown_days=7,
            ))
        return acoes

    # ==================== HELPERS ====================

    @staticmethod
    def _camp_name(campanhas: list, camp_id: str) -> str:
        """Busca nome de campanha por ID."""
        return next(
            (c.get("name", camp_id) for c in campanhas if str(c.get("id", "")) == camp_id),
            camp_id,
        )
