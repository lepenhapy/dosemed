"""Constantes e funções utilitárias compartilhadas entre os testes."""

TELEFONE   = "65999990001"
TELEFONE_B = "65999990002"
PIN        = "1234"


def _criar_usuario(client, telefone=TELEFONE, nome="Teste"):
    r = client.post("/usuario", json={
        "telefone": telefone, "nome": nome, "aceite_lgpd": True,
    })
    assert r.status_code == 200, r.text
    return r.json()["session_token"]


def _definir_pin(client, telefone=TELEFONE, pin=PIN):
    r = client.post("/auth/definir-pin", json={"telefone": telefone, "pin": pin})
    assert r.status_code == 200, r.text


def _login(client, telefone=TELEFONE, pin=PIN):
    r = client.post("/auth/verificar-pin", json={"telefone": telefone, "pin": pin})
    assert r.status_code == 200, r.text
    return r.json()["session_token"]


def _auth(token):
    return {"X-Token": token}
