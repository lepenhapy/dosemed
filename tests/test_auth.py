"""Testes de autenticação: cadastro, PIN, token, bloqueio de conta."""
from tests.helpers import TELEFONE, PIN, _criar_usuario, _definir_pin, _login, _auth


def test_cadastro_retorna_token(client):
    r = client.post("/usuario", json={"telefone": TELEFONE, "nome": "A", "aceite_lgpd": True})
    assert r.status_code == 200
    assert "session_token" in r.json()
    assert r.json()["session_token"] is not None


def test_cadastro_telefone_duplicado(client, token):
    r = client.post("/usuario", json={"telefone": TELEFONE, "nome": "B", "aceite_lgpd": True})
    assert r.status_code == 409


def test_get_usuario_sem_pin_retorna_token(client, token):
    r = client.get(f"/usuario/{TELEFONE}")
    assert r.status_code == 200
    assert r.json()["tem_pin"] is False
    assert r.json()["session_token"] is not None


def test_get_usuario_com_pin_nao_retorna_token(client, token_com_pin):
    r = client.get(f"/usuario/{TELEFONE}")
    assert r.status_code == 200
    assert r.json()["tem_pin"] is True
    assert r.json()["session_token"] is None


def test_verificar_pin_correto_retorna_token(client, token_com_pin):
    r = client.post("/auth/verificar-pin", json={"telefone": TELEFONE, "pin": PIN})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "session_token" in data


def test_verificar_pin_errado_retorna_401(client, token_com_pin):
    r = client.post("/auth/verificar-pin", json={"telefone": TELEFONE, "pin": "0000"})
    assert r.status_code == 401
    assert "restante" in r.json()["detail"]


def test_bloqueio_apos_5_tentativas(client, token_com_pin):
    for i in range(4):
        r = client.post("/auth/verificar-pin", json={"telefone": TELEFONE, "pin": "0000"})
        assert r.status_code == 401

    # 5ª tentativa — bloqueia
    r = client.post("/auth/verificar-pin", json={"telefone": TELEFONE, "pin": "0000"})
    assert r.status_code == 429
    assert "bloqueada" in r.json()["detail"].lower()


def test_bloqueio_impede_pin_correto(client, token_com_pin):
    for _ in range(5):
        client.post("/auth/verificar-pin", json={"telefone": TELEFONE, "pin": "0000"})

    # Mesmo com PIN correto, conta está bloqueada
    r = client.post("/auth/verificar-pin", json={"telefone": TELEFONE, "pin": PIN})
    assert r.status_code == 429


def test_sucesso_zera_contador(client, token_com_pin):
    # 3 erros
    for _ in range(3):
        client.post("/auth/verificar-pin", json={"telefone": TELEFONE, "pin": "0000"})
    # Login correto — zera
    r = client.post("/auth/verificar-pin", json={"telefone": TELEFONE, "pin": PIN})
    assert r.status_code == 200
    # Após reset, pode errar de novo sem bloquear imediatamente
    r = client.post("/auth/verificar-pin", json={"telefone": TELEFONE, "pin": "0000"})
    assert r.status_code == 401
    assert "4" in r.json()["detail"]  # 4 restantes (não 1)


def test_dashboard_sem_token_retorna_401(client, token):
    r = client.get(f"/dashboard/{TELEFONE}")
    assert r.status_code == 401


def test_dashboard_token_invalido_retorna_401(client, token):
    r = client.get(f"/dashboard/{TELEFONE}", headers={"X-Token": "invalido"})
    assert r.status_code == 401


def test_dashboard_token_valido(client, token):
    r = client.get(f"/dashboard/{TELEFONE}", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["usuario"]["telefone"] == TELEFONE
