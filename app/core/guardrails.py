"""
Guardrails — Travas de Seguranca UNIFICADAS

Merge de:
- app/core/guardrails.py (kill switch, horario, recursos, limites, budget/ROAS)
- scripts/guardrails.py (validacao por tipo, pos-acao, emergencia, saude geral)
"""

import logging
import psutil
from datetime import datetime
from pathlib import Path
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class GuardrailsManager:
    """Sistema de travas de seguranca multicamadas."""

    def __init__(self, config, state_manager):
        self.config = config
        self.state_manager = state_manager
        self.stop_file = Path("./STOP")

    # ==================== KILL SWITCH ====================

    def kill_switch_ativo(self) -> bool:
        return self.stop_file.exists()

    def ativar_kill_switch(self, motivo: str):
        """Cria arquivo STOP (kill switch de emergencia)."""
        self.stop_file.write_text(f"{datetime.now().isoformat()}: {motivo}")
        logger.critical(f"KILL SWITCH ATIVADO: {motivo}")

        for conta_id in self.config.contas:
            self.state_manager.alertar(conta_id, "CRITICO", f"Kill switch: {motivo}")

    def desativar_kill_switch(self):
        if self.stop_file.exists():
            self.stop_file.unlink()
            logger.info("Kill switch desativado")

    # ==================== HORARIO ====================

    def dentro_horario_operacao(self) -> Tuple[bool, str]:
        """Verifica se esta dentro dos work blocks."""
        agora = datetime.now()
        agora_min = agora.hour * 60 + agora.minute
        variacao = self.config.variacao_minutos

        for bloco in self.config.work_blocks:
            inicio_h, inicio_m, fim_h, fim_m = bloco
            inicio_min = inicio_h * 60 + inicio_m - variacao
            fim_min = fim_h * 60 + fim_m + variacao

            if inicio_min <= agora_min <= fim_min:
                return True, "Dentro do horario"

        blocos_str = ", ".join(
            f"{b[0]:02d}:{b[1]:02d}-{b[2]:02d}:{b[3]:02d}"
            for b in self.config.work_blocks
        )
        return False, f"Fora do horario (blocos: {blocos_str})"

    # ==================== RECURSOS ====================

    def recursos_suficientes(self) -> Tuple[bool, dict]:
        """Verifica RAM, CPU e disco."""
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.5)

        # No Windows, usar disco C:
        try:
            disco = psutil.disk_usage("C:\\")
        except Exception:
            disco = psutil.disk_usage("/")

        detalhes = {
            "memoria_livre_mb": mem.available / (1024 * 1024),
            "memoria_pct": mem.percent,
            "cpu_percent": cpu,
            "disco_livre_mb": disco.free / (1024 * 1024),
        }

        motivos = []
        if detalhes["memoria_livre_mb"] < self.config.ram_min_livre_mb:
            motivos.append(f"RAM baixa ({detalhes['memoria_livre_mb']:.0f}MB livre)")
        if cpu >= self.config.cpu_max_pct:
            motivos.append(f"CPU alta ({cpu:.0f}%)")
        if detalhes["disco_livre_mb"] < self.config.disco_min_livre_mb:
            motivos.append(f"Disco baixo ({detalhes['disco_livre_mb']:.0f}MB)")

        detalhes["motivos"] = motivos
        return len(motivos) == 0, detalhes

    # ==================== LIMITES ====================

    def limite_acoes_atingido(self, conta_id: int) -> Tuple[bool, str]:
        """Verifica limites diarios e horarios."""
        acoes_dia = self.state_manager.acoes_hoje_count(conta_id)
        if acoes_dia >= self.config.max_acoes_dia:
            return True, f"Limite diario: {acoes_dia}/{self.config.max_acoes_dia}"

        acoes_hora = self.state_manager.acoes_ultima_hora(conta_id)
        if acoes_hora >= self.config.max_acoes_hora:
            return True, f"Limite horario: {acoes_hora}/{self.config.max_acoes_hora}"

        return False, ""

    # ==================== VALIDACAO BUDGET/ROAS ====================

    def variacao_budget_ok(self, atual: float, novo: float) -> Tuple[bool, str]:
        if atual <= 0:
            return novo <= 500, "Budget atual invalido, limite R$500 para novos"

        variacao = abs((novo - atual) / atual) * 100
        if variacao > self.config.variacao_budget_max_pct:
            return False, f"Variacao {variacao:.1f}% > max {self.config.variacao_budget_max_pct}%"
        return True, ""

    def variacao_roas_ok(self, atual: float, novo: float) -> Tuple[bool, str]:
        if not (self.config.roas_min <= novo <= self.config.roas_max):
            return False, f"ROAS {novo}x fora do range {self.config.roas_min}-{self.config.roas_max}x"

        if atual > 0:
            variacao = abs((novo - atual) / atual) * 100
            if variacao > self.config.variacao_roas_max_pct:
                return False, f"Variacao ROAS {variacao:.1f}% > max {self.config.variacao_roas_max_pct}%"

        return True, ""

    # ==================== VALIDACAO POR TIPO ====================

    def _validar_por_tipo(self, tipo_acao: str, params: dict) -> Tuple[bool, str]:
        """Validacoes especificas por tipo de acao."""
        if tipo_acao == "criar_campanha":
            budget = params.get("budget", 0)
            if budget <= 0:
                return False, "Budget inicial nao definido"
            if budget > 500:
                return False, f"Budget inicial R${budget:.2f} > R$500 limite"

        elif tipo_acao == "editar_budget":
            novo = params.get("novo_budget", 0)
            atual = params.get("budget_atual", 0)
            if atual > 0 and novo > 0:
                return self.variacao_budget_ok(atual, novo)

        elif tipo_acao == "editar_roas_target":
            novo = params.get("novo_roas", 0)
            atual = params.get("roas_atual", 0)
            if novo > 0:
                return self.variacao_roas_ok(atual, novo)

        return True, ""

    # ==================== VERIFICACAO PRINCIPAL ====================

    def pode_executar(self, tipo_acao: str, conta_id: int, params: dict = None) -> Tuple[bool, str]:
        """
        Verifica TODAS as condicoes antes de executar uma acao.

        Returns:
            (pode_executar, motivo_se_bloqueado)
        """
        if params is None:
            params = {}

        # 1. Kill switch
        if self.kill_switch_ativo():
            return False, "Kill switch ativo"

        # 2. Horario
        ok, motivo = self.dentro_horario_operacao()
        if not ok:
            return False, motivo

        # 3. Recursos
        ok, detalhes = self.recursos_suficientes()
        if not ok:
            return False, f"Recursos: {detalhes.get('motivos', [])}"

        # 4. Limites
        atingido, motivo = self.limite_acoes_atingido(conta_id)
        if atingido:
            return False, motivo

        # 5. Validacao por tipo
        ok, motivo = self._validar_por_tipo(tipo_acao, params)
        if not ok:
            return False, motivo

        return True, ""

    # ==================== RELATORIO ====================

    def relatorio(self) -> dict:
        """Retorna status dos guardrails."""
        ok_recursos, det = self.recursos_suficientes()
        ok_horario, _ = self.dentro_horario_operacao()

        return {
            "kill_switch": self.kill_switch_ativo(),
            "dentro_horario": ok_horario,
            "recursos_ok": ok_recursos,
            "memoria_livre_mb": det.get("memoria_livre_mb", 0),
            "cpu_percent": det.get("cpu_percent", 0),
            "disco_livre_mb": det.get("disco_livre_mb", 0),
            "timestamp": datetime.now().isoformat(),
        }
