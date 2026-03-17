"""Testes para DecisionEngine — 13 regras R01-R13."""

import tempfile
from dataclasses import dataclass

import pytest

from app.config import ContaInfo
from app.core.state_manager import StateManager
from app.core.decision_engine import DecisionEngine


# ==================== FIXTURES ====================


class MockConfig:
    def get_conta_info(self, conta_id):
        return ContaInfo(margem=0.08, roas_breakeven=12.5)


@pytest.fixture
def sm():
    with tempfile.TemporaryDirectory() as d:
        yield StateManager(base_dir=d)


@pytest.fixture
def engine(sm):
    config = MockConfig()
    return DecisionEngine(config, sm)


def camp(id="1", name="Campanha Teste", status="active", roas=5.0,
         clicks=200, budget=100.0, roas_target=5.0, strategy="VISIBILITY",
         lost_imp=0, imp_share=50, cpc=1.0, cvr=5.0, cost=50.0,
         last_updated="2026-03-17T10:00:00"):
    """Helper para criar campanha mock."""
    return {
        "id": id, "name": name, "status": status,
        "budget": budget, "roas_target": roas_target,
        "strategy": strategy, "last_updated": last_updated,
        "metrics": {
            "roas": roas, "clicks": clicks, "cost": cost,
            "lost_impression_share_by_budget": lost_imp,
            "impression_share": imp_share,
            "cpc": cpc, "cvr": cvr,
        },
    }


def ad(item_id="MLB123", campaign_id="1", status="active",
       clicks=0, vendas=0, impressoes=100, recommended=False):
    """Helper para criar anuncio mock."""
    return {
        "item_id": item_id, "campaign_id": campaign_id,
        "status": status, "recommended": recommended,
        "metrics": {
            "clicks": clicks,
            "direct_items_quantity": vendas,
            "prints": impressoes,
        },
    }


# ==================== R01: PAUSAR ROAS NEGATIVO ====================


def test_R01_roas_baixo_com_cliques(engine):
    campanhas = [camp(roas=0.5, clicks=200)]
    acoes = engine.R01_pausar_roas_negativo(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 1
    assert acoes[0].rule_id == "R01"
    assert acoes[0].action_type == "pausar"


def test_R01_roas_baixo_poucos_cliques(engine):
    """Nao deve pausar com menos de 150 cliques."""
    campanhas = [camp(roas=0.5, clicks=100)]
    acoes = engine.R01_pausar_roas_negativo(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 0


def test_R01_roas_ok(engine):
    campanhas = [camp(roas=5.0, clicks=200)]
    acoes = engine.R01_pausar_roas_negativo(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 0


def test_R01_campanha_pausada_ignorada(engine):
    campanhas = [camp(roas=0.5, clicks=200, status="paused")]
    acoes = engine.R01_pausar_roas_negativo(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 0


# ==================== R02: REMOVER ZERO VENDAS ====================


def test_R02_sem_vendas_muitos_cliques(engine):
    campanhas = [camp()]
    anuncios = [ad(clicks=250, vendas=0)]
    acoes = engine.R02_remover_anuncio_zero_vendas(campanhas, anuncios, {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 1
    assert acoes[0].rule_id == "R02"


def test_R02_com_vendas(engine):
    anuncios = [ad(clicks=250, vendas=5)]
    acoes = engine.R02_remover_anuncio_zero_vendas([], anuncios, {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 0


def test_R02_poucos_cliques(engine):
    anuncios = [ad(clicks=100, vendas=0)]
    acoes = engine.R02_remover_anuncio_zero_vendas([], anuncios, {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 0


# ==================== R03: EXCESSO BUDGET ====================


def test_R03_budget_excedido(engine):
    campanhas = [
        camp(id="1", cost=300.0, roas=2.0),
        camp(id="2", cost=250.0, roas=8.0),
    ]
    acoes = engine.R03_pausar_excesso_budget(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) >= 1
    # Deve pausar a de menor ROAS primeiro
    assert acoes[0].campaign_id == "1"


def test_R03_budget_ok(engine):
    campanhas = [camp(cost=200.0), camp(cost=200.0)]
    acoes = engine.R03_pausar_excesso_budget(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 0


# ==================== R04: HOLD ====================


def test_R04_anuncio_hold(engine):
    campanhas = [camp()]
    anuncios = [ad(status="hold")]
    acoes = engine.R04_verificar_anuncios_hold(campanhas, anuncios, {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 1
    assert acoes[0].rule_id == "R04"


def test_R04_anuncio_ativo(engine):
    anuncios = [ad(status="active")]
    acoes = engine.R04_verificar_anuncios_hold([], anuncios, {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 0


# ==================== R05: AUMENTAR BUDGET IMPRESSOES ====================


def test_R05_lost_impression_alta_roas_bom(engine):
    campanhas = [camp(lost_imp=40, roas=8.0, roas_target=5.0, budget=100)]
    acoes = engine.R05_aumentar_budget_impressoes(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 1
    assert acoes[0].params["percentual"] == 15


def test_R05_lost_impression_baixa(engine):
    campanhas = [camp(lost_imp=10, roas=8.0, roas_target=5.0)]
    acoes = engine.R05_aumentar_budget_impressoes(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 0


# ==================== R06: AUMENTAR BUDGET ROAS ALTO ====================


def test_R06_roas_dobro_target(engine):
    campanhas = [camp(roas=12.0, roas_target=5.0, imp_share=30, budget=100)]
    acoes = engine.R06_aumentar_budget_roas_alto(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 1
    assert acoes[0].params["percentual"] == 20


def test_R06_roas_normal(engine):
    campanhas = [camp(roas=6.0, roas_target=5.0, imp_share=30)]
    acoes = engine.R06_aumentar_budget_roas_alto(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 0


# ==================== R07: AUMENTAR ROAS TARGET ====================


def test_R07_roas_entre_breakeven_e_target(engine):
    """ROAS entre break-even (12.5) e target (15) -> aumentar target."""
    campanhas = [camp(roas=13.0, roas_target=15.0)]
    acoes = engine.R07_aumentar_roas_target(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 1
    assert acoes[0].params["percentual"] == 10


def test_R07_roas_abaixo_breakeven(engine):
    campanhas = [camp(roas=5.0, roas_target=15.0)]
    acoes = engine.R07_aumentar_roas_target(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 0


# ==================== R08: MUDAR ESTRATEGIA ====================


def test_R08_cpc_alto_cvr_baixo(engine):
    campanhas = [camp(cpc=3.0, cvr=2.0, strategy="VISIBILITY")]
    acoes = engine.R08_mudar_estrategia_cpc(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 1
    assert acoes[0].params["nova_estrategia"] == "PROFITABILITY"


def test_R08_ja_profitability(engine):
    campanhas = [camp(cpc=3.0, cvr=2.0, strategy="PROFITABILITY")]
    acoes = engine.R08_mudar_estrategia_cpc(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 0


# ==================== R09: CAMPANHA PAUSADA 30+ DIAS ====================


def test_R09_pausada_longa(engine):
    from datetime import datetime, timedelta
    old = (datetime.now() - timedelta(days=35)).isoformat()
    campanhas = [camp(status="paused", last_updated=old)]
    acoes = engine.R09_remover_campanha_pausada(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 1
    assert acoes[0].rule_id == "R09"


def test_R09_pausada_recente(engine):
    from datetime import datetime, timedelta
    recent = (datetime.now() - timedelta(days=5)).isoformat()
    campanhas = [camp(status="paused", last_updated=recent)]
    acoes = engine.R09_remover_campanha_pausada(campanhas, [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 0


# ==================== R10: BAIXA IMPRESSAO ====================


def test_R10_poucas_impressoes(engine):
    campanhas = [camp()]
    anuncios = [ad(impressoes=30)]
    acoes = engine.R10_mover_anuncio_baixa_impressao(campanhas, anuncios, {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 1
    assert acoes[0].params["destino"] == "VISIBILITY"


def test_R10_impressoes_ok(engine):
    anuncios = [ad(impressoes=200)]
    acoes = engine.R10_mover_anuncio_baixa_impressao([], anuncios, {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 0


# ==================== R11: CONSOLIDAR ====================


def test_R11_campanha_com_poucos_ativos(engine):
    campanhas = [camp(id="1")]
    anuncios = [
        ad(item_id="A", campaign_id="1", status="active"),
        ad(item_id="B", campaign_id="1", status="paused"),
        ad(item_id="C", campaign_id="1", status="paused"),
    ]
    acoes = engine.R11_consolidar_campanha(campanhas, anuncios, {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 1


# ==================== R12: CRIAR CAMPANHA RECOMENDADOS ====================


def test_R12_muitos_recomendados(engine):
    anuncios = [
        ad(item_id=f"MLB{i}", status="idle", recommended=True)
        for i in range(10)
    ]
    acoes = engine.R12_criar_campanha_recomendados([], anuncios, {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 1
    assert acoes[0].rule_id == "R12"
    assert acoes[0].action_type == "criar_campanha"


def test_R12_poucos_recomendados(engine):
    anuncios = [
        ad(item_id=f"MLB{i}", status="idle", recommended=True)
        for i in range(3)
    ]
    acoes = engine.R12_criar_campanha_recomendados([], anuncios, {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 0


# ==================== R13: CURVA A ====================


def test_R13_produtos_curva_a_sem_ads(engine):
    campanhas = [camp(id="1", name="Estrelas")]
    anuncios = [ad(item_id="MLB1", campaign_id="1")]
    estado = {"produtos_curva_a": ["MLB1", "MLB2", "MLB3"]}

    acoes = engine.R13_adicionar_produtos_curva_a(campanhas, anuncios, estado, ContaInfo(0.08, 12.5))
    assert len(acoes) == 1
    # MLB1 ja tem ads, entao deve recomendar MLB2 e MLB3
    assert "MLB2" in acoes[0].params["anuncios"]


def test_R13_sem_curva_a(engine):
    acoes = engine.R13_adicionar_produtos_curva_a([], [], {}, ContaInfo(0.08, 12.5))
    assert len(acoes) == 0


# ==================== EVALUATE COMPLETO ====================


def test_evaluate_only_critical(engine):
    """Modo offline: so R01-R04."""
    campanhas = [camp(roas=0.5, clicks=200)]
    anuncios = [ad(clicks=250, vendas=0)]

    acoes = engine.evaluate(campanhas, anuncios, {}, conta_id=111, only_critical=True)
    for acao in acoes:
        assert acao.rule_id in ("R01", "R02", "R03", "R04")


def test_evaluate_ordenacao_prioridade(engine):
    """Acoes criticas devem vir antes de otimizacao."""
    campanhas = [
        camp(id="1", roas=0.5, clicks=200),  # R01 critica
        camp(id="2", roas=12.0, roas_target=5.0, imp_share=30, budget=100),  # R06 otimizacao
    ]
    acoes = engine.evaluate(campanhas, [], {}, conta_id=111)

    if len(acoes) >= 2:
        assert acoes[0].priority <= acoes[1].priority
