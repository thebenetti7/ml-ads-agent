"""Testes para GuardrailsManager."""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field

import pytest

from app.core.guardrails import GuardrailsManager


# ==================== FIXTURES ====================


@dataclass
class MockConfig:
    contas: list = field(default_factory=lambda: [111, 222])
    work_blocks: list = field(default_factory=lambda: [(8, 0, 12, 0), (13, 30, 18, 0), (19, 0, 22, 0)])
    variacao_minutos: int = 30
    ram_min_livre_mb: int = 500
    cpu_max_pct: int = 80
    disco_min_livre_mb: int = 1000
    max_acoes_dia: int = 50
    max_acoes_hora: int = 20
    variacao_budget_max_pct: float = 15.0
    variacao_roas_max_pct: float = 10.0
    roas_min: float = 1.0
    roas_max: float = 35.0


class MockStateManager:
    def __init__(self):
        self._acoes_hoje = {}
        self._acoes_hora = {}
        self._alertas = []

    def acoes_hoje_count(self, conta_id):
        return self._acoes_hoje.get(conta_id, 0)

    def acoes_ultima_hora(self, conta_id):
        return self._acoes_hora.get(conta_id, 0)

    def alertar(self, conta_id, tipo, mensagem):
        self._alertas.append((conta_id, tipo, mensagem))


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def guardrails(tmp_dir):
    config = MockConfig()
    sm = MockStateManager()
    gm = GuardrailsManager(config, sm)
    gm.stop_file = tmp_dir / "STOP"
    return gm


# ==================== KILL SWITCH ====================


def test_kill_switch_inativo(guardrails):
    assert guardrails.kill_switch_ativo() is False


def test_kill_switch_ativo(guardrails):
    guardrails.ativar_kill_switch("teste")
    assert guardrails.kill_switch_ativo() is True


def test_kill_switch_desativar(guardrails):
    guardrails.ativar_kill_switch("teste")
    guardrails.desativar_kill_switch()
    assert guardrails.kill_switch_ativo() is False


# ==================== HORARIO ====================


def test_dentro_horario_10h(guardrails):
    """10:00 esta dentro do bloco 08:00-12:00."""
    from datetime import datetime

    with patch("app.core.guardrails.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 17, 10, 0)
        ok, motivo = guardrails.dentro_horario_operacao()
        assert ok is True


def test_fora_horario_3h(guardrails):
    """03:00 esta fora de todos os blocos."""
    from datetime import datetime

    with patch("app.core.guardrails.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 17, 3, 0)
        ok, motivo = guardrails.dentro_horario_operacao()
        assert ok is False
        assert "Fora do horario" in motivo


def test_dentro_horario_com_variacao(guardrails):
    """07:35 esta dentro do bloco 08:00-12:00 com variacao de 30min."""
    from datetime import datetime

    with patch("app.core.guardrails.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 17, 7, 35)
        ok, _ = guardrails.dentro_horario_operacao()
        assert ok is True


# ==================== RECURSOS ====================


def test_recursos_suficientes(guardrails):
    """Simula recursos OK."""
    mem = MagicMock()
    mem.available = 2 * 1024 * 1024 * 1024  # 2GB
    mem.percent = 50.0

    disco = MagicMock()
    disco.free = 10 * 1024 * 1024 * 1024  # 10GB

    with patch("app.core.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = mem
        mock_psutil.cpu_percent.return_value = 30.0
        mock_psutil.disk_usage.return_value = disco

        ok, detalhes = guardrails.recursos_suficientes()
        assert ok is True
        assert len(detalhes["motivos"]) == 0


def test_recursos_ram_baixa(guardrails):
    """Simula RAM abaixo do minimo."""
    mem = MagicMock()
    mem.available = 200 * 1024 * 1024  # 200MB
    mem.percent = 90.0

    disco = MagicMock()
    disco.free = 10 * 1024 * 1024 * 1024

    with patch("app.core.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = mem
        mock_psutil.cpu_percent.return_value = 30.0
        mock_psutil.disk_usage.return_value = disco

        ok, detalhes = guardrails.recursos_suficientes()
        assert ok is False
        assert any("RAM" in m for m in detalhes["motivos"])


def test_recursos_cpu_alta(guardrails):
    """Simula CPU acima do maximo."""
    mem = MagicMock()
    mem.available = 2 * 1024 * 1024 * 1024
    mem.percent = 50.0

    disco = MagicMock()
    disco.free = 10 * 1024 * 1024 * 1024

    with patch("app.core.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = mem
        mock_psutil.cpu_percent.return_value = 95.0
        mock_psutil.disk_usage.return_value = disco

        ok, detalhes = guardrails.recursos_suficientes()
        assert ok is False
        assert any("CPU" in m for m in detalhes["motivos"])


# ==================== LIMITES ====================


def test_limite_dia_nao_atingido(guardrails):
    ok, _ = guardrails.limite_acoes_atingido(111)
    assert ok is False


def test_limite_dia_atingido(guardrails):
    guardrails.state_manager._acoes_hoje[111] = 50
    ok, motivo = guardrails.limite_acoes_atingido(111)
    assert ok is True
    assert "diario" in motivo.lower()


def test_limite_hora_atingido(guardrails):
    guardrails.state_manager._acoes_hora[111] = 20
    ok, motivo = guardrails.limite_acoes_atingido(111)
    assert ok is True
    assert "horario" in motivo.lower()


# ==================== VARIACAO BUDGET ====================


def test_budget_variacao_ok(guardrails):
    ok, _ = guardrails.variacao_budget_ok(100.0, 110.0)  # 10%
    assert ok is True


def test_budget_variacao_excede(guardrails):
    ok, motivo = guardrails.variacao_budget_ok(100.0, 120.0)  # 20%
    assert ok is False
    assert "Variacao" in motivo


def test_budget_atual_zero(guardrails):
    ok, _ = guardrails.variacao_budget_ok(0, 300.0)
    assert ok is True  # Novo budget <= 500

    ok, _ = guardrails.variacao_budget_ok(0, 600.0)
    assert ok is False


# ==================== VARIACAO ROAS ====================


def test_roas_variacao_ok(guardrails):
    ok, _ = guardrails.variacao_roas_ok(10.0, 10.5)  # 5%
    assert ok is True


def test_roas_variacao_excede(guardrails):
    ok, motivo = guardrails.variacao_roas_ok(10.0, 12.0)  # 20%
    assert ok is False


def test_roas_fora_range(guardrails):
    ok, motivo = guardrails.variacao_roas_ok(5.0, 36.0)  # > 35
    assert ok is False
    assert "fora do range" in motivo.lower()


def test_roas_abaixo_minimo(guardrails):
    ok, motivo = guardrails.variacao_roas_ok(2.0, 0.5)  # < 1
    assert ok is False


# ==================== PODE_EXECUTAR ====================


def test_pode_executar_bloqueado_kill_switch(guardrails):
    guardrails.ativar_kill_switch("teste")
    ok, motivo = guardrails.pode_executar("pausar_campanha", 111)
    assert ok is False
    assert "Kill switch" in motivo


def test_pode_executar_validacao_tipo(guardrails):
    """Validacao especifica para criar_campanha sem budget."""
    from datetime import datetime

    with patch("app.core.guardrails.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 17, 10, 0)

        mem = MagicMock()
        mem.available = 2 * 1024 * 1024 * 1024
        mem.percent = 50.0
        disco = MagicMock()
        disco.free = 10 * 1024 * 1024 * 1024

        with patch("app.core.guardrails.psutil") as mock_psutil:
            mock_psutil.virtual_memory.return_value = mem
            mock_psutil.cpu_percent.return_value = 30.0
            mock_psutil.disk_usage.return_value = disco

            ok, motivo = guardrails.pode_executar(
                "criar_campanha", 111, {"budget": 0}
            )
            assert ok is False
            assert "Budget" in motivo
