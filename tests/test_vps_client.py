"""Testes para VPSClient."""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import httpx

from app.api.vps_client import VPSClient
from app.models import AcaoExecutada, StatusAcao, Heartbeat, Alerta


# ==================== FIXTURES ====================


@pytest.fixture
def client():
    return VPSClient(base_url="https://test.example.com", api_key="test-key")


# ==================== BASICO ====================


def test_init(client):
    assert client.base_url == "https://test.example.com"
    assert client.online is True


# ==================== BUSCAR ACOES PENDENTES ====================


@pytest.mark.asyncio
async def test_buscar_acoes_200(client):
    """Retorna acoes quando VPS responde 200."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "acoes": [
            {
                "action_id": "act-1",
                "action_type": "PAUSAR_CAMPANHA",
                "priority": "ALTA",
                "target": {"campaign_id": "123", "campaign_name": "Teste"},
                "params": {},
            }
        ]
    }

    with patch.object(client, "_request", return_value=mock_resp):
        acoes = await client.buscar_acoes_pendentes(conta_id=111)
        assert len(acoes) == 1
        assert acoes[0].action_id == "act-1"


@pytest.mark.asyncio
async def test_buscar_acoes_vazia(client):
    """Retorna lista vazia quando nao ha acoes."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"acoes": []}

    with patch.object(client, "_request", return_value=mock_resp):
        acoes = await client.buscar_acoes_pendentes(conta_id=111)
        assert len(acoes) == 0


@pytest.mark.asyncio
async def test_buscar_acoes_offline(client):
    """Retorna lista vazia quando VPS offline."""
    with patch.object(client, "_request", return_value=None):
        acoes = await client.buscar_acoes_pendentes(conta_id=111)
        assert len(acoes) == 0


@pytest.mark.asyncio
async def test_buscar_acoes_401(client):
    """Retorna lista vazia com token invalido."""
    mock_resp = MagicMock()
    mock_resp.status_code = 401

    with patch.object(client, "_request", return_value=mock_resp):
        acoes = await client.buscar_acoes_pendentes(conta_id=111)
        assert len(acoes) == 0


# ==================== REPORTAR ACAO ====================


@pytest.mark.asyncio
async def test_reportar_acao_sucesso(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    resultado = AcaoExecutada(
        action_id="act-1",
        status=StatusAcao.SUCESSO,
        detalhes="Campanha pausada",
    )

    with patch.object(client, "_request", return_value=mock_resp):
        ok = await client.reportar_acao(resultado)
        assert ok is True


@pytest.mark.asyncio
async def test_reportar_acao_offline(client):
    resultado = AcaoExecutada(
        action_id="act-1",
        status=StatusAcao.SUCESSO,
    )

    with patch.object(client, "_request", return_value=None):
        ok = await client.reportar_acao(resultado)
        assert ok is False


# ==================== HEARTBEAT ====================


@pytest.mark.asyncio
async def test_heartbeat_com_comando(client):
    """VPS pode retornar comando no heartbeat."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"command": "pause", "reason": "manutencao"}

    heartbeat = Heartbeat()

    with patch.object(client, "_request", return_value=mock_resp):
        result = await client.enviar_heartbeat(heartbeat)
        assert result is not None
        assert result["command"] == "pause"


@pytest.mark.asyncio
async def test_heartbeat_sem_comando(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 204

    heartbeat = Heartbeat()

    with patch.object(client, "_request", return_value=mock_resp):
        result = await client.enviar_heartbeat(heartbeat)
        assert result is None


@pytest.mark.asyncio
async def test_heartbeat_offline(client):
    heartbeat = Heartbeat()

    with patch.object(client, "_request", return_value=None):
        result = await client.enviar_heartbeat(heartbeat)
        assert result is None


# ==================== ALERTAS ====================


@pytest.mark.asyncio
async def test_enviar_alerta(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 201

    alerta = Alerta(tipo="erro", mensagem="Sessao perdida", conta_id=111)

    with patch.object(client, "_request", return_value=mock_resp):
        ok = await client.enviar_alerta(alerta)
        assert ok is True


# ==================== CONFIG REMOTO ====================


@pytest.mark.asyncio
async def test_buscar_config(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"max_acoes_dia": 30, "dry_run": False}

    with patch.object(client, "_request", return_value=mock_resp):
        config = await client.buscar_config()
        assert config is not None
        assert config["max_acoes_dia"] == 30


@pytest.mark.asyncio
async def test_buscar_config_offline(client):
    with patch.object(client, "_request", return_value=None):
        config = await client.buscar_config()
        assert config is None


# ==================== RETRY / BACKOFF ====================


@pytest.mark.asyncio
async def test_request_retry_on_500(client):
    """Deve fazer retry em erro 500."""
    call_count = 0

    async def mock_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        if call_count < 3:
            resp.status_code = 500
        else:
            resp.status_code = 200
        return resp

    with patch.object(client.client, "request", side_effect=mock_request):
        # Patch sleep to avoid waiting
        with patch("app.api.vps_client.asyncio.sleep", new_callable=AsyncMock):
            resp = await client._request("GET", "/test", max_retries=3)
            assert resp is not None
            assert resp.status_code == 200
            assert call_count == 3


@pytest.mark.asyncio
async def test_request_connect_error_marks_offline(client):
    """ConnectError deve marcar como offline."""
    async def mock_request(method, url, **kwargs):
        raise httpx.ConnectError("Connection refused")

    with patch.object(client.client, "request", side_effect=mock_request):
        with patch("app.api.vps_client.asyncio.sleep", new_callable=AsyncMock):
            resp = await client._request("GET", "/test", max_retries=2)
            assert resp is None
            assert client.online is False
