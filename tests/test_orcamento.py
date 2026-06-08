"""Testes de orçamento: auth, fluxo completo e proteção de ownership."""
import secrets
import pytest
from models import Farmacia, OrcamentoSolicitacao, OrcamentoResposta
from tests.helpers import TELEFONE, TELEFONE_B, _auth

TELEFONE_FARM = "65999990099"
PIN_FARM = "5678"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def farmacia(db):
    from helpers import hash_pin
    f = Farmacia(
        nome="Farmácia Teste", email="farm@teste.com",
        telefone_contato=TELEFONE_FARM, bairros="Centro",
        plano="basico", ativo=1,
        pin=hash_pin(PIN_FARM, TELEFONE_FARM),
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


@pytest.fixture
def sol_resposta(db, farmacia, item_id):
    """Cria solicitação e uma resposta pendente diretamente no banco."""
    sol = OrcamentoSolicitacao(
        usuario_id=TELEFONE, nome_med="Dipirona",
        quantidade_restante=2, status="coletando",
    )
    db.add(sol)
    db.flush()
    tok = secrets.token_urlsafe(16)
    resp = OrcamentoResposta(
        solicitacao_id=sol.id, farmacia_id=farmacia.id,
        token=tok, status="pendente",
    )
    db.add(resp)
    db.commit()
    return {"sol_id": sol.id, "resp_id": resp.id, "resp_token": tok}


# ---------------------------------------------------------------------------
# Auth — endpoints protegidos devem exigir token
# ---------------------------------------------------------------------------

def test_solicitar_sem_token_retorna_401(client, item_id):
    r = client.post(f"/estoque/{item_id}/solicitar-reposicao?telefone={TELEFONE}")
    assert r.status_code == 401


def test_solicitar_token_alheio_retorna_403(client, token_b, item_id):
    r = client.post(
        f"/estoque/{item_id}/solicitar-reposicao?telefone={TELEFONE}",
        headers=_auth(token_b),
    )
    assert r.status_code == 403


def test_solicitar_item_inexistente_retorna_404(client, token):
    r = client.post(
        f"/estoque/99999/solicitar-reposicao?telefone={TELEFONE}",
        headers=_auth(token),
    )
    assert r.status_code == 404


def test_listar_orcamentos_sem_token_retorna_401(client, token):
    r = client.get(f"/usuario/{TELEFONE}/orcamentos")
    assert r.status_code == 401


def test_listar_orcamentos_token_alheio_retorna_403(client, token_b):
    r = client.get(f"/usuario/{TELEFONE}/orcamentos", headers=_auth(token_b))
    assert r.status_code == 403


def test_escolher_sem_token_retorna_401(client, sol_resposta):
    r = client.post(
        f"/orcamento/{sol_resposta['sol_id']}/escolher/{sol_resposta['resp_id']}?telefone={TELEFONE}"
    )
    assert r.status_code == 401


def test_confirmar_sem_token_retorna_401(client, sol_resposta):
    r = client.post(
        f"/orcamento/{sol_resposta['sol_id']}/confirmar-entrega",
        json={"telefone": TELEFONE, "entregue": True},
    )
    assert r.status_code == 401


def test_avaliar_sem_token_retorna_401(client, sol_resposta):
    r = client.post(
        f"/orcamento/{sol_resposta['sol_id']}/avaliar-farmacia",
        json={"telefone": TELEFONE, "rating": 5},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Solicitar reposição
# ---------------------------------------------------------------------------

def test_solicitar_cria_solicitacao(client, token, farmacia, item_id):
    r = client.post(
        f"/estoque/{item_id}/solicitar-reposicao?telefone={TELEFONE}",
        headers=_auth(token),
    )
    assert r.status_code == 200
    data = r.json()
    assert "solicitacao_id" in data
    assert data["ok"] is True


# ---------------------------------------------------------------------------
# Ver orçamento (público — acessado pela farmácia via link no e-mail)
# ---------------------------------------------------------------------------

def test_ver_orcamento_token_invalido_retorna_404(client, sol_resposta):
    r = client.get("/orcamento/token-inexistente")
    assert r.status_code == 404


def test_ver_orcamento_valido(client, sol_resposta):
    r = client.get(f"/orcamento/{sol_resposta['resp_token']}")
    assert r.status_code == 200
    data = r.json()
    assert data["medicamento"] == "Dipirona"
    assert data["status_resposta"] == "pendente"


# ---------------------------------------------------------------------------
# Farmácia responde
# ---------------------------------------------------------------------------

def test_farmacia_responde_orcamento(client, sol_resposta):
    r = client.post(
        f"/orcamento/{sol_resposta['resp_token']}/responder",
        json={"preco": 45.90, "prazo_entrega": "1 dia útil", "formas_pagamento": "pix"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_farmacia_responde_preco_invalido_retorna_400(client, sol_resposta):
    r = client.post(
        f"/orcamento/{sol_resposta['resp_token']}/responder",
        json={"preco": 0, "prazo_entrega": "1 dia", "formas_pagamento": "pix"},
    )
    assert r.status_code == 400


def test_farmacia_nao_pode_responder_duas_vezes(client, sol_resposta):
    payload = {"preco": 30.0, "prazo_entrega": "1 dia", "formas_pagamento": "pix"}
    client.post(f"/orcamento/{sol_resposta['resp_token']}/responder", json=payload)
    r = client.post(f"/orcamento/{sol_resposta['resp_token']}/responder", json=payload)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Fluxo completo: solicitar → responder → escolher → confirmar → avaliar
# ---------------------------------------------------------------------------

def test_fluxo_completo(client, token, sol_resposta):
    tok = sol_resposta["resp_token"]
    sol_id = sol_resposta["sol_id"]
    resp_id = sol_resposta["resp_id"]

    # 1. Farmácia responde
    r = client.post(
        f"/orcamento/{tok}/responder",
        json={"preco": 30.0, "prazo_entrega": "2 dias", "formas_pagamento": "pix,dinheiro"},
    )
    assert r.status_code == 200

    # 2. Usuário vê seus orçamentos
    r = client.get(f"/usuario/{TELEFONE}/orcamentos", headers=_auth(token))
    assert r.status_code == 200
    assert any(s["id"] == sol_id for s in r.json())

    # 3. Usuário escolhe a farmácia
    r = client.post(
        f"/orcamento/{sol_id}/escolher/{resp_id}?telefone={TELEFONE}&modalidade=retirada",
        headers=_auth(token),
    )
    assert r.status_code == 200

    # 4. Confirma entrega
    r = client.post(
        f"/orcamento/{sol_id}/confirmar-entrega",
        json={"telefone": TELEFONE, "entregue": True},
        headers=_auth(token),
    )
    assert r.status_code == 200

    # 5. Avalia a farmácia
    r = client.post(
        f"/orcamento/{sol_id}/avaliar-farmacia",
        json={"telefone": TELEFONE, "rating": 5},
        headers=_auth(token),
    )
    assert r.status_code == 200

    # 6. Não pode avaliar duas vezes
    r = client.post(
        f"/orcamento/{sol_id}/avaliar-farmacia",
        json={"telefone": TELEFONE, "rating": 4},
        headers=_auth(token),
    )
    assert r.status_code == 400
