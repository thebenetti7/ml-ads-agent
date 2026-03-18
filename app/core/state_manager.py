"""
Gerenciador de Estado Persistente — UNIFICADO

Merge de:
- app/core/state_manager.py (atomic writes, per-account JSON, per-day changelog)
- scripts/state_manager.py (cooldown, snapshots, rollback, alertas, reset diario, global)
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)


class StateManager:
    """Gerencia estado persistente em JSON + changelog JSONL."""

    def __init__(self, base_dir: str = "./state"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self.global_file = self.base_dir / "global.json"
        self.snapshots_dir = self.base_dir / "snapshots"
        self.snapshots_dir.mkdir(exist_ok=True)

        logger.info(f"StateManager inicializado em {self.base_dir}")

    # ==================== PATHS ====================

    def _arquivo_conta(self, conta_id: int) -> Path:
        return self.base_dir / f"{conta_id}.json"

    def _arquivo_changelog(self, data: Optional[datetime] = None) -> Path:
        if data is None:
            data = datetime.now()
        return self.base_dir / f"changelog_{data.strftime('%Y-%m-%d')}.jsonl"

    # ==================== ATOMIC WRITE ====================

    def _salvar_json(self, filepath: Path, dados: dict):
        """Salva JSON de forma atomica (temp + rename)."""
        tmp = filepath.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dados, f, indent=2, ensure_ascii=False, default=str)
            tmp.replace(filepath)
        except Exception as e:
            logger.error(f"Erro ao salvar {filepath}: {e}")
            if tmp.exists():
                tmp.unlink()

    def _carregar_json(self, filepath: Path, default: dict = None) -> dict:
        """Carrega JSON com fallback."""
        if default is None:
            default = {}
        if not filepath.exists():
            return default
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Erro ao carregar {filepath}: {e}")
            return default

    # ==================== GLOBAL STATE ====================

    def load_global(self) -> dict:
        """Carrega estado global (cross-conta)."""
        return self._carregar_json(self.global_file, {
            "timestamp": datetime.now().isoformat(),
            "ultima_data_reset": None,
        })

    def save_global(self, dados: dict):
        """Salva estado global."""
        dados["timestamp"] = datetime.now().isoformat()
        self._salvar_json(self.global_file, dados)

    # ==================== CONTA STATE ====================

    def carregar_estado(self, conta_id: int) -> dict[str, Any]:
        """Carrega estado de uma conta."""
        return self._carregar_json(self._arquivo_conta(conta_id), self._estado_inicial(conta_id))

    def salvar_estado(self, conta_id: int, estado: dict[str, Any]):
        """Salva estado de uma conta."""
        estado["ultima_atualizacao"] = datetime.now().isoformat()
        self._salvar_json(self._arquivo_conta(conta_id), estado)

    @staticmethod
    def _estado_inicial(conta_id: int = 0) -> dict[str, Any]:
        return {
            "conta_id": conta_id,
            "acoes_hoje": 0,
            "acoes_ciclo": 0,
            "acoes": [],
            "erros": [],
            "alertas": [],
            "sessao_status": "fechada",
            "sessao_aberta_em": None,
            "ultima_acao": None,
            "criado_em": datetime.now().isoformat(),
            "ultima_atualizacao": datetime.now().isoformat(),
        }

    # ==================== CHANGELOG ====================

    def registrar_acao(
        self,
        conta_id: int,
        action_id: str = "",
        rule_id: str = "",
        action_type: str = "",
        campaign_id: str = "",
        campaign_name: str = "",
        params: dict = None,
        status: str = "sucesso",
        duration_ms: int = 0,
    ):
        """Registra acao no changelog do dia + atualiza estado da conta."""
        registro = {
            "timestamp": datetime.now().isoformat(),
            "conta_id": conta_id,
            "action_id": action_id,
            "rule_id": rule_id,
            "action_type": action_type,
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "params": params or {},
            "status": status,
            "duration_ms": duration_ms,
        }

        try:
            # Append ao changelog do dia
            arquivo = self._arquivo_changelog()
            with open(arquivo, "a", encoding="utf-8") as f:
                f.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")

            # Atualizar estado da conta
            estado = self.carregar_estado(conta_id)
            estado["acoes_hoje"] = estado.get("acoes_hoje", 0) + 1
            estado["acoes_ciclo"] = estado.get("acoes_ciclo", 0) + 1
            estado["ultima_acao"] = registro["timestamp"]

            # Manter ultimas 50 acoes no estado
            if "acoes" not in estado:
                estado["acoes"] = []
            estado["acoes"].append(registro)
            estado["acoes"] = estado["acoes"][-50:]

            self.salvar_estado(conta_id, estado)
            logger.info(f"Acao registrada: {rule_id} {action_type} ({campaign_id}) conta {conta_id}")

        except Exception as e:
            logger.error(f"Erro ao registrar acao: {e}")

    def carregar_changelog(self, data: Optional[datetime] = None) -> list[dict]:
        """Carrega changelog de um dia."""
        arquivo = self._arquivo_changelog(data)
        if not arquivo.exists():
            return []

        acoes = []
        try:
            with open(arquivo, "r", encoding="utf-8") as f:
                for linha in f:
                    if linha.strip():
                        acoes.append(json.loads(linha))
        except Exception as e:
            logger.error(f"Erro ao carregar changelog: {e}")
        return acoes

    def changelog_hoje(self, conta_id: Optional[int] = None) -> list[dict]:
        """Retorna changelog de hoje, opcionalmente filtrado por conta."""
        registros = self.carregar_changelog()
        if conta_id is not None:
            registros = [r for r in registros if r.get("conta_id") == conta_id]
        return registros

    def changelog_periodo(self, dias: int = 7, conta_id: Optional[int] = None) -> list[dict]:
        """Retorna changelog dos ultimos N dias."""
        registros = []
        for i in range(dias):
            data = datetime.now() - timedelta(days=i)
            registros.extend(self.carregar_changelog(data))

        if conta_id is not None:
            registros = [r for r in registros if r.get("conta_id") == conta_id]
        return registros

    # ==================== COOLDOWN ====================

    def cooldown_ok(self, rule_id: str, campaign_id: str, dias: int = 7) -> bool:
        """Verifica se cooldown expirou para regra+campanha. True = pode executar."""
        registros = self.changelog_periodo(dias=dias + 1)

        for reg in reversed(registros):
            if reg.get("rule_id") == rule_id and reg.get("campaign_id") == campaign_id:
                ts = datetime.fromisoformat(reg["timestamp"])
                if (datetime.now() - ts).days < dias:
                    logger.debug(f"Cooldown ativo: {rule_id}/{campaign_id}")
                    return False
                return True

        return True  # Nunca executada

    # ==================== SESSAO ====================

    def marcar_sessao_aberta(self, conta_id: int):
        estado = self.carregar_estado(conta_id)
        estado["sessao_status"] = "aberta"
        estado["sessao_aberta_em"] = datetime.now().isoformat()
        self.salvar_estado(conta_id, estado)

    def marcar_sessao_fechada(self, conta_id: int):
        estado = self.carregar_estado(conta_id)
        estado["sessao_status"] = "fechada"
        self.salvar_estado(conta_id, estado)

    # ==================== ERROS ====================

    def registrar_erro(self, conta_id: int, mensagem: str, detalhes: Optional[str] = None):
        estado = self.carregar_estado(conta_id)
        if "erros" not in estado:
            estado["erros"] = []

        estado["erros"].append({
            "timestamp": datetime.now().isoformat(),
            "mensagem": mensagem,
            "detalhes": detalhes,
        })
        estado["erros"] = estado["erros"][-100:]  # Manter ultimos 100
        self.salvar_estado(conta_id, estado)

    # ==================== ALERTAS ====================

    def alertar(self, conta_id: int, tipo: str, mensagem: str):
        estado = self.carregar_estado(conta_id)
        if "alertas" not in estado:
            estado["alertas"] = []

        estado["alertas"].append({
            "timestamp": datetime.now().isoformat(),
            "tipo": tipo,
            "mensagem": mensagem,
        })
        estado["alertas"] = estado["alertas"][-100:]
        self.salvar_estado(conta_id, estado)
        logger.warning(f"Alerta [{tipo}] conta {conta_id}: {mensagem}")

    def limpar_alertas(self, conta_id: int):
        estado = self.carregar_estado(conta_id)
        estado["alertas"] = []
        self.salvar_estado(conta_id, estado)

    # ==================== SNAPSHOTS & ROLLBACK ====================

    def snapshot_pre_ciclo(self, conta_id: int):
        """Cria snapshot do estado ANTES de executar ciclo."""
        try:
            estado = self.carregar_estado(conta_id)
            ts = datetime.now().strftime("%Y%m%dT%H%M%S")
            snapshot_file = self.snapshots_dir / f"{conta_id}_{ts}.json"

            with open(snapshot_file, "w", encoding="utf-8") as f:
                json.dump(estado, f, indent=2, ensure_ascii=False, default=str)

            logger.debug(f"Snapshot criado: {snapshot_file.name}")
        except Exception as e:
            logger.error(f"Erro ao criar snapshot: {e}")

    def rollback(self, conta_id: int) -> bool:
        """Reverte estado para ultimo snapshot."""
        try:
            snapshots = sorted(self.snapshots_dir.glob(f"{conta_id}_*.json"), reverse=True)
            if not snapshots:
                logger.warning(f"Nenhum snapshot para rollback de {conta_id}")
                return False

            with open(snapshots[0], "r", encoding="utf-8") as f:
                estado = json.load(f)

            self.salvar_estado(conta_id, estado)
            logger.warning(f"Rollback executado para {conta_id}: {snapshots[0].name}")
            return True
        except Exception as e:
            logger.error(f"Erro no rollback: {e}")
            return False

    def limpar_snapshots_antigos(self, dias: int = 7):
        """Remove snapshots com mais de N dias."""
        try:
            limite = datetime.now() - timedelta(days=dias)
            removidos = 0
            for snap in self.snapshots_dir.glob("*.json"):
                if snap.stat().st_mtime < limite.timestamp():
                    snap.unlink()
                    removidos += 1
            if removidos:
                logger.info(f"{removidos} snapshots antigos removidos")
        except Exception as e:
            logger.error(f"Erro ao limpar snapshots: {e}")

    # ==================== RESET DIARIO ====================

    def reset_diario(self):
        """Reset midnight — limpa contadores do dia."""
        hoje_str = datetime.now().strftime("%Y-%m-%d")
        global_state = self.load_global()

        if global_state.get("ultima_data_reset") == hoje_str:
            return  # Ja resetou hoje

        logger.info("Reset diario: limpando contadores")

        for conta_file in self.base_dir.glob("*.json"):
            if conta_file.name in ("global.json",):
                continue
            try:
                with open(conta_file, "r", encoding="utf-8") as f:
                    estado = json.load(f)
                estado["acoes_hoje"] = 0
                estado["acoes_ciclo"] = 0
                self._salvar_json(conta_file, estado)
            except Exception:
                pass

        global_state["ultima_data_reset"] = hoje_str
        self.save_global(global_state)

        # Limpar snapshots antigos
        self.limpar_snapshots_antigos(dias=7)

    # ==================== UTILITARIOS ====================

    def acoes_hoje_count(self, conta_id: int) -> int:
        """Retorna numero de acoes executadas hoje."""
        estado = self.carregar_estado(conta_id)
        return estado.get("acoes_hoje", 0)

    def acoes_ultima_hora(self, conta_id: int) -> int:
        """Conta acoes bem-sucedidas na ultima hora (ignora falhas/canceladas)."""
        uma_hora_atras = datetime.now() - timedelta(hours=1)
        changelog = self.carregar_changelog()
        return sum(
            1 for r in changelog
            if r.get("conta_id") == conta_id
            and datetime.fromisoformat(r["timestamp"]) >= uma_hora_atras
            and r.get("status", "sucesso") in ("sucesso", "simulado", "SUCESSO", "SIMULADO")
        )

    def resumo_contas(self) -> dict:
        """Retorna resumo de todas as contas."""
        resumo = {}
        for conta_file in self.base_dir.glob("*.json"):
            if conta_file.name in ("global.json",):
                continue
            try:
                with open(conta_file, "r", encoding="utf-8") as f:
                    estado = json.load(f)
                conta_id = estado.get("conta_id", conta_file.stem)
                resumo[conta_id] = {
                    "acoes_hoje": estado.get("acoes_hoje", 0),
                    "alertas": len(estado.get("alertas", [])),
                    "erros": len(estado.get("erros", [])),
                    "sessao": estado.get("sessao_status", "fechada"),
                    "ultima_acao": estado.get("ultima_acao"),
                }
            except Exception:
                pass
        return resumo
