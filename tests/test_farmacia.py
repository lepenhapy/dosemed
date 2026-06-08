"""Testes da farmácia: auto-cadastro, PIN, auth, dashboard, perfil."""
import pytest
from tests.helpers import _auth

TELEFONE_FARM = "65999990077"
PIN_FARM = "4321"


# ---------------------------------------------------------------------------
# Helpers de teste
# ---------------------------------------------------------------------------

def _cadastrar(client, telefone=TELEFONE_FARM):
    return client.post("/farmacia/cadastro", json={
        "telefone": telefone,
        "nome": "Farmácia Boa Saúde",
        "email": "boas@saude.com",
        "bairros": "Centro",
    })


def _definir_pin(client, telefone=TELEFONE_FARM, pin=PIN_FARM):
    return client.post("/auth/farmacia/definir-pin", json={
        "telefone": telefone, "pin": pin,
    })


def _login(client, telefone=TELEFONE_FARM, pin=PIN_FARM):
    r = client.post("/auth/verificar-pin", json={"telefone": telefone, "pin": pin})
    assert r.status_code == 200, r.text
    return r.json()["session_token"]


# ---------------------------------------------------------------------------
# Cadastro
# ---------------------------------------------------------------------------

def test_cadastro_retorna_token(client):
    r = _cadastrar(client)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "session_token" in data
    assert data["session_token"] is not None


def test_cadastro_telefone_duplicado_retorna_409(client):
    _cadastrar(client)
    r = _cadastrar(client)
    assert r.status_code == 409


def test_cadastro_telefone_invalido_retorna_400(client):
    r = client.post("/farmacia/cadastro", json={
        "telefone": "123", "nome": "X", "email": "x@x.com",
    })
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# PIN
# ---------------------------------------------------------------------------

def test_definir_pin_retorna_token(client):
    _cadastrar(client)
    r = _definir_pin(client)
    assert r.status_code == 200
    assert "session_token" in r.json()


def test_definir_pin_ja_existente_retorna_400(client):
    _cadastrar(client)
    _definir_pin(client)
    r = _definir_pin(client)
    assert r.status_code == 400
    assert "já definido" in r.json()["detail"].lower()


def test_definir_pin_curto_retorna_400(client):
    _cadastrar(client)
    r = client.post("/auth/farmacia/definir-pin", json={
        "telefone": TELEFONE_FARM, "pin": "12",
    })
    assert r.status_code == 400


def test_definir_pin_farmacia_inexistente_retorna_404(client):
    r = _definir_pin(client, telefone="65999990000")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def test_login_correto_retorna_token(client):
    _cadastrar(client)
    _definir_pin(client)
    r = client.post("/auth/verificar-pin", json={"telefone": TELEFONE_FARM, "pin": PIN_FARM})
    assert r.status_code == 200
    data = r.json()
    assert data["tipo"] == "farmacia"
    assert "session_token" in data


def test_login_pin_errado_retorna_401(client):
    _cadastrar(client)
    _definir_pin(client)
    r = client.post("/auth/verificar-pin", json={"telefone": TELEFONE_FARM, "pin": "0000"})
    assert r.status_code == 401


def test_login_sem_pin_retorna_token(client):
    """Farmácia sem PIN ainda consegue token ao verificar — para completar o onboarding."""
    _cadastrar(client)
    r = client.post("/auth/verificar-pin", json={"telefone": TELEFONE_FARM, "pin": ""})
    assert r.status_code == 200
    assert r.json()["tipo"] == "farmacia"


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def test_dashboard_sem_token_retorna_401(client):
    _cadastrar(client)
    _definir_pin(client)
    r = client.get(f"/farmacia/dashboard?telefone={TELEFONE_FARM}")
    assert r.status_code == 401


def test_dashboard_token_invalido_retorna_401(client):
    _cadastrar(client)
    _definir_pin(client)
    r = client.get(f"/farmacia/dashboard?telefone={TELEFONE_FARM}",
                   headers={"X-Token": "invalido"})
    assert r.status_code == 401


def test_dashboard_token_valido(client):
    _cadastrar(client)
    _definir_pin(client)
    tok = _login(client)
    r = client.get(f"/farmacia/dashboard?telefone={TELEFONE_FARM}",
                   headers={"X-Token": tok})
    assert r.status_code == 200
    data = r.json()
    assert data["farmacia"]["nome"] == "Farmácia Boa Saúde"
    assert "leads_recentes" in data


def test_dashboard_token_alheio_retorna_403(client):
    _cadastrar(client, telefone="65999990001")
    _definir_pin(client, telefone="65999990001")
    tok = _login(client, telefone="65999990001")
    _cadastrar(client, telefone=TELEFONE_FARM)

    r = client.get(f"/farmacia/dashboard?telefone={TELEFONE_FARM}",
                   headers={"X-Token": tok})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Perfil
# ---------------------------------------------------------------------------

def test_atualizar_perfil_sem_token_retorna_401(client):
    _cadastrar(client)
    r = client.put(f"/farmacia/perfil?telefone={TELEFONE_FARM}",
                   json={"bairros": "Centro"})
    assert r.status_code == 401


def test_atualizar_perfil_salva_bairros(client):
    _cadastrar(client)
    _definir_pin(client)
    tok = _login(client)

    r = client.put(f"/farmacia/perfil?telefone={TELEFONE_FARM}",
                   json={"bairros": "Centro, Jardim América", "atende_manipulado": 1},
                   headers={"X-Token": tok})
    assert r.status_code == 200

    r2 = client.get(f"/farmacia/dashboard?telefone={TELEFONE_FARM}",
                    headers={"X-Token": tok})
    farm = r2.json()["farmacia"]
    assert "Jardim" in farm["bairros"]
    assert farm["atende_manipulado"] is True


def test_atualizar_servicos_farmacia(client):
    _cadastrar(client)
    _definir_pin(client)
    tok = _login(client)

    import json
    r = client.put(f"/farmacia/perfil?telefone={TELEFONE_FARM}",
                   json={"servicos": json.dumps(["pressao", "glicose"])},
                   headers={"X-Token": tok})
    assert r.status_code == 200

    r2 = client.get(f"/farmacia/dashboard?telefone={TELEFONE_FARM}",
                    headers={"X-Token": tok})
    import json as _json
    servicos = _json.loads(r2.json()["farmacia"].get("servicos") or "[]")
    assert "pressao" in servicos
