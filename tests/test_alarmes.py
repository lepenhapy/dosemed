"""Testes de alarmes: CRUD e verificação de ownership."""
from tests.helpers import TELEFONE, TELEFONE_B, _auth


def test_criar_alarme(client, token):
    r = client.post("/alarmes", json={
        "telefone": TELEFONE, "nome_med": "Dipirona",
        "horario": "08:00", "dias": "1,2,3,4,5",
    }, headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["nome_med"] == "Dipirona"


def test_listar_alarmes_proprios(client, token, alarme_id):
    r = client.get(f"/alarmes/{TELEFONE}", headers=_auth(token))
    assert r.status_code == 200
    assert any(a["id"] == alarme_id for a in r.json())


def test_editar_alarme_proprio(client, token, alarme_id):
    r = client.put(f"/alarmes/{alarme_id}", json={
        "telefone": TELEFONE, "nome_med": "Ibuprofeno",
        "horario": "12:00", "dias": "1,2,3",
    }, headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["nome_med"] == "Ibuprofeno"


def test_editar_alarme_alheio_retorna_403(client, token_b, alarme_id):
    r = client.put(f"/alarmes/{alarme_id}", json={
        "telefone": TELEFONE_B, "nome_med": "Hack",
        "horario": "00:00", "dias": "1",
    }, headers=_auth(token_b))
    assert r.status_code == 403


def test_excluir_alarme_proprio(client, token, alarme_id):
    r = client.delete(f"/alarmes/{alarme_id}", headers=_auth(token))
    assert r.status_code == 200
    # Não aparece mais na listagem
    r2 = client.get(f"/alarmes/{TELEFONE}", headers=_auth(token))
    assert not any(a["id"] == alarme_id for a in r2.json())


def test_excluir_alarme_alheio_retorna_403(client, token_b, alarme_id):
    r = client.delete(f"/alarmes/{alarme_id}", headers=_auth(token_b))
    assert r.status_code == 403


def test_horario_invalido_retorna_400(client, token):
    # "8:00" não bate no padrão HH:MM (dois dígitos obrigatórios)
    r = client.post("/alarmes", json={
        "telefone": TELEFONE, "nome_med": "X",
        "horario": "8:00", "dias": "1",
    }, headers=_auth(token))
    assert r.status_code == 400
