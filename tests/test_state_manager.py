"""Testes para StateManager."""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.core.state_manager import StateManager


@pytest.fixture
def sm():
    with tempfile.TemporaryDirectory() as d:
        yield StateManager(base_dir=d)


# ==================== ATOMIC WRITE ====================


def test_salvar_e_carregar_json(sm):
    filepath = sm.base_dir / "test.json"
    dados = {"key": "value", "num": 42}
    sm._salvar_json(filepath, dados)

    resultado = sm._carregar_json(filepath)
    assert resultado["key"] == "value"
    assert resultado["num"] == 42


def test_carregar_json_inexistente(sm):
    filepath = sm.base_dir / "nao_existe.json"
    resultado = sm._carregar_json(filepath, {"default": True})
    assert resultado["default"] is True


def test_atomic_write_sem_arquivo_tmp_residual(sm):
    filepath = sm.base_dir / "test.json"
    sm._salvar_json(filepath, {"ok": True})
    assert not filepath.with_suffix(".json.tmp").exists()


# ==================== GLOBAL STATE ====================


def test_load_save_global(sm):
    state = sm.load_global()
    state["custom"] = "data"
    sm.save_global(state)

    reloaded = sm.load_global()
    assert reloaded["custom"] == "data"
    assert "timestamp" in reloaded


# ==================== CONTA STATE ====================


def test_carregar_estado_novo(sm):
    estado = sm.carregar_estado(111)
    assert estado["conta_id"] == 111
    assert estado["acoes_hoje"] == 0


def test_salvar_e_carregar_estado(sm):
    estado = sm.carregar_estado(111)
    estado["acoes_hoje"] = 5
    sm.salvar_estado(111, estado)

    reloaded = sm.carregar_estado(111)
    assert reloaded["acoes_hoje"] == 5
    assert "ultima_atualizacao" in reloaded


# ==================== CHANGELOG ====================


def test_registrar_acao(sm):
    sm.registrar_acao(
        conta_id=111,
        action_id="act-1",
        rule_id="R01",
        action_type="pausar",
        campaign_id="camp-1",
        campaign_name="Teste",
        params={"novo_status": "paused"},
        status="sucesso",
        duration_ms=1500,
    )

    # Verifica changelog
    changelog = sm.carregar_changelog()
    assert len(changelog) == 1
    assert changelog[0]["action_id"] == "act-1"
    assert changelog[0]["rule_id"] == "R01"

    # Verifica estado da conta
    estado = sm.carregar_estado(111)
    assert estado["acoes_hoje"] == 1
    assert len(estado["acoes"]) == 1


def test_registrar_multiplas_acoes(sm):
    for i in range(5):
        sm.registrar_acao(
            conta_id=111,
            action_id=f"act-{i}",
            action_type="pausar",
        )

    estado = sm.carregar_estado(111)
    assert estado["acoes_hoje"] == 5


def test_changelog_hoje_filtro_conta(sm):
    sm.registrar_acao(conta_id=111, action_id="a1")
    sm.registrar_acao(conta_id=222, action_id="a2")
    sm.registrar_acao(conta_id=111, action_id="a3")

    registros_111 = sm.changelog_hoje(conta_id=111)
    assert len(registros_111) == 2

    registros_222 = sm.changelog_hoje(conta_id=222)
    assert len(registros_222) == 1


def test_changelog_limite_50_acoes(sm):
    for i in range(60):
        sm.registrar_acao(conta_id=111, action_id=f"act-{i}")

    estado = sm.carregar_estado(111)
    assert len(estado["acoes"]) == 50  # Manteve ultimas 50


# ==================== COOLDOWN ====================


def test_cooldown_ok_sem_historico(sm):
    assert sm.cooldown_ok("R01", "camp-1", 7) is True


def test_cooldown_ativo(sm):
    sm.registrar_acao(
        conta_id=111,
        action_id="act-1",
        rule_id="R01",
        campaign_id="camp-1",
    )
    assert sm.cooldown_ok("R01", "camp-1", 7) is False


def test_cooldown_diferentes_regras(sm):
    sm.registrar_acao(
        conta_id=111,
        action_id="act-1",
        rule_id="R01",
        campaign_id="camp-1",
    )
    # R02 para mesma campanha deve estar livre
    assert sm.cooldown_ok("R02", "camp-1", 7) is True


# ==================== SESSAO ====================


def test_marcar_sessao(sm):
    sm.marcar_sessao_aberta(111)
    estado = sm.carregar_estado(111)
    assert estado["sessao_status"] == "aberta"

    sm.marcar_sessao_fechada(111)
    estado = sm.carregar_estado(111)
    assert estado["sessao_status"] == "fechada"


# ==================== ERROS ====================


def test_registrar_erro(sm):
    sm.registrar_erro(111, "Teste de erro", "Detalhes")
    estado = sm.carregar_estado(111)
    assert len(estado["erros"]) == 1
    assert estado["erros"][0]["mensagem"] == "Teste de erro"


def test_erros_limite_100(sm):
    for i in range(110):
        sm.registrar_erro(111, f"Erro {i}")

    estado = sm.carregar_estado(111)
    assert len(estado["erros"]) == 100


# ==================== ALERTAS ====================


def test_alertar(sm):
    sm.alertar(111, "AVISO", "Teste de alerta")
    estado = sm.carregar_estado(111)
    assert len(estado["alertas"]) == 1
    assert estado["alertas"][0]["tipo"] == "AVISO"


def test_limpar_alertas(sm):
    sm.alertar(111, "AVISO", "Alerta 1")
    sm.alertar(111, "CRITICO", "Alerta 2")
    sm.limpar_alertas(111)
    estado = sm.carregar_estado(111)
    assert len(estado["alertas"]) == 0


# ==================== SNAPSHOTS & ROLLBACK ====================


def test_snapshot_e_rollback(sm):
    estado = sm.carregar_estado(111)
    estado["acoes_hoje"] = 10
    sm.salvar_estado(111, estado)

    sm.snapshot_pre_ciclo(111)

    # Modificar estado
    estado["acoes_hoje"] = 99
    sm.salvar_estado(111, estado)

    # Rollback
    assert sm.rollback(111) is True

    estado_rollback = sm.carregar_estado(111)
    assert estado_rollback["acoes_hoje"] == 10


def test_rollback_sem_snapshot(sm):
    assert sm.rollback(999) is False


# ==================== RESET DIARIO ====================


def test_reset_diario(sm):
    sm.registrar_acao(conta_id=111, action_id="a1")
    sm.registrar_acao(conta_id=222, action_id="a2")

    estado_111 = sm.carregar_estado(111)
    assert estado_111["acoes_hoje"] == 1

    sm.reset_diario()

    estado_111 = sm.carregar_estado(111)
    assert estado_111["acoes_hoje"] == 0

    estado_222 = sm.carregar_estado(222)
    assert estado_222["acoes_hoje"] == 0


def test_reset_diario_idempotente(sm):
    sm.reset_diario()
    sm.reset_diario()  # Nao deve resetar de novo
    # Apenas verifica que nao da erro


# ==================== UTILITARIOS ====================


def test_acoes_hoje_count(sm):
    assert sm.acoes_hoje_count(111) == 0
    sm.registrar_acao(conta_id=111, action_id="a1")
    assert sm.acoes_hoje_count(111) == 1


def test_resumo_contas(sm):
    sm.registrar_acao(conta_id=111, action_id="a1")
    sm.registrar_acao(conta_id=222, action_id="a2")
    sm.alertar(111, "AVISO", "teste")

    resumo = sm.resumo_contas()
    assert 111 in resumo or "111" in resumo
