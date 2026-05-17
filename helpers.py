# helpers.py — utility functions, email, push, Asaas, bulas

import re
import os
import io
import json
import base64
import hashlib
import secrets
import smtplib
import logging
import requests
from datetime import date, timedelta, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

try:
    from pywebpush import webpush, WebPushException
    PUSH_OK = True
except ImportError:
    PUSH_OK = False

try:
    import keyring
    KEYRING_OK = True
except ImportError:
    KEYRING_OK = False

try:
    import anthropic
    ANTHROPIC_OK = True
except ImportError:
    ANTHROPIC_OK = False

from PIL import Image
from fastapi import HTTPException
from sqlalchemy.orm import Session

from config import DDD_ESTADO
from models import (
    Estoque, AlarmeRemedio, LogBusca, Usuario, PushSub,
)

logger = logging.getLogger("dosemed")

# ---------------------------------------------------------------------------
# VAPID / Web Push
# ---------------------------------------------------------------------------

def _gerar_vapid_keys():
    """Gera par de chaves VAPID e persiste no .env."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.backends import default_backend
        priv = ec.generate_private_key(ec.SECP256R1(), default_backend())
        pub = priv.public_key()
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat, PrivateFormat, NoEncryption
        )
        priv_b64 = base64.urlsafe_b64encode(
            priv.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
        ).decode().rstrip("=")
        pub_bytes = pub.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        pub_b64 = base64.urlsafe_b64encode(pub_bytes).decode().rstrip("=")
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        with open(env_path, "a", encoding="utf-8") as f:
            f.write(f"\nVAPID_PRIVATE_KEY={priv_b64}\nVAPID_PUBLIC_KEY={pub_b64}\n")
        os.environ["VAPID_PRIVATE_KEY"] = priv_b64
        os.environ["VAPID_PUBLIC_KEY"] = pub_b64
        logger.info("Chaves VAPID geradas e salvas no .env")
    except Exception as e:
        logger.error(f"Erro ao gerar VAPID: {e}")


_vpub = os.getenv("VAPID_PUBLIC_KEY", "")
_vpriv = os.getenv("VAPID_PRIVATE_KEY", "")
if PUSH_OK and (not _vpub or not _vpriv):
    _gerar_vapid_keys()


def enviar_push(sub: PushSub, titulo: str, corpo: str) -> bool:
    if not PUSH_OK:
        return False
    priv = os.getenv("VAPID_PRIVATE_KEY", "")
    if not priv:
        return False
    try:
        webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth}
            },
            data=json.dumps({"title": titulo, "body": corpo}),
            vapid_private_key=priv,
            vapid_claims={"sub": "mailto:admin@dosemed.app"},
        )
        return True
    except Exception as e:
        logger.warning(f"Push falhou para {sub.usuario_id}: {type(e).__name__}: {e}")
        return False


# ---------------------------------------------------------------------------
# Asaas
# ---------------------------------------------------------------------------

def _asaas_headers() -> dict:
    return {"access_token": os.getenv("ASAAS_KEY", ""), "Content-Type": "application/json"}


def _asaas_url(path: str) -> str:
    env = os.getenv("ASAAS_ENV", "sandbox")
    base = "https://api.asaas.com/v3" if env == "production" else "https://sandbox.asaas.com/api/v3"
    return f"{base}{path}"


def asaas_criar_link_pagamento(valor: float, descricao: str, ref_externa: str):
    """Tenta paymentLinks; se falhar, cria cliente sem CNPJ + cobrança. Retorna (url, charge_ref, erro)."""
    if not os.getenv("ASAAS_KEY"):
        return None, None, "ASAAS_KEY não configurada"

    for billing in ("BOLETO", "PIX", "CREDIT_CARD"):
        payload_link = {
            "name": descricao[:50],
            "value": round(valor, 2),
            "billingType": billing,
            "chargeType": "DETACHED",
            "dueDateLimitDays": 5,
            "externalReference": ref_externa,
            "description": descricao,
        }
        try:
            r = requests.post(_asaas_url("/paymentLinks"), json=payload_link,
                              headers=_asaas_headers(), timeout=30)
            if r.ok:
                data = r.json()
                url = data.get("url")
                if url:
                    logger.info(f"Asaas paymentLink criado ({billing}) — {url}")
                    return url, data.get("id"), None
        except Exception:
            pass

    payload_cli = {"name": descricao[:40], "email": "pagamento@dosemed.app"}
    try:
        rc = requests.post(_asaas_url("/customers"), json=payload_cli,
                           headers=_asaas_headers(), timeout=30)
        if rc.ok:
            customer_id = rc.json().get("id")
            if customer_id:
                venc = str((date.today() + timedelta(days=3)))
                payload_charge = {
                    "customer": customer_id,
                    "billingType": "PIX",
                    "value": round(valor, 2),
                    "dueDate": venc,
                    "description": descricao,
                    "externalReference": ref_externa,
                }
                rp = requests.post(_asaas_url("/payments"), json=payload_charge,
                                   headers=_asaas_headers(), timeout=30)
                if rp.ok:
                    data = rp.json()
                    url = data.get("invoiceUrl")
                    logger.info(f"Asaas cobrança anônima criada — {url}")
                    return url, data.get("id"), None
                logger.warning(f"Asaas cobrança falhou: {rp.status_code} {rp.text[:200]}")
        else:
            logger.warning(f"Asaas cliente anônimo falhou: {rc.status_code} {rc.text[:200]}")
    except Exception as e:
        logger.error(f"Asaas fallback: {e}")

    return None, None, "Não foi possível gerar cobrança no Asaas (verifique configurações da conta)"


def asaas_criar_cliente(nome: str, email: str, cnpj: str = None):
    """Retorna (customer_id, erro). customer_id é None em caso de falha."""
    if not os.getenv("ASAAS_KEY"):
        return None, "ASAAS_KEY não configurada"
    if not cnpj:
        return None, f"CNPJ não informado para '{nome}'"
    payload = {"name": nome, "email": email, "cpfCnpj": re.sub(r"\D", "", cnpj)}
    try:
        r = requests.post(_asaas_url("/customers"), json=payload, headers=_asaas_headers(), timeout=30)
        if r.ok:
            return r.json().get("id"), ""
        erro = f"HTTP {r.status_code}: {r.text[:300]}"
        logger.warning(f"Asaas criar cliente falhou — {erro}")
        return None, erro
    except Exception as e:
        logger.error(f"Asaas criar cliente: {e}")
        return None, str(e)


def asaas_criar_assinatura(customer_id: str, valor: float, descricao: str) -> str | None:
    if not os.getenv("ASAAS_KEY") or not customer_id:
        return None
    proximo = (date.today().replace(day=1) + timedelta(days=32)).replace(day=1)
    payload = {
        "customer": customer_id,
        "billingType": "PIX",
        "value": valor,
        "nextDueDate": str(proximo),
        "cycle": "MONTHLY",
        "description": descricao,
    }
    try:
        r = requests.post(_asaas_url("/subscriptions"), json=payload, headers=_asaas_headers(), timeout=30)
        if r.ok:
            return r.json().get("id")
    except Exception as e:
        logger.error(f"Asaas criar assinatura: {e}")
    return None


def asaas_criar_cobranca(customer_id: str, valor: float, descricao: str, vencimento: str = None):
    """Retorna (charge_id, invoice_url, erro_str). charge_id é None em caso de falha."""
    if not os.getenv("ASAAS_KEY"):
        return None, None, "ASAAS_KEY não configurada"
    if not customer_id:
        return None, None, "customer_id ausente"
    if valor <= 0:
        return None, None, f"valor inválido: {valor}"
    payload = {
        "customer": customer_id,
        "billingType": "PIX",
        "value": round(valor, 2),
        "dueDate": vencimento or str(date.today()),
        "description": descricao,
    }
    try:
        r = requests.post(_asaas_url("/payments"), json=payload, headers=_asaas_headers(), timeout=30)
        if r.ok:
            data = r.json()
            return data.get("id"), data.get("invoiceUrl"), None
        erro = f"HTTP {r.status_code}: {r.text[:300]}"
        logger.warning(f"Asaas criar cobrança falhou — {erro}")
        return None, None, erro
    except Exception as e:
        logger.error(f"Asaas criar cobrança: {e}")
        return None, None, str(e)


def asaas_listar_cobrancas(customer_id: str) -> list:
    if not os.getenv("ASAAS_KEY") or not customer_id:
        return []
    try:
        r = requests.get(_asaas_url(f"/payments?customer={customer_id}&limit=20"), headers=_asaas_headers(), timeout=30)
        if r.ok:
            return r.json().get("data", [])
    except Exception as e:
        logger.error(f"Asaas listar cobranças: {e}")
    return []


# ---------------------------------------------------------------------------
# Bulas
# ---------------------------------------------------------------------------

_BULAS_PATH = os.path.join(os.path.dirname(__file__), "bulas.json")


def _carregar_bulas() -> list[dict]:
    with open(_BULAS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _salvar_bulas(bulas: list[dict]):
    with open(_BULAS_PATH, "w", encoding="utf-8") as f:
        json.dump(bulas, f, ensure_ascii=False, indent=2)


BULAS: list[dict] = _carregar_bulas()


def buscar_bula_local(nome: str) -> Optional[dict]:
    termo = nome.lower().strip()
    for b in BULAS:
        nomes = [n.lower() for n in b.get("nomes", [])]
        pa = b.get("principio_ativo", "").lower()
        sintomas = [s.lower() for s in b.get("sintomas", [])]
        if (any(termo in n for n in nomes) or
                any(n in termo for n in nomes) or
                termo in pa or
                any(termo in s for s in sintomas)):
            return b
    return None


# ---------------------------------------------------------------------------
# PIN / Auth helpers
# ---------------------------------------------------------------------------

def hash_pin(pin: str, telefone: str) -> str:
    return hashlib.sha256(f"{pin}:{telefone}".encode()).hexdigest()


def gerar_codigo_recuperacao() -> str:
    return str(secrets.randbelow(900000) + 100000)


# ---------------------------------------------------------------------------
# Input sanitisation / validation
# ---------------------------------------------------------------------------

def sanitizar(texto: str) -> str:
    return re.sub(r"[<>\"'%;()&+]", "", texto).strip()[:200]


def validar_telefone(tel: str) -> str:
    digitos = re.sub(r"\D", "", tel)
    if not (10 <= len(digitos) <= 11):
        raise HTTPException(status_code=400, detail="Telefone inválido. Informe DDD + número (10 ou 11 dígitos).")
    return digitos


def validar_email(email: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email))


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _enviar_via_brevo(para: str, assunto: str, corpo_html: str, api_key: str):
    nome_env = os.getenv("BREVO_FROM_NAME", "DoseMed")
    email_env = os.getenv("BREVO_FROM_EMAIL", os.getenv("SMTP_USER", ""))
    if not email_env:
        return False, "BREVO_FROM_EMAIL não configurado"
    try:
        r = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json={
                "sender": {"name": nome_env, "email": email_env},
                "to": [{"email": para}],
                "subject": assunto,
                "htmlContent": corpo_html,
            },
            timeout=15,
        )
        if r.ok:
            logger.info(f"E-mail (Brevo) enviado para {para}: {assunto}")
            return True, ""
        return False, f"Brevo HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        logger.error(f"Brevo erro: {e}")
        return False, str(e)


def _enviar_via_resend(para: str, assunto: str, corpo_html: str, api_key: str):
    remetente = os.getenv("RESEND_FROM", os.getenv("SMTP_FROM", "DoseMed <noreply@dosemed.com.br>"))
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": remetente, "to": [para], "subject": assunto, "html": corpo_html},
            timeout=15,
        )
        if r.ok:
            logger.info(f"E-mail (Resend) enviado para {para}: {assunto}")
            return True, ""
        return False, f"Resend HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        logger.error(f"Resend erro: {e}")
        return False, str(e)


def _enviar_via_smtp(para: str, assunto: str, corpo_html: str):
    import socket, ssl as _ssl
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587").strip() or "587")
    user = os.getenv("SMTP_USER", "").strip()
    senha = os.getenv("SMTP_PASSWORD", "").replace(" ", "")
    remetente = os.getenv("SMTP_FROM", f"DoseMed <{user}>")

    _placeholder = {"seuemail@gmail.com", "suasenhadoapp"}
    if not host or not user or not senha or user in _placeholder or senha in _placeholder:
        return False, f"SMTP não configurado (host={host!r} user={user!r})"

    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        host_ip = infos[0][4][0]
    except Exception as e:
        return False, f"DNS falhou para {host}: {e}"

    try:
        mensagem = MIMEMultipart("alternative")
        mensagem["Subject"] = assunto
        mensagem["From"] = remetente
        mensagem["To"] = para
        mensagem.attach(MIMEText(corpo_html, "html", "utf-8"))

        ctx = _ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host_ip, port, context=ctx, timeout=15) as smtp:
                smtp.login(user, senha)
                smtp.sendmail(user, para, mensagem.as_string())
        else:
            with smtplib.SMTP(host_ip, port, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ctx)
                smtp.ehlo()
                smtp.login(user, senha)
                smtp.sendmail(user, para, mensagem.as_string())

        logger.info(f"E-mail (SMTP) enviado para {para}: {assunto}")
        return True, ""
    except Exception as e:
        logger.error(f"SMTP erro: {e}")
        return False, str(e)


def enviar_email(para: str, assunto: str, corpo_html: str):
    """Retorna (ok: bool, erro: str). Prioridade: Brevo → Resend → SMTP."""
    brevo_key = os.getenv("BREVO_API_KEY", "").strip()
    if brevo_key:
        return _enviar_via_brevo(para, assunto, corpo_html, brevo_key)
    resend_key = os.getenv("RESEND_API_KEY", "").strip()
    if resend_key:
        return _enviar_via_resend(para, assunto, corpo_html, resend_key)
    return _enviar_via_smtp(para, assunto, corpo_html)


# ---------------------------------------------------------------------------
# HTML email templates
# ---------------------------------------------------------------------------

def html_recuperacao(nome: str, codigo: str) -> str:
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:24px">
      <h2 style="color:#2563eb">💊 DoseMed</h2>
      <p>Olá, <strong>{nome}</strong>!</p>
      <p>Seu código para redefinir o PIN é:</p>
      <div style="font-size:36px;font-weight:900;letter-spacing:8px;color:#1e40af;
                  background:#eff6ff;border-radius:12px;padding:20px;text-align:center;margin:20px 0">
        {codigo}
      </div>
      <p style="color:#9ca3af;font-size:13px">Válido por 15 minutos. Se não foi você, ignore este e-mail.</p>
      <hr style="border:none;border-top:1px solid #f3f4f6;margin:20px 0">
      <p style="color:#d1d5db;font-size:11px">DoseMed — controle de medicamentos em casa</p>
    </div>"""


def html_vencimento(nome: str, itens: list[dict]) -> str:
    linhas = ""
    for i in itens:
        cor = "#ef4444" if i["status"] == "vencido" else "#f59e0b"
        status_txt = "VENCIDO" if i["status"] == "vencido" else f"Vence em {i['data_vencimento']}"
        linhas += f"""
        <tr>
          <td style="padding:10px 8px;border-bottom:1px solid #f3f4f6">
            <strong>{i['nome']}</strong>
            {'<br><span style="font-size:12px;color:#9ca3af">' + i['principio_ativo'] + '</span>' if i['principio_ativo'] else ''}
          </td>
          <td style="padding:10px 8px;border-bottom:1px solid #f3f4f6;color:{cor};font-weight:600;white-space:nowrap">
            {status_txt}
          </td>
        </tr>"""
    hoje = date.today().strftime("%d/%m/%Y")
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;padding:24px">
      <h2 style="color:#2563eb">💊 DoseMed — Alerta de Vencimento</h2>
      <p>Olá, <strong>{nome}</strong>! Confira os medicamentos que precisam de atenção:</p>
      <table style="width:100%;border-collapse:collapse;margin:16px 0">
        <thead>
          <tr style="background:#f3f4f6">
            <th style="padding:8px;text-align:left;font-size:12px;text-transform:uppercase">Medicamento</th>
            <th style="padding:8px;text-align:left;font-size:12px;text-transform:uppercase">Status</th>
          </tr>
        </thead>
        <tbody>{linhas}</tbody>
      </table>
      <p style="color:#9ca3af;font-size:13px">Acesse o DoseMed para gerenciar seu estoque.</p>
      <hr style="border:none;border-top:1px solid #f3f4f6;margin:20px 0">
      <p style="color:#d1d5db;font-size:11px">Gerado em {hoje} · DoseMed — controle de medicamentos em casa · Este e-mail é informativo e não substitui orientação médica.</p>
    </div>"""


def html_orcamento_farmacia(farmacia_nome: str, bairro: str, medicamento: str, pa: str, mg: str, manipulado: bool, token: str) -> str:
    link = f"https://dosemed.onrender.com/orcamento/{token}"
    tipo = "MANIPULADO" if manipulado else "Medicamento"
    return f"""<div style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px">
    <p style="color:#16a34a;font-weight:bold;font-size:18px">DoseMed — Solicitação de Orçamento</p>
    <p>Olá, <strong>{farmacia_nome}</strong>!</p>
    <p>Um paciente do bairro <strong>{bairro}</strong> precisa renovar o seguinte medicamento e está pedindo orçamento:</p>
    <div style="background:#f0fdf4;border-left:4px solid #16a34a;padding:16px;border-radius:8px;margin:16px 0">
      <p style="margin:0;font-size:15px"><strong>{tipo}: {medicamento}</strong></p>
      {"<p style='margin:4px 0;color:#555'>Princípio ativo: "+pa+"</p>" if pa else ""}
      {"<p style='margin:4px 0;color:#555'>Dosagem: "+mg+"</p>" if mg else ""}
    </div>
    <p>Clique abaixo para enviar sua proposta. O paciente receberá os orçamentos e escolherá a farmácia:</p>
    <a href="{link}" style="display:inline-block;background:#16a34a;color:white;font-weight:bold;padding:12px 28px;border-radius:8px;text-decoration:none;margin:8px 0;font-size:15px">
      Enviar Orçamento
    </a>
    <p style="color:#9ca3af;font-size:11px;margin-top:16px">DoseMed · Plataforma de controle de medicamentos em casa</p>
    </div>"""


def html_orcamento_ganhou(farmacia_nome: str, medicamento: str, bairro: str, preco: float, prazo: str, formas: str) -> str:
    return f"""<div style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px">
    <p style="color:#16a34a;font-weight:bold;font-size:20px">Parabéns, {farmacia_nome}!</p>
    <p>O paciente do bairro <strong>{bairro}</strong> escolheu a sua farmácia:</p>
    <div style="background:#f0fdf4;border-left:4px solid #16a34a;padding:16px;border-radius:8px;margin:16px 0">
      <p style="margin:0;font-size:15px"><strong>{medicamento}</strong></p>
      <p style="margin:4px 0;color:#555">Preço acordado: <strong>R$ {preco:.2f}</strong></p>
      <p style="margin:4px 0;color:#555">Prazo: {prazo}</p>
      <p style="margin:4px 0;color:#555">Pagamento: {formas}</p>
    </div>
    <p>Entre em contato com o paciente para combinar a entrega ou retirada. Boas vendas!</p>
    <p style="color:#9ca3af;font-size:11px">DoseMed · Plataforma de controle de medicamentos</p>
    </div>"""


def html_interesse_promocao(farmacia_nome: str, usuario_nome: str, bairro: str, telefone: str, produto: str, preco: float = None) -> str:
    preco_str = f" · R$ {preco:.2f}" if preco else ""
    return f"""<div style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px">
    <h2 style="color:#2563eb">💊 DoseMed — Interesse em Promoção</h2>
    <p>Olá, <strong>{farmacia_nome}</strong>!</p>
    <p>Um paciente demonstrou interesse na sua promoção de <strong>{produto}</strong>{preco_str}:</p>
    <div style="background:#f0fdf4;border-left:4px solid #16a34a;padding:16px;border-radius:8px;margin:16px 0">
      <p style="margin:0 0 6px"><strong>Nome:</strong> {usuario_nome}</p>
      <p style="margin:0 0 6px"><strong>Bairro:</strong> {bairro}</p>
      <p style="margin:0"><strong>WhatsApp:</strong> +55 {telefone}</p>
    </div>
    <p>Entre em contato diretamente com o paciente para concluir a venda. Boas vendas!</p>
    <p style="color:#9ca3af;font-size:11px">DoseMed · Plataforma de controle de medicamentos em casa</p>
    </div>"""


def html_orcamento_perdeu(farmacia_nome: str, medicamento: str) -> str:
    return f"""<div style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px">
    <p style="color:#6b7280;font-weight:bold;font-size:18px">DoseMed — Resultado do Orçamento</p>
    <p>Olá, <strong>{farmacia_nome}</strong>.</p>
    <p>O paciente escolheu outra farmácia para o fornecimento de <strong>{medicamento}</strong> desta vez.</p>
    <p style="color:#555">Continue participando dos orçamentos para aumentar suas chances. Obrigado pela participação!</p>
    <p style="color:#9ca3af;font-size:11px">DoseMed · Plataforma de controle de medicamentos</p>
    </div>"""


def html_lead_farmacia(farmacia_nome: str, bairro: str, medicamento: str, pa: str, mg: str, manipulado: bool) -> str:
    tipo = "💊 MANIPULADO" if manipulado else "Medicamento"
    return f"""<div style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px">
    <p style="color:#2563eb;font-weight:bold;font-size:18px">DoseMed — Novo Lead</p>
    <p>Olá, <strong>{farmacia_nome}</strong>!</p>
    <p>Um paciente do bairro <strong>{bairro}</strong> precisará renovar o seguinte medicamento em breve:</p>
    <div style="background:#f0f9ff;border-left:4px solid #2563eb;padding:16px;border-radius:8px;margin:16px 0">
      <p style="margin:0;font-size:15px"><strong>{tipo}: {medicamento}</strong></p>
      {"<p style='margin:4px 0;color:#555'>Princípio ativo: "+pa+"</p>" if pa else ""}
      {"<p style='margin:4px 0;color:#555'>Dosagem: "+mg+"</p>" if mg else ""}
    </div>
    <p style="color:#555;font-size:13px">Este lead foi gerado automaticamente pelo DoseMed. O paciente <strong>não foi identificado</strong> — apenas o bairro e o medicamento são compartilhados.</p>
    <p style="color:#9ca3af;font-size:11px">DoseMed · Plataforma de controle de medicamentos em casa</p>
    </div>"""


def html_relatorio_farmacia(farmacia_nome: str, semana: str, buscas_med: list[dict],
                            buscas_sint: list[dict], por_sexo: dict, por_faixa: dict,
                            por_bairro: dict) -> str:
    def _linhas_tabela(itens: list[dict], col_titulo: str) -> str:
        if not itens:
            return "<tr><td colspan='2' style='padding:10px;color:#9ca3af;font-style:italic'>Sem dados esta semana</td></tr>"
        html = ""
        for row in itens[:10]:
            html += f"<tr><td style='padding:8px 4px;border-bottom:1px solid #f3f4f6'>{row['termo']}</td><td style='padding:8px 4px;border-bottom:1px solid #f3f4f6;text-align:right;font-weight:600'>{row['total']}</td></tr>"
        return html

    def _bar(label: str, valor: int, total: int, cor: str) -> str:
        pct = round(valor / total * 100) if total else 0
        return f"""<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:13px"><span>{label}</span><span>{valor} ({pct}%)</span></div><div style="background:#f3f4f6;border-radius:4px;height:8px;margin-top:3px"><div style="background:{cor};border-radius:4px;height:8px;width:{pct}%"></div></div></div>"""

    total_sexo = sum(por_sexo.values()) or 1
    total_faixa = sum(por_faixa.values()) or 1
    sexo_html = _bar("Feminino", por_sexo.get("F", 0), total_sexo, "#ec4899") + _bar("Masculino", por_sexo.get("M", 0), total_sexo, "#3b82f6") + _bar("Não informado", por_sexo.get("?", 0), total_sexo, "#9ca3af")
    faixa_html = "".join(_bar(f, por_faixa.get(f, 0), total_faixa, c) for f, c in [("<18", "#a78bfa"), ("18-35", "#3b82f6"), ("36-55", "#10b981"), ("56+", "#f59e0b"), ("?", "#9ca3af")])
    bairros_html = "".join(f"<div style='display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #f9fafb;font-size:13px'><span>{b}</span><span style='font-weight:600'>{n}</span></div>" for b, n in sorted(por_bairro.items(), key=lambda x: -x[1])[:8])
    if not bairros_html:
        bairros_html = "<p style='color:#9ca3af;font-style:italic;font-size:13px'>Sem dados</p>"

    return f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:0;background:#f9fafb">
  <div style="background:#2563eb;padding:28px 24px;text-align:center">
    <h1 style="color:#fff;margin:0;font-size:22px">💊 DoseMed — Relatório Semanal</h1>
    <p style="color:#bfdbfe;margin:6px 0 0;font-size:14px">{farmacia_nome} · Semana de {semana}</p>
  </div>
  <div style="padding:24px">
    <p style="color:#374151">Olá, <strong>{farmacia_nome}</strong>! Aqui está o resumo das buscas na sua região esta semana.</p>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:20px 0">
      <div style="background:#fff;border-radius:10px;padding:16px;border:1px solid #e5e7eb">
        <p style="font-weight:700;color:#1e40af;margin:0 0 12px;font-size:13px;text-transform:uppercase">Medicamentos mais buscados</p>
        <table style="width:100%;border-collapse:collapse">
          <thead><tr><th style="text-align:left;font-size:11px;color:#9ca3af;padding:4px">Medicamento</th><th style="text-align:right;font-size:11px;color:#9ca3af;padding:4px">Buscas</th></tr></thead>
          <tbody>{_linhas_tabela(buscas_med, "Medicamento")}</tbody>
        </table>
      </div>
      <div style="background:#fff;border-radius:10px;padding:16px;border:1px solid #e5e7eb">
        <p style="font-weight:700;color:#1e40af;margin:0 0 12px;font-size:13px;text-transform:uppercase">Sintomas mais buscados</p>
        <table style="width:100%;border-collapse:collapse">
          <thead><tr><th style="text-align:left;font-size:11px;color:#9ca3af;padding:4px">Sintoma / Condição</th><th style="text-align:right;font-size:11px;color:#9ca3af;padding:4px">Buscas</th></tr></thead>
          <tbody>{_linhas_tabela(buscas_sint, "Sintoma")}</tbody>
        </table>
      </div>
    </div>

    <div style="background:#fff;border-radius:10px;padding:16px;border:1px solid #e5e7eb;margin-bottom:16px">
      <p style="font-weight:700;color:#1e40af;margin:0 0 12px;font-size:13px;text-transform:uppercase">Perfil dos usuários — sexo</p>
      {sexo_html}
    </div>

    <div style="background:#fff;border-radius:10px;padding:16px;border:1px solid #e5e7eb;margin-bottom:16px">
      <p style="font-weight:700;color:#1e40af;margin:0 0 12px;font-size:13px;text-transform:uppercase">Perfil dos usuários — faixa etária</p>
      {faixa_html}
    </div>

    <div style="background:#fff;border-radius:10px;padding:16px;border:1px solid #e5e7eb;margin-bottom:16px">
      <p style="font-weight:700;color:#1e40af;margin:0 0 12px;font-size:13px;text-transform:uppercase">Bairros com mais buscas</p>
      {bairros_html}
    </div>

    <p style="color:#6b7280;font-size:12px;text-align:center;margin-top:24px">Este relatório é exclusivo para farmácias no plano Pro ou Manipulado do DoseMed.<br>Dados anonimizados — nenhum usuário é identificado individualmente.</p>
    <hr style="border:none;border-top:1px solid #e5e7eb;margin:16px 0">
    <p style="color:#d1d5db;font-size:11px;text-align:center">DoseMed · controle de medicamentos em casa · Este e-mail é informativo.</p>
  </div>
</div>"""


def html_anuncio_confirmacao(farmacia_nome: str, texto: str, total: int, publico: str) -> str:
    publico_str = "todos os bairros cadastrados" if publico == "todos" else "usuários do seu bairro/região"
    return f"""
    <div style="font-family:sans-serif;max-width:560px;margin:auto;background:#fff;border-radius:12px;border:1px solid #e5e7eb;overflow:hidden">
      <div style="background:#2563eb;padding:24px;text-align:center">
        <h1 style="color:#fff;margin:0;font-size:22px">📢 Anúncio disparado!</h1>
      </div>
      <div style="padding:28px">
        <p style="color:#374151">Olá, <strong>{farmacia_nome}</strong>!</p>
        <p style="color:#374151">Seu anúncio foi enviado com sucesso para <strong>{total} usuários</strong> entre {publico_str}.</p>
        <div style="background:#f0f9ff;border-left:4px solid #2563eb;padding:14px 18px;border-radius:6px;margin:20px 0">
          <p style="color:#1e40af;font-style:italic;margin:0">"{texto}"</p>
        </div>
        <p style="color:#6b7280;font-size:13px">Agradecemos por anunciar no DoseMed. Em breve os usuários poderão consultar sua farmácia.</p>
      </div>
    </div>"""


# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------

def ddd_para_estado(telefone: str) -> tuple[str, str]:
    digitos = re.sub(r"\D", "", telefone)
    ddd = int(digitos[:2]) if len(digitos) >= 2 else 0
    estado = DDD_ESTADO.get(ddd, "??")
    return str(ddd), estado


def calcular_preco(nome: str) -> tuple[float, str]:
    nome_lower = nome.lower()
    if any(t in nome_lower for t in ["injetável", "injetavel", "insulina", "especial"]):
        return 50.0, "alto"
    if any(t in nome_lower for t in ["antibiótico", "antibiotico", "xarope", "gotas"]):
        return 25.0, "medio"
    return 10.0, "baixo"


def calcular_status(data_validade: Optional[date]) -> str:
    if data_validade is None:
        return "ok"
    hoje = date.today()
    if data_validade < hoje:
        return "vencido"
    if data_validade <= hoje + timedelta(days=30):
        return "atencao"
    return "ok"


def calcular_doses_por_dia(db: Session, usuario_id: str, nome_med: str) -> Optional[float]:
    """Doses/dia estimadas pelos alarmes ativos do usuário para o medicamento."""
    alarmes = db.query(AlarmeRemedio).filter(
        AlarmeRemedio.usuario_id == usuario_id,
        AlarmeRemedio.nome_med.ilike(f"%{nome_med.split()[0]}%"),
        AlarmeRemedio.ativo == 1
    ).all()
    if not alarmes:
        return None
    total = sum(len([d for d in a.dias.split(",") if d.strip()]) for a in alarmes)
    return round(total / 7, 2)


def atualizar_status_estoque(db: Session, usuario_id: str):
    itens = db.query(Estoque).filter(
        Estoque.usuario_id == usuario_id,
        Estoque.status != "consumido"
    ).all()
    for item in itens:
        item.status = calcular_status(item.data_validade)
    db.commit()


def precos_medios_sistema(db: Session) -> dict:
    """Retorna {nome_normalizado: {media, amostras}} de todos os preco_real no sistema."""
    itens = (
        db.query(Estoque)
        .filter(Estoque.preco_real.isnot(None), Estoque.preco_real > 0)
        .all()
    )
    acum = {}
    for i in itens:
        k = i.nome_medicamento.lower().strip()
        acum.setdefault(k, []).append(i.preco_real)
    return {
        k: {"media": round(sum(v) / len(v), 2), "amostras": len(v)}
        for k, v in acum.items()
    }


def valor_efetivo(item, precos_sistema: Optional[dict] = None) -> float:
    if item.preco_real is not None:
        return item.preco_real
    if precos_sistema:
        k = item.nome_medicamento.lower().strip()
        if k in precos_sistema:
            return precos_sistema[k]["media"]
    return item.preco_estimado or 0.0


def serializar_item(i) -> dict:
    return {
        "id": i.id,
        "nome": i.nome_medicamento,
        "principio_ativo": i.principio_ativo,
        "miligramas": i.miligramas,
        "fabricante": i.fabricante,
        "manipulado": bool(i.manipulado),
        "uso_continuo": bool(i.uso_continuo),
        "iniciado": bool(i.iniciado),
        "quantidade": i.quantidade or 1,
        "preco_real": i.preco_real,
        "preco_estimado": i.preco_estimado,
        "data_vencimento": str(i.data_validade) if i.data_validade else None,
        "data_consumo": str(i.data_consumo.date()) if i.data_consumo else None,
        "categoria": i.categoria_valor,
        "status": i.status
    }


def comprimir_imagem(dados: bytes) -> bytes:
    img = Image.open(io.BytesIO(dados))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > 1600:
        img.thumbnail((1600, 1600), Image.LANCZOS)
    qualidade = 85
    while qualidade >= 40:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=qualidade, optimize=True)
        if buf.tell() <= 900 * 1024:
            break
        qualidade -= 15
    return buf.getvalue()


def sort_por_vencimento(itens: list[dict]) -> list[dict]:
    return sorted(itens, key=lambda x: x["data_vencimento"] or "9999-12-31")


def registrar_busca(db: Session, tipo: str, termo: str, usuario_id: str = None):
    try:
        ddd, estado = ddd_para_estado(usuario_id or "")
        bairro = genero = ano_nasc = None
        # Pseudonimiza o identificador: SHA-256 truncado — não é reversível
        uid_hash = hashlib.sha256(usuario_id.encode()).hexdigest()[:40] if usuario_id else None
        if usuario_id:
            u = db.query(Usuario).filter(Usuario.telefone == usuario_id).first()
            if u:
                bairro = u.bairro
                genero = u.genero if u.genero in ("M", "F") else "?"
                ano_nasc = u.data_nascimento.year if u.data_nascimento else None
        log = LogBusca(tipo=tipo, termo=termo[:100], ddd=ddd, estado=estado,
                       bairro=bairro, usuario_id=uid_hash,
                       genero=genero, ano_nasc=ano_nasc)
        db.add(log)
        db.commit()
    except Exception:
        pass


def get_api_key() -> str:
    key = keyring.get_password("dosemed", "anthropic_api_key") if KEYRING_OK else None
    return key or os.getenv("ANTHROPIC_API_KEY", "")


def gerar_leads_para_item(db: Session, item, usuario, origem: str = "cron"):
    """Gera leads para farmácias elegíveis para um item de estoque."""
    from database import get_config
    from models import Farmacia, Lead

    if not usuario.bairro:
        return 0
    bairro = usuario.bairro.lower().strip()

    farmacias = db.query(Farmacia).filter(Farmacia.ativo == 1).all()
    enviados = 0
    for f in farmacias:
        bairros_f = [b.lower().strip() for b in (f.bairros or "").split(",") if b.strip()]
        if bairros_f and bairro not in bairros_f:
            continue
        if item.manipulado and not f.atende_manipulado:
            continue

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
        dup = db.query(Lead).filter(
            Lead.farmacia_id == f.id,
            Lead.medicamento == item.nome_medicamento,
            Lead.criado_em >= cutoff
        ).first()
        if dup:
            continue

        preco_chave = "preco_lead_manipulado" if item.manipulado else "preco_lead_comum"
        preco = float(get_config(db, preco_chave, "5.00"))

        lead = Lead(
            farmacia_id=f.id,
            usuario_bairro=usuario.bairro,
            medicamento=item.nome_medicamento,
            principio_ativo=item.principio_ativo,
            miligramas=item.miligramas,
            manipulado=item.manipulado or 0,
            origem=origem,
            status="enviado",
            preco_cobrado=preco,
        )
        db.add(lead)
        db.flush()

        if f.email:
            html = html_lead_farmacia(f.nome, usuario.bairro, item.nome_medicamento,
                                      item.principio_ativo or "", item.miligramas or "",
                                      bool(item.manipulado))
            ok_lead, err_lead = enviar_email(f.email, f"DoseMed — Lead: {item.nome_medicamento} ({usuario.bairro})", html)
            if not ok_lead:
                logger.warning(f"Lead email falhou para {f.nome}: {err_lead}")

        enviados += 1

    db.commit()
    return enviados


def criar_orcamento_auto(db, item, usuario) -> tuple:
    """Cria OrcamentoSolicitacao automaticamente para item de uso contínuo com estoque baixo.
    Prioriza a farmácia favorita (última que venceu para este medicamento).
    Retorna (solicitacao_id, enviados) ou (None, 0) se já existe solicitação ativa."""
    from database import get_config
    from models import Farmacia, Lead, OrcamentoResposta, OrcamentoSolicitacao

    # Não cria se já existe solicitação ativa para este medicamento
    ativa = db.query(OrcamentoSolicitacao).filter(
        OrcamentoSolicitacao.usuario_id == usuario.telefone,
        OrcamentoSolicitacao.nome_med == item.nome_medicamento,
        OrcamentoSolicitacao.status.in_(["coletando", "aguardando_usuario"])
    ).first()
    if ativa:
        return None, 0

    # Farmácia favorita = última que ganhou para este usuário + medicamento
    favorita_id = None
    ultima = (
        db.query(OrcamentoResposta)
        .join(OrcamentoResposta.solicitacao)
        .filter(
            OrcamentoSolicitacao.usuario_id == usuario.telefone,
            OrcamentoSolicitacao.nome_med == item.nome_medicamento,
            OrcamentoResposta.status == "ganhou",
        )
        .order_by(OrcamentoResposta.id.desc())
        .first()
    )
    if ultima:
        favorita_id = ultima.farmacia_id

    minutos = int(get_config(db, "minutos_disputa_lead", "30"))
    expira = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=minutos)
    sol = OrcamentoSolicitacao(
        usuario_id=usuario.telefone,
        nome_med=item.nome_medicamento,
        quantidade_restante=item.quantidade,
        status="coletando",
        expira_em=expira,
        origem="auto",
        favorita_farmacia_id=favorita_id,
    )
    db.add(sol)
    db.flush()

    max_farm = int(get_config(db, "max_farmacias_orcamento", "4"))
    preco_chave = "preco_lead_manipulado" if item.manipulado else "preco_lead_comum"
    preco_lead = float(get_config(db, preco_chave, "5.00"))

    def _adicionar(f):
        token = secrets.token_urlsafe(16)
        db.add(OrcamentoResposta(
            solicitacao_id=sol.id, farmacia_id=f.id, token=token, status="pendente",
        ))
        db.add(Lead(
            farmacia_id=f.id,
            usuario_bairro=usuario.bairro if usuario else None,
            medicamento=item.nome_medicamento,
            principio_ativo=item.principio_ativo,
            miligramas=item.miligramas,
            manipulado=item.manipulado or 0,
            origem="auto_reposicao",
            status="enviado",
            preco_cobrado=preco_lead,
        ))
        if f.email:
            html = html_orcamento_farmacia(
                f.nome, usuario.bairro if usuario else "—",
                item.nome_medicamento, item.principio_ativo or "",
                item.miligramas or "", bool(item.manipulado), token,
            )
            enviar_email(f.email, f"DoseMed — Reposição automática: {item.nome_medicamento}", html)

    enviados = 0
    ids_adicionados: set = set()

    # Favorita primeiro
    if favorita_id:
        fav = db.query(Farmacia).filter(Farmacia.id == favorita_id, Farmacia.ativo == 1).first()
        if fav and not (item.manipulado and not fav.atende_manipulado):
            _adicionar(fav)
            ids_adicionados.add(fav.id)
            enviados += 1

    # Demais farmácias elegíveis até max_farm
    farmacias = db.query(Farmacia).filter(Farmacia.ativo == 1).all()
    for f in farmacias:
        if enviados >= max_farm:
            break
        if f.id in ids_adicionados:
            continue
        if usuario and usuario.bairro:
            bairros_f = [b.lower().strip() for b in (f.bairros or "").split(",") if b.strip()]
            if bairros_f and usuario.bairro.lower().strip() not in bairros_f:
                continue
        if item.manipulado and not f.atende_manipulado:
            continue
        _adicionar(f)
        ids_adicionados.add(f.id)
        enviados += 1

    db.commit()
    return sol.id, enviados
