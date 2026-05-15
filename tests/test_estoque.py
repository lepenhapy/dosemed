"""Testes de estoque: CRUD e verificação de ownership."""
import pytest
from tests.helpers import TELEFONE, TELEFONE_B, _auth


def test_dashboard_mostra_item(client, token, item_id):
    r = client.get(f"/dashboard/{TELEFONE}", headers=_auth(token))
    assert r.status_code == 200
    nomes = [i["nome"] for cat in r.json()["estoque"].values() for i in cat]
    assert "Dipirona" in nomes


def test_editar_item_proprio(client, token, item_id):
    r = client.put(f"/estoque/{item_id}",
                   json={"nome_medicamento": "Paracetamol"},
                   headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["item"]["nome"] == "Paracetamol"


def test_editar_item_alheio_retorna_403(client, token_b, item_id):
    r = client.put(f"/estoque/{item_id}",
                   json={"nome_medicamento": "Hack"},
                   headers=_auth(token_b))
    assert r.status_code == 403


def test_iniciar_item_proprio(client, token, item_id):
    r = client.post(f"/estoque/{item_id}/iniciar", headers=_auth(token))
    assert r.status_code == 200


def test_iniciar_item_alheio_retorna_403(client, token_b, item_id):
    r = client.post(f"/estoque/{item_id}/iniciar", headers=_auth(token_b))
    assert r.status_code == 403


def test_consumir_item_proprio(client, token, item_id):
    r = client.post(f"/estoque/{item_id}/consumir", headers=_auth(token))
    assert r.status_code == 200


def test_consumir_item_alheio_retorna_403(client, token_b, item_id):
    r = client.post(f"/estoque/{item_id}/consumir", headers=_auth(token_b))
    assert r.status_code == 403


def test_excluir_item_proprio(client, token, item_id):
    r = client.delete(f"/estoque/{item_id}", headers=_auth(token))
    assert r.status_code == 200
    # Dashboard não mostra mais o item
    r2 = client.get(f"/dashboard/{TELEFONE}", headers=_auth(token))
    nomes = [i["nome"] for cat in r2.json()["estoque"].values() for i in cat]
    assert "Dipirona" not in nomes


def test_excluir_item_alheio_retorna_403(client, token_b, item_id):
    r = client.delete(f"/estoque/{item_id}", headers=_auth(token_b))
    assert r.status_code == 403


def test_historico_sem_token_retorna_401(client, token):
    r = client.get(f"/historico/{TELEFONE}")
    assert r.status_code == 401


def test_historico_token_alheio_retorna_403(client, token_b):
    r = client.get(f"/historico/{TELEFONE}", headers=_auth(token_b))
    assert r.status_code == 403


def test_historico_token_proprio(client, token):
    r = client.get(f"/historico/{TELEFONE}", headers=_auth(token))
    assert r.status_code == 200
    assert "por_mes" in r.json()
