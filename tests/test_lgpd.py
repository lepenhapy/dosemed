"""Testes LGPD: exportação de dados e exclusão de conta."""
from tests.helpers import TELEFONE, TELEFONE_B, PIN, _auth, _definir_pin


def test_exportar_dados_retorna_json(client, token, item_id, alarme_id):
    r = client.get(f"/usuario/{TELEFONE}/exportar-dados", headers=_auth(token))
    assert r.status_code == 200
    data = r.json()
    assert "perfil" in data
    assert "estoque" in data
    assert "alarmes" in data
    assert "historico_buscas" in data
    assert data["perfil"]["telefone"] == TELEFONE
    assert len(data["estoque"]) == 1
    assert len(data["alarmes"]) == 1


def test_exportar_dados_sem_token_retorna_401(client, token):
    r = client.get(f"/usuario/{TELEFONE}/exportar-dados")
    assert r.status_code == 401


def test_exportar_dados_token_alheio_retorna_403(client, token, token_b):
    r = client.get(f"/usuario/{TELEFONE}/exportar-dados", headers=_auth(token_b))
    assert r.status_code == 403


def test_exportar_cabecalho_download(client, token):
    r = client.get(f"/usuario/{TELEFONE}/exportar-dados", headers=_auth(token))
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert ".json" in cd


def test_exportar_inclui_aviso_lgpd(client, token):
    r = client.get(f"/usuario/{TELEFONE}/exportar-dados", headers=_auth(token))
    assert "lgpd" in r.json().get("aviso_lgpd", "").lower()


def test_excluir_conta_remove_usuario(client, token):
    _definir_pin(client)
    r = client.request("DELETE", f"/usuario/{TELEFONE}",
                       json={"pin": PIN, "confirmacao": "EXCLUIR"})
    assert r.status_code == 200
    # Usuário não existe mais
    r2 = client.get(f"/usuario/{TELEFONE}")
    assert r2.status_code == 404


def test_excluir_conta_sem_confirmacao_retorna_400(client, token):
    _definir_pin(client)
    r = client.request("DELETE", f"/usuario/{TELEFONE}",
                       json={"pin": PIN, "confirmacao": "excluir"})
    assert r.status_code == 400
