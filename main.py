import re
import os
import io
import json
import base64
import hashlib
import random
import smtplib
import logging
import requests
try:
    import anthropic
    ANTHROPIC_OK = True
except ImportError:
    ANTHROPIC_OK = False
try:
    import keyring
    KEYRING_OK = True
except ImportError:
    KEYRING_OK = False
from datetime import date, timedelta, datetime
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    SCHEDULER_OK = True
except ImportError:
    SCHEDULER_OK = False
try:
    from pywebpush import webpush, WebPushException
    PUSH_OK = True
except ImportError:
    PUSH_OK = False
from typing import Optional
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.exception_handlers import http_exception_handler
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from PIL import Image
from dotenv import load_dotenv

load_dotenv()
from models import Base, Usuario, Estoque, Pedido, CodigoRecuperacao, LogBusca, Farmacia, Lead, Configuracao, PushSub, AlarmeRemedio

# --- Logging ---
_log_handlers = [logging.StreamHandler()]
try:
    _log_handlers.append(logging.FileHandler("dosemed.log", encoding="utf-8"))
except Exception:
    pass
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=_log_handlers
)
logger = logging.getLogger("dosemed")

# --- Config ---
ADMIN_PHONE = "65993125115"

DDD_ESTADO = {
    11: "SP", 12: "SP", 13: "SP", 14: "SP", 15: "SP", 16: "SP", 17: "SP", 18: "SP", 19: "SP",
    21: "RJ", 22: "RJ", 24: "RJ",
    27: "ES", 28: "ES",
    31: "MG", 32: "MG", 33: "MG", 34: "MG", 35: "MG", 37: "MG", 38: "MG",
    41: "PR", 42: "PR", 43: "PR", 44: "PR", 45: "PR", 46: "PR",
    47: "SC", 48: "SC", 49: "SC",
    51: "RS", 53: "RS", 54: "RS", 55: "RS",
    61: "DF", 62: "GO", 64: "GO",
    63: "TO", 65: "MT", 66: "MT", 67: "MS", 68: "AC", 69: "RO",
    71: "BA", 73: "BA", 74: "BA", 75: "BA", 77: "BA",
    79: "SE", 81: "PE", 87: "PE", 82: "AL", 83: "PB", 84: "RN",
    85: "CE", 88: "CE", 86: "PI", 89: "PI",
    91: "PA", 93: "PA", 94: "PA", 92: "AM", 97: "AM",
    95: "RR", 96: "AP", 98: "MA", 99: "MA"
}

# --- Banco ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dosemed.db")
# Railway fornece postgres://, SQLAlchemy precisa de postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)

# Migrações seguras (add-only)
_migracoes = {
    "usuarios": ["pin TEXT", "email TEXT", "bairro TEXT", "aceite_lgpd DATETIME"],
    "estoque": ["iniciado INTEGER DEFAULT 0", "data_consumo DATETIME"],
    "log_buscas": ["bairro TEXT"],
    "leads": ["asaas_charge_id TEXT"],
    "alarmes": [],
    "push_subs": [],
}
with engine.connect() as conn:
    for tabela, colunas in _migracoes.items():
        for col_def in colunas:
            try:
                conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {col_def}"))
                conn.commit()
            except Exception:
                pass

# Seed de configurações padrão (só insere se ainda não existir)
_CONFIG_DEFAULT = {
    "preco_lead_comum":      ("5.00",  "Preço por lead comum enviado à farmácia (R$)"),
    "preco_lead_manipulado": ("15.00", "Preço por lead de manipulado enviado à farmácia (R$)"),
    "preco_plano_basico":    ("299.00","Assinatura mensal plano básico (R$/mês)"),
    "preco_plano_pro":       ("499.00","Assinatura mensal plano pro (R$/mês)"),
    "preco_plano_manipulado":("699.00","Assinatura mensal plano manipulação (R$/mês)"),
    "dias_aviso_vencimento": ("30",    "Dias antes do vencimento para gerar lead"),
    "asaas_env":             ("sandbox","Ambiente Asaas: sandbox | production"),
}
with SessionLocal() as _sess:
    for chave, (valor, descricao) in _CONFIG_DEFAULT.items():
        if not _sess.query(Configuracao).filter(Configuracao.chave == chave).first():
            _sess.add(Configuracao(chave=chave, valor=valor, descricao=descricao))
    _sess.commit()


def get_config(db: Session, chave: str, default: str = "") -> str:
    c = db.query(Configuracao).filter(Configuracao.chave == chave).first()
    return c.valor if c else default


# --- VAPID / Web Push ---

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
        logger.warning(f"Push falhou para {sub.usuario_id}: {e}")
        return False


# --- Asaas ---
def _asaas_headers() -> dict:
    return {"access_token": os.getenv("ASAAS_KEY", ""), "Content-Type": "application/json"}


def _asaas_url(path: str) -> str:
    env = os.getenv("ASAAS_ENV", "sandbox")
    base = "https://api.asaas.com/v3" if env == "production" else "https://sandbox.asaas.com/api/v3"
    return f"{base}{path}"


def asaas_criar_cliente(nome: str, email: str, cnpj: str = None) -> str | None:
    if not os.getenv("ASAAS_KEY"):
        return None
    if not cnpj:
        logger.warning(f"Asaas: CNPJ não informado para '{nome}' — cliente não criado (exigido para cobranças PIX)")
        return None
    payload = {"name": nome, "email": email, "cpfCnpj": re.sub(r"\D", "", cnpj)}
    try:
        r = requests.post(_asaas_url("/customers"), json=payload, headers=_asaas_headers(), timeout=10)
        if r.ok:
            return r.json().get("id")
        logger.warning(f"Asaas criar cliente falhou ({r.status_code}): {r.text[:200]}")
    except Exception as e:
        logger.error(f"Asaas criar cliente: {e}")
    return None


def asaas_criar_assinatura(customer_id: str, valor: float, descricao: str) -> str | None:
    if not os.getenv("ASAAS_KEY") or not customer_id:
        return None
    from datetime import date
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
        r = requests.post(_asaas_url("/subscriptions"), json=payload, headers=_asaas_headers(), timeout=10)
        if r.ok:
            return r.json().get("id")
    except Exception as e:
        logger.error(f"Asaas criar assinatura: {e}")
    return None


def asaas_criar_cobranca(customer_id: str, valor: float, descricao: str, vencimento: str = None) -> str | None:
    if not os.getenv("ASAAS_KEY") or not customer_id or valor <= 0:
        return None
    from datetime import date
    payload = {
        "customer": customer_id,
        "billingType": "PIX",
        "value": round(valor, 2),
        "dueDate": vencimento or str(date.today()),
        "description": descricao,
    }
    try:
        r = requests.post(_asaas_url("/payments"), json=payload, headers=_asaas_headers(), timeout=10)
        if r.ok:
            return r.json().get("id")
    except Exception as e:
        logger.error(f"Asaas criar cobrança: {e}")
    return None


def asaas_listar_cobrancas(customer_id: str) -> list:
    if not os.getenv("ASAAS_KEY") or not customer_id:
        return []
    try:
        r = requests.get(_asaas_url(f"/payments?customer={customer_id}&limit=20"), headers=_asaas_headers(), timeout=10)
        if r.ok:
            return r.json().get("data", [])
    except Exception as e:
        logger.error(f"Asaas listar cobranças: {e}")
    return []


# --- Rate limiter ---
limiter = Limiter(key_func=get_remote_address, default_limits=[])
app = FastAPI(title="DoseMed API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- Bulas ---
_BULAS_PATH = os.path.join(os.path.dirname(__file__), "bulas.json")


def _carregar_bulas() -> list[dict]:
    with open(_BULAS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _salvar_bulas(bulas: list[dict]):
    with open(_BULAS_PATH, "w", encoding="utf-8") as f:
        json.dump(bulas, f, ensure_ascii=False, indent=2)


BULAS: list[dict] = _carregar_bulas()


# --- Helpers ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_api_key() -> str:
    key = keyring.get_password("dosemed", "anthropic_api_key") if KEYRING_OK else None
    return key or os.getenv("ANTHROPIC_API_KEY", "")


def sanitizar(texto: str) -> str:
    return re.sub(r"[<>\"'%;()&+]", "", texto).strip()[:200]


def validar_telefone(tel: str) -> str:
    digitos = re.sub(r"\D", "", tel)
    if not (10 <= len(digitos) <= 11):
        raise HTTPException(status_code=400, detail="Telefone inválido. Informe DDD + número (10 ou 11 dígitos).")
    return digitos


def hash_pin(pin: str, telefone: str) -> str:
    return hashlib.sha256(f"{pin}:{telefone}".encode()).hexdigest()


def validar_email(email: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email))


def gerar_codigo_recuperacao() -> str:
    return str(random.randint(100000, 999999))


def enviar_email(para: str, assunto: str, corpo_html: str) -> bool:
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    senha = os.getenv("SMTP_PASSWORD", "")
    remetente = os.getenv("SMTP_FROM", f"DoseMed <{user}>")

    _placeholder = {"seuemail@gmail.com", "suasenhadoapp"}
    if not host or not user or not senha or user in _placeholder or senha in _placeholder:
        logger.warning("SMTP não configurado — e-mail não enviado")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = remetente
        msg["To"] = para
        msg.attach(MIMEText(corpo_html, "html", "utf-8"))

        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(user, senha)
            smtp.sendmail(user, para, msg.as_string())
        logger.info(f"E-mail enviado para {para}: {assunto}")
        return True
    except Exception as e:
        logger.error(f"Erro ao enviar e-mail para {para}: {e}")
        return False


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


def atualizar_status_estoque(db: Session, usuario_id: str):
    itens = db.query(Estoque).filter(
        Estoque.usuario_id == usuario_id,
        Estoque.status != "consumido"
    ).all()
    for item in itens:
        item.status = calcular_status(item.data_validade)
    db.commit()


def valor_efetivo(item) -> float:
    if item.preco_real is not None:
        return item.preco_real
    return item.preco_estimado


def serializar_item(i) -> dict:
    return {
        "id": i.id,
        "nome": i.nome_medicamento,
        "principio_ativo": i.principio_ativo,
        "miligramas": i.miligramas,
        "fabricante": i.fabricante,
        "manipulado": bool(i.manipulado),
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
        bairro = None
        if usuario_id:
            u = db.query(Usuario).filter(Usuario.telefone == usuario_id).first()
            if u:
                bairro = u.bairro
        log = LogBusca(tipo=tipo, termo=termo[:100], ddd=ddd, estado=estado, bairro=bairro, usuario_id=usuario_id)
        db.add(log)
        db.commit()
    except Exception:
        pass


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


# --- Schemas ---

class UsuarioPayload(BaseModel):
    telefone: str
    nome: str
    email: Optional[str] = None
    bairro: Optional[str] = None
    endereco_completo: Optional[str] = None
    instrucoes_portaria: Optional[str] = None
    aceite_lgpd: Optional[bool] = False


class WebhookPayload(BaseModel):
    telefone: str
    medicamento: str
    principio_ativo: Optional[str] = None
    miligramas: Optional[str] = None
    fabricante: Optional[str] = None
    validade: Optional[str] = None
    quantidade: Optional[int] = 1
    manipulado: Optional[bool] = False
    preco_real: Optional[float] = None
    nome_usuario: Optional[str] = None


class EditarUsuarioPayload(BaseModel):
    nome: Optional[str] = None
    email: Optional[str] = None
    bairro: Optional[str] = None
    endereco_completo: Optional[str] = None
    instrucoes_portaria: Optional[str] = None


class RecuperarPinPayload(BaseModel):
    telefone: str


class VerificarCodigoPayload(BaseModel):
    telefone: str
    codigo: str
    novo_pin: str


class EditarItemPayload(BaseModel):
    nome_medicamento: Optional[str] = None
    principio_ativo: Optional[str] = None
    miligramas: Optional[str] = None
    fabricante: Optional[str] = None
    validade: Optional[str] = None
    quantidade: Optional[int] = None
    manipulado: Optional[bool] = None
    preco_real: Optional[float] = None


class PinPayload(BaseModel):
    telefone: str
    pin: str


class LogBuscaPayload(BaseModel):
    tipo: str
    termo: str
    usuario_id: Optional[str] = None


class EnriquecerPayload(BaseModel):
    nome: str
    principio_ativo: Optional[str] = None
    fabricante: Optional[str] = None
    usuario_id: Optional[str] = None


class ConfirmarChegadaPayload(BaseModel):
    pedido_id: int
    validade: Optional[str] = None


class PushSubPayload(BaseModel):
    telefone: str
    endpoint: str
    p256dh: str
    auth: str


class AlarmePayload(BaseModel):
    telefone: str
    nome_med: str
    horario: str          # "HH:MM"
    dias: Optional[str] = "1,2,3,4,5,6,7"
    ativo: Optional[int] = 1


# --- Rotas ---

@app.get("/")
def root():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))

@app.get("/manifest.json")
def manifest():
    return FileResponse(os.path.join(os.path.dirname(__file__), "manifest.json"), media_type="application/manifest+json")

@app.get("/sw.js")
def service_worker():
    return FileResponse(os.path.join(os.path.dirname(__file__), "sw.js"), media_type="application/javascript")

@app.get("/icon-{size}.png")
def icone(size: str):
    path = os.path.join(os.path.dirname(__file__), f"icon-{size}.png")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Ícone não encontrado.")
    return FileResponse(path, media_type="image/png")


@app.get("/usuario/{telefone}")
def get_usuario(telefone: str, db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    return {
        "telefone": usuario.telefone,
        "nome": usuario.nome,
        "email": usuario.email,
        "bairro": usuario.bairro,
        "endereco": usuario.endereco_completo,
        "instrucoes_portaria": usuario.instrucoes_portaria,
        "tem_pin": bool(usuario.pin),
        "tem_email": bool(usuario.email),
        "aceite_lgpd": bool(usuario.aceite_lgpd)
    }


@app.post("/usuario")
def criar_usuario(payload: UsuarioPayload, db: Session = Depends(get_db)):
    telefone = validar_telefone(payload.telefone)
    if db.query(Usuario).filter(Usuario.telefone == telefone).first():
        raise HTTPException(status_code=409, detail="Telefone já cadastrado.")
    email = None
    if payload.email:
        email_val = payload.email.strip().lower()
        if not validar_email(email_val):
            raise HTTPException(status_code=400, detail="E-mail inválido.")
        email = email_val
    usuario = Usuario(
        telefone=telefone,
        nome=sanitizar(payload.nome),
        email=email,
        bairro=sanitizar(payload.bairro or ""),
        endereco_completo=sanitizar(payload.endereco_completo or ""),
        instrucoes_portaria=sanitizar(payload.instrucoes_portaria or ""),
        aceite_lgpd=datetime.utcnow() if payload.aceite_lgpd else None
    )
    db.add(usuario)
    db.commit()
    logger.info(f"Novo usuário: {telefone}")
    return {"mensagem": "Usuário cadastrado com sucesso!", "telefone": telefone}


@app.put("/usuario/{telefone}")
def editar_usuario(telefone: str, payload: EditarUsuarioPayload, db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    if payload.nome is not None:
        usuario.nome = sanitizar(payload.nome)
    if payload.email is not None:
        email_val = payload.email.strip().lower()
        if email_val and not validar_email(email_val):
            raise HTTPException(status_code=400, detail="E-mail inválido.")
        usuario.email = email_val or None
    if payload.bairro is not None:
        usuario.bairro = sanitizar(payload.bairro)
    if payload.endereco_completo is not None:
        usuario.endereco_completo = sanitizar(payload.endereco_completo)
    if payload.instrucoes_portaria is not None:
        usuario.instrucoes_portaria = sanitizar(payload.instrucoes_portaria)
    db.commit()
    return {"mensagem": "Perfil atualizado com sucesso."}


# --- PIN ---

@app.post("/auth/definir-pin")
def definir_pin(payload: PinPayload, db: Session = Depends(get_db)):
    telefone = validar_telefone(payload.telefone)
    pin = re.sub(r"\D", "", payload.pin)
    if not (4 <= len(pin) <= 6):
        raise HTTPException(status_code=400, detail="PIN deve ter 4 a 6 dígitos.")
    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    usuario.pin = hash_pin(pin, telefone)
    db.commit()
    return {"mensagem": "PIN definido com sucesso."}


@app.post("/auth/verificar-pin")
def verificar_pin(payload: PinPayload, db: Session = Depends(get_db)):
    telefone = validar_telefone(payload.telefone)
    pin = re.sub(r"\D", "", payload.pin)
    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    if not usuario.pin:
        return {"ok": True, "mensagem": "Sem PIN definido."}
    if hash_pin(pin, telefone) != usuario.pin:
        raise HTTPException(status_code=401, detail="PIN incorreto.")
    return {"ok": True, "mensagem": "PIN verificado."}


@app.post("/auth/recuperar-pin")
def recuperar_pin(payload: RecuperarPinPayload, db: Session = Depends(get_db)):
    telefone = validar_telefone(payload.telefone)
    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    if not usuario.email:
        raise HTTPException(status_code=400, detail="Nenhum e-mail cadastrado. Informe seu e-mail no perfil para usar esta função.")

    # Invalida códigos anteriores
    db.query(CodigoRecuperacao).filter(
        CodigoRecuperacao.telefone == telefone,
        CodigoRecuperacao.usado == 0
    ).update({"usado": 1})
    db.commit()

    codigo = gerar_codigo_recuperacao()
    expira = datetime.utcnow() + timedelta(minutes=15)
    db.add(CodigoRecuperacao(telefone=telefone, codigo=codigo, expira_em=expira))
    db.commit()

    enviado = enviar_email(
        usuario.email,
        "DoseMed — Código para redefinir seu PIN",
        html_recuperacao(usuario.nome, codigo)
    )

    if not enviado:
        # Dev mode: credenciais SMTP não configuradas ou são placeholder
        smtp_user = os.getenv("SMTP_USER", "")
        smtp_pass = os.getenv("SMTP_PASSWORD", "")
        smtp_real = smtp_user and smtp_user != "seuemail@gmail.com" and smtp_pass and smtp_pass != "suasenhadoapp"
        if not smtp_real:
            return {"mensagem": f"[DEV] Código: {codigo}", "codigo": codigo, "email_mascarado": usuario.email}
        raise HTTPException(status_code=503, detail="Erro ao enviar e-mail. Tente novamente.")

    partes = usuario.email.split("@")
    mascarado = partes[0][:2] + "***@" + partes[1]
    return {"mensagem": f"Código enviado para {mascarado}. Válido por 15 minutos.", "email_mascarado": mascarado}


@app.post("/auth/verificar-codigo")
def verificar_codigo(payload: VerificarCodigoPayload, db: Session = Depends(get_db)):
    telefone = validar_telefone(payload.telefone)
    pin = re.sub(r"\D", "", payload.novo_pin)
    if len(pin) < 4:
        raise HTTPException(status_code=400, detail="Novo PIN deve ter ao menos 4 dígitos.")

    agora = datetime.utcnow()
    cod = db.query(CodigoRecuperacao).filter(
        CodigoRecuperacao.telefone == telefone,
        CodigoRecuperacao.codigo == payload.codigo.strip(),
        CodigoRecuperacao.usado == 0,
        CodigoRecuperacao.expira_em > agora
    ).first()

    if not cod:
        raise HTTPException(status_code=401, detail="Código inválido ou expirado.")

    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    usuario.pin = hash_pin(pin, telefone)
    cod.usado = 1
    db.commit()
    return {"ok": True, "mensagem": "PIN redefinido com sucesso."}


# --- Notificações por e-mail ---

@app.post("/notificacoes/vencimentos")
def notificar_vencimentos(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    if not os.getenv("SMTP_HOST"):
        raise HTTPException(status_code=503, detail="SMTP não configurado no .env")

    usuarios = db.query(Usuario).filter(Usuario.email.isnot(None)).all()
    enviados = 0
    erros = 0

    for u in usuarios:
        atualizar_status_estoque(db, u.telefone)
        itens = db.query(Estoque).filter(
            Estoque.usuario_id == u.telefone,
            Estoque.status.in_(["vencido", "atencao"])
        ).all()
        if not itens:
            continue

        lista = [serializar_item(i) for i in itens]
        ok = enviar_email(
            u.email,
            "DoseMed — Medicamentos com atenção no seu estoque",
            html_vencimento(u.nome, lista)
        )
        if ok:
            enviados += 1
        else:
            erros += 1

    logger.info(f"Notificações de vencimento: {enviados} enviadas, {erros} erros")
    return {"enviados": enviados, "erros": erros, "total_usuarios_com_email": len(usuarios)}


@app.post("/notificacoes/testar-email")
def testar_email(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    usuario = db.query(Usuario).filter(Usuario.telefone == tel).first()
    if not usuario or not usuario.email:
        raise HTTPException(status_code=400, detail="Usuário não encontrado ou sem e-mail.")
    ok = enviar_email(
        usuario.email,
        "DoseMed — Teste de e-mail",
        f"<p>Olá, <strong>{usuario.nome}</strong>! Seu e-mail está configurado corretamente no DoseMed. 💊</p>"
    )
    if ok:
        return {"mensagem": f"E-mail de teste enviado para {usuario.email}"}
    raise HTTPException(status_code=503, detail="Erro ao enviar e-mail. Verifique as configurações SMTP no .env")


# --- Webhook / Estoque ---

@app.post("/webhook")
def webhook(payload: WebhookPayload, db: Session = Depends(get_db)):
    telefone = validar_telefone(payload.telefone)
    medicamento = sanitizar(payload.medicamento)

    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        nome = sanitizar(payload.nome_usuario or "Usuário")
        usuario = Usuario(telefone=telefone, nome=nome)
        db.add(usuario)
        db.commit()

    ja_tem = db.query(Estoque).filter(
        Estoque.usuario_id == telefone,
        Estoque.nome_medicamento.ilike(f"%{medicamento}%")
    ).first()

    if ja_tem and not payload.validade and payload.preco_real is None:
        msg = (
            f"Atenção: '{ja_tem.nome_medicamento}' está no estoque mas VENCIDO. Recomenda-se repor."
            if ja_tem.status == "vencido"
            else f"Você já tem '{ja_tem.nome_medicamento}' em estoque!"
        )
        return {"mensagem": msg, "status_item": ja_tem.status,
                "data_vencimento": str(ja_tem.data_validade) if ja_tem.data_validade else None}

    alerta_pa = None
    if payload.principio_ativo:
        pa = sanitizar(payload.principio_ativo)
        mesmo_pa = db.query(Estoque).filter(
            Estoque.usuario_id == telefone,
            Estoque.principio_ativo.ilike(f"%{pa}%"),
            Estoque.status.notin_(["consumido", "vencido"])
        ).first()
        if mesmo_pa:
            alerta_pa = f"Atenção: você já tem '{mesmo_pa.nome_medicamento}' com o mesmo princípio ativo em estoque."

    preco, categoria = calcular_preco(medicamento)
    data_val = None
    if payload.validade:
        try:
            data_val = date.fromisoformat(payload.validade)
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de validade inválido. Use AAAA-MM-DD.")

    status = calcular_status(data_val)
    item = Estoque(
        usuario_id=telefone,
        nome_medicamento=medicamento,
        principio_ativo=sanitizar(payload.principio_ativo or ""),
        miligramas=sanitizar(payload.miligramas or ""),
        fabricante=sanitizar(payload.fabricante or ""),
        quantidade=max(1, payload.quantidade or 1),
        manipulado=1 if payload.manipulado else 0,
        preco_real=payload.preco_real,
        data_validade=data_val,
        categoria_valor=categoria,
        preco_estimado=preco,
        status=status
    )
    db.add(item)
    db.commit()

    atualizar_status_estoque(db, telefone)
    todos = db.query(Estoque).filter(Estoque.usuario_id == telefone, Estoque.status != "consumido").all()
    prejuizo = sum(valor_efetivo(i) * (i.quantidade or 1) for i in todos if i.status == "vencido")
    em_risco = sum(valor_efetivo(i) * (i.quantidade or 1) for i in todos if i.status == "atencao")

    logger.info(f"Item cadastrado: {medicamento} — usuário {telefone}")
    return {
        "mensagem": f"'{medicamento}' cadastrado com sucesso!",
        "alerta_principio_ativo": alerta_pa,
        "preco_estimado": preco,
        "status": status,
        "prejuizo_acumulado": prejuizo,
        "valor_em_risco": em_risco
    }


@app.get("/dashboard/{telefone}")
def dashboard(telefone: str, db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    atualizar_status_estoque(db, telefone)
    itens = db.query(Estoque).filter(
        Estoque.usuario_id == telefone,
        Estoque.status != "consumido"
    ).all()

    vencidos = sort_por_vencimento([serializar_item(i) for i in itens if i.status == "vencido"])
    atencao = sort_por_vencimento([serializar_item(i) for i in itens if i.status == "atencao"])
    ok = sort_por_vencimento([serializar_item(i) for i in itens if i.status == "ok"])

    prejuizo = sum(valor_efetivo(i) * (i.quantidade or 1) for i in itens if i.status == "vencido")
    em_risco = sum(valor_efetivo(i) * (i.quantidade or 1) for i in itens if i.status == "atencao")

    return {
        "usuario": {
            "telefone": usuario.telefone,
            "nome": usuario.nome,
            "email": usuario.email,
            "bairro": usuario.bairro,
            "endereco": usuario.endereco_completo,
            "instrucoes_portaria": usuario.instrucoes_portaria,
            "is_admin": usuario.telefone == ADMIN_PHONE,
            "tem_email": bool(usuario.email),
            "aceite_lgpd": bool(usuario.aceite_lgpd)
        },
        "prejuizo_acumulado": prejuizo,
        "valor_em_risco": em_risco,
        "estoque": {"vencidos": vencidos, "atencao": atencao, "ok": ok}
    }


@app.put("/estoque/{item_id}")
def editar_item(item_id: int, payload: EditarItemPayload, db: Session = Depends(get_db)):
    item = db.query(Estoque).filter(Estoque.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item não encontrado.")
    if payload.nome_medicamento is not None:
        item.nome_medicamento = sanitizar(payload.nome_medicamento)
        item.preco_estimado, item.categoria_valor = calcular_preco(item.nome_medicamento)
    if payload.principio_ativo is not None:
        item.principio_ativo = sanitizar(payload.principio_ativo)
    if payload.miligramas is not None:
        item.miligramas = sanitizar(payload.miligramas)
    if payload.fabricante is not None:
        item.fabricante = sanitizar(payload.fabricante)
    if payload.validade is not None:
        try:
            item.data_validade = date.fromisoformat(payload.validade) if payload.validade else None
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de validade inválido.")
        item.status = calcular_status(item.data_validade)
    if payload.quantidade is not None:
        item.quantidade = max(1, payload.quantidade)
    if payload.manipulado is not None:
        item.manipulado = 1 if payload.manipulado else 0
    if payload.preco_real is not None:
        item.preco_real = payload.preco_real if payload.preco_real > 0 else None
    db.commit()
    return {"mensagem": "Medicamento atualizado.", "item": serializar_item(item)}


@app.post("/estoque/{item_id}/iniciar")
def iniciar_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(Estoque).filter(Estoque.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item não encontrado.")
    item.iniciado = 0 if item.iniciado else 1
    db.commit()
    return {"iniciado": bool(item.iniciado), "item": serializar_item(item)}


@app.post("/usuario/{telefone}/aceitar-lgpd")
def aceitar_lgpd(telefone: str, db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    usuario.aceite_lgpd = datetime.utcnow()
    db.commit()
    return {"ok": True}


@app.post("/estoque/{item_id}/consumir")
def consumir_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(Estoque).filter(Estoque.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item não encontrado.")
    item.status = "consumido"
    item.data_consumo = datetime.utcnow()
    db.commit()
    return {"mensagem": f"'{item.nome_medicamento}' marcado como consumido."}


@app.get("/historico/{telefone}")
def historico(telefone: str, db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    itens = (
        db.query(Estoque)
        .filter(Estoque.usuario_id == telefone, Estoque.status == "consumido")
        .order_by(Estoque.data_consumo.desc().nulls_last(), Estoque.id.desc())
        .all()
    )

    consumidos = [serializar_item(i) for i in itens]

    # Agrupar por mês
    por_mes: dict = {}
    total_valor = 0.0
    for c in consumidos:
        mes = (c["data_consumo"] or "?")[:7]  # "2026-05" ou "?"
        if mes not in por_mes:
            por_mes[mes] = {"itens": [], "valor": 0.0}
        valor = (c["preco_real"] or c["preco_estimado"] or 0) * c["quantidade"]
        por_mes[mes]["itens"].append(c)
        por_mes[mes]["valor"] += valor
        total_valor += valor

    meses = [
        {"mes": m, "itens": v["itens"], "valor": round(v["valor"], 2)}
        for m, v in sorted(por_mes.items(), reverse=True)
    ]

    return {
        "total_consumidos": len(consumidos),
        "total_valor_consumido": round(total_valor, 2),
        "por_mes": meses
    }


@app.delete("/estoque/{item_id}")
def excluir_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(Estoque).filter(Estoque.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item não encontrado.")
    db.delete(item)
    db.commit()
    return {"mensagem": "Medicamento removido."}


# --- Bulas ---

@app.get("/bulas-index")
def bulas_index():
    return [
        {"nomes": b.get("nomes", []), "principio_ativo": b.get("principio_ativo", ""), "sintomas": b.get("sintomas", [])}
        for b in BULAS
    ]


@app.get("/bulas/{nome}")
def buscar_bula(nome: str, db: Session = Depends(get_db)):
    resultado = buscar_bula_local(nome)
    if resultado:
        return resultado
    raise HTTPException(status_code=404, detail="Medicamento não encontrado na biblioteca.")


@app.post("/bulas/enriquecer")
@limiter.limit("20/hour")
async def enriquecer_bula(request: Request, payload: EnriquecerPayload, db: Session = Depends(get_db)):
    global BULAS
    nome = sanitizar(payload.nome)
    pa = sanitizar(payload.principio_ativo or "")
    fabricante = sanitizar(payload.fabricante or "")
    if not nome and not pa:
        raise HTTPException(status_code=400, detail="Nome inválido.")

    # Usa principio_ativo como termo de busca se nome parece genérico
    termos_genericos = {"medicamento genérico", "genérico", "medicamento", "remédio"}
    termo_busca = pa if nome.lower() in termos_genericos and pa else nome

    # Verifica se já existe (tenta por PA e por nome)
    existente = buscar_bula_local(termo_busca) or (pa and buscar_bula_local(pa))
    if existente:
        return existente

    if not ANTHROPIC_OK:
        raise HTTPException(status_code=503, detail="Enriquecimento de bulas não disponível.")
    api_key = get_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="Chave da API não configurada.")

    registrar_busca(db, "enriquecimento", termo_busca, payload.usuario_id)

    contexto_extra = ""
    if pa:
        contexto_extra += f"\nPrincípio ativo: {pa}"
    if fabricante:
        contexto_extra += f"\nFabricante: {fabricante}"

    prompt = f"""Você é um farmacêutico especialista. Crie uma entrada de bula resumida e segura para o medicamento "{termo_busca}".{contexto_extra}
Retorne SOMENTE um JSON válido, sem texto adicional, com exatamente esta estrutura:
{{
  "nomes": ["nome comercial", "outros nomes"],
  "principio_ativo": "substância ativa",
  "categoria": "categoria terapêutica",
  "indicacao": "para que serve (2-3 frases)",
  "posologia": "dose e frequência típica",
  "contraindicacoes": "quem não deve usar",
  "avisos": "avisos importantes de segurança",
  "receita": true ou false,
  "sintomas": ["sintoma1", "sintoma2", "sintoma3"],
  "dicas": ["dica prática 1", "dica prática 2", "dica prática 3"]
}}
Sintomas: palavras-chave em português que um paciente usaria para descrever por que tomaria este medicamento.
Dicas: orientações práticas do dia a dia (horário, alimento, duração máxima, cuidados especiais).
Se não conhecer o medicamento com certeza, retorne {{"erro": "desconhecido"}}."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        texto = msg.content[0].text.strip()
        try:
            dados = json.loads(texto)
        except Exception:
            match = re.search(r'\{.*\}', texto, re.DOTALL)
            dados = json.loads(match.group()) if match else {}

        if "erro" in dados:
            raise HTTPException(status_code=404, detail=f"Medicamento '{nome}' não reconhecido.")

        # Salva no arquivo e na memória
        BULAS.append(dados)
        _salvar_bulas(BULAS)
        logger.info(f"Bula enriquecida e salva: {nome}")
        return dados
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao enriquecer bula de {nome}: {e}")
        raise HTTPException(status_code=500, detail="Erro ao buscar informações do medicamento.")


# --- Log de buscas ---

@app.post("/log-busca")
def log_busca(payload: LogBuscaPayload, db: Session = Depends(get_db)):
    registrar_busca(db, payload.tipo, payload.termo, payload.usuario_id)
    return {"ok": True}


# --- Admin ---

@app.get("/admin/relatorios")
def admin_relatorios(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    logs = db.query(LogBusca).all()

    # Total por tipo
    por_tipo: dict[str, int] = {}
    for l in logs:
        por_tipo[l.tipo] = por_tipo.get(l.tipo, 0) + 1

    # Top termos
    contagem_termos: dict[str, int] = {}
    for l in logs:
        t = l.termo.lower()
        contagem_termos[t] = contagem_termos.get(t, 0) + 1
    top_termos = sorted(contagem_termos.items(), key=lambda x: -x[1])[:15]

    # Por estado
    por_estado: dict[str, int] = {}
    for l in logs:
        e = l.estado or "??"
        por_estado[e] = por_estado.get(e, 0) + 1
    top_estados = sorted(por_estado.items(), key=lambda x: -x[1])[:10]

    # Por dia (últimos 30 dias)
    por_dia: dict[str, int] = {}
    cutoff = datetime.utcnow().date() - timedelta(days=30)
    for l in logs:
        if l.timestamp and l.timestamp.date() >= cutoff:
            d = str(l.timestamp.date())
            por_dia[d] = por_dia.get(d, 0) + 1

    # Usuários únicos
    usuarios_unicos = len(set(l.usuario_id for l in logs if l.usuario_id))

    # Total de usuários cadastrados
    total_usuarios = db.query(Usuario).count()
    total_itens = db.query(Estoque).filter(Estoque.status != "consumido").count()

    return {
        "total_buscas": len(logs),
        "usuarios_unicos_buscas": usuarios_unicos,
        "total_usuarios": total_usuarios,
        "total_itens_estoque": total_itens,
        "por_tipo": por_tipo,
        "top_termos": [{"termo": t, "count": c} for t, c in top_termos],
        "top_estados": [{"estado": e, "count": c} for e, c in top_estados],
        "por_dia": [{"data": d, "count": c} for d, c in sorted(por_dia.items())]
    }


@app.get("/admin/insights")
def admin_insights(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    # Buscas por (bairro, termo, tipo, periodo)
    logs = db.query(LogBusca).filter(LogBusca.bairro.isnot(None)).all()
    agg_buscas: dict = {}
    for l in logs:
        periodo = l.timestamp.strftime("%m/%Y") if l.timestamp else "?"
        chave = (l.bairro or "?", l.termo.lower()[:50], l.tipo, periodo)
        agg_buscas[chave] = agg_buscas.get(chave, 0) + 1

    buscas_rows = [
        {"bairro": k[0], "termo": k[1], "tipo": k[2], "periodo": k[3], "count": v}
        for k, v in sorted(agg_buscas.items(), key=lambda x: -x[1])
    ][:300]

    # Ticket médio por (bairro, medicamento, periodo)
    itens_com_preco = (
        db.query(Estoque, Usuario.bairro)
        .join(Usuario, Estoque.usuario_id == Usuario.telefone)
        .filter(Estoque.preco_real.isnot(None), Usuario.bairro.isnot(None))
        .all()
    )
    agg_ticket: dict = {}
    for item, bairro in itens_com_preco:
        # Usar mês de criação não existe — usamos o período atual como referência
        nome = item.nome_medicamento
        chave = (bairro, nome)
        if chave not in agg_ticket:
            agg_ticket[chave] = []
        agg_ticket[chave].append(item.preco_real)

    ticket_rows = [
        {
            "bairro": k[0],
            "medicamento": k[1],
            "ticket_medio": round(sum(v) / len(v), 2),
            "amostras": len(v)
        }
        for k, v in sorted(agg_ticket.items(), key=lambda x: -len(x[1]))
    ][:200]

    return {"buscas": buscas_rows, "tickets": ticket_rows}


# --- Leads ---

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


def gerar_leads_para_item(db: Session, item: Estoque, usuario: Usuario, origem: str = "cron"):
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

        # Evitar lead duplicado nos últimos 30 dias
        cutoff = datetime.utcnow() - timedelta(days=30)
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
            enviar_email(f.email, f"DoseMed — Lead: {item.nome_medicamento} ({usuario.bairro})", html)

        enviados += 1

    db.commit()
    return enviados


# --- Cron / Notificações automáticas ---

def cron_notificacoes_vencimento():
    db = SessionLocal()
    try:
        hoje = date.today()
        dias = int(get_config(db, "dias_aviso_vencimento", "30"))
        limite = hoje + timedelta(days=dias)
        usuarios = db.query(Usuario).all()
        emails_enviados = 0
        leads_gerados = 0

        for u in usuarios:
            itens_atencao = db.query(Estoque).filter(
                Estoque.usuario_id == u.telefone,
                Estoque.status != "consumido",
                Estoque.data_validade.isnot(None),
                Estoque.data_validade <= limite
            ).all()

            if not itens_atencao:
                continue

            # Notificação por e-mail ao usuário
            if u.email:
                lista = [serializar_item(i) for i in itens_atencao]
                ok = enviar_email(u.email, "DoseMed — Medicamentos com atenção no seu estoque", html_vencimento(u.nome, lista))
                if ok:
                    emails_enviados += 1

            # Geração de leads para farmácias parceiras
            for item in itens_atencao:
                leads_gerados += gerar_leads_para_item(db, item, u, origem="cron")

        logger.info(f"[CRON] {emails_enviados} e-mails enviados, {leads_gerados} leads gerados")
    except Exception as e:
        logger.error(f"[CRON] Erro: {e}")
    finally:
        db.close()


def cron_disparar_alarmes():
    if not PUSH_OK:
        return
    db = SessionLocal()
    try:
        # Converte UTC para BRT (UTC-3)
        agora_brt = datetime.utcnow() - timedelta(hours=3)
        horario_atual = agora_brt.strftime("%H:%M")
        dia_semana = str(agora_brt.isoweekday())  # 1=seg, 7=dom

        alarmes = db.query(AlarmeRemedio).filter(
            AlarmeRemedio.ativo == 1,
            AlarmeRemedio.horario == horario_atual
        ).all()

        disparados = 0
        for a in alarmes:
            if dia_semana not in a.dias.split(","):
                continue
            subs = db.query(PushSub).filter(PushSub.usuario_id == a.usuario_id).all()
            for sub in subs:
                if enviar_push(sub, "💊 DoseMed", f"Hora de tomar {a.nome_med}"):
                    disparados += 1
        if disparados:
            logger.info(f"[ALARMES] {disparados} notificações push enviadas ({horario_atual} BRT)")
    except Exception as e:
        logger.error(f"[ALARMES] Erro: {e}")
    finally:
        db.close()


if SCHEDULER_OK:
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(cron_notificacoes_vencimento, CronTrigger(hour=8, minute=0),
                       id="vencimentos", replace_existing=True, max_instances=1, coalesce=True)
    _scheduler.add_job(cron_disparar_alarmes, CronTrigger(minute="*"),
                       id="alarmes", replace_existing=True, max_instances=1, coalesce=True)
else:
    _scheduler = None


@app.on_event("startup")
def _startup():
    if _scheduler and not _scheduler.running:
        _scheduler.start()
        logger.info("Scheduler iniciado (vencimentos 08:00, alarmes a cada minuto)")
    elif not _scheduler:
        logger.warning("APScheduler não instalado — notificações automáticas desativadas")


@app.on_event("shutdown")
def _shutdown():
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)


# --- Farmácias (admin) ---

class FarmaciaPayload(BaseModel):
    nome: str
    cnpj: Optional[str] = None
    email: str
    telefone_contato: Optional[str] = None
    bairros: Optional[str] = None
    plano: Optional[str] = "lead"
    ativo: Optional[int] = 1
    atende_manipulado: Optional[int] = 0


def serializar_farmacia(f: Farmacia, db: Session) -> dict:
    total_leads = db.query(Lead).filter(Lead.farmacia_id == f.id).count()
    leads_mes = db.query(Lead).filter(
        Lead.farmacia_id == f.id,
        Lead.criado_em >= datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)
    ).count()
    return {
        "id": f.id, "nome": f.nome, "cnpj": f.cnpj, "email": f.email,
        "telefone_contato": f.telefone_contato, "bairros": f.bairros,
        "plano": f.plano, "ativo": bool(f.ativo), "atende_manipulado": bool(f.atende_manipulado),
        "asaas_customer_id": f.asaas_customer_id, "asaas_subscription_id": f.asaas_subscription_id,
        "total_leads": total_leads, "leads_mes": leads_mes,
        "criado_em": str(f.criado_em.date()) if f.criado_em else None,
    }


@app.get("/admin/farmacias")
def listar_farmacias(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    farmacias = db.query(Farmacia).order_by(Farmacia.nome).all()
    return [serializar_farmacia(f, db) for f in farmacias]


@app.post("/admin/farmacias")
def criar_farmacia(telefone: str, payload: FarmaciaPayload, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    f = Farmacia(
        nome=sanitizar(payload.nome), cnpj=payload.cnpj, email=payload.email.strip().lower(),
        telefone_contato=payload.telefone_contato, bairros=payload.bairros,
        plano=payload.plano or "lead", ativo=1, atende_manipulado=payload.atende_manipulado or 0
    )
    db.add(f); db.flush()

    # Criar cliente no Asaas
    cid = asaas_criar_cliente(f.nome, f.email, f.cnpj)
    if cid:
        f.asaas_customer_id = cid
        # Se plano não é 'lead', criar assinatura imediatamente
        if payload.plano and payload.plano != "lead":
            preco_chave = f"preco_plano_{payload.plano}"
            valor = float(get_config(db, preco_chave, "299.00"))
            sub_id = asaas_criar_assinatura(cid, valor, f"DoseMed — Plano {payload.plano.capitalize()} — {f.nome}")
            if sub_id:
                f.asaas_subscription_id = sub_id

    db.commit()
    return serializar_farmacia(f, db)


@app.put("/admin/farmacias/{farmacia_id}")
def editar_farmacia(farmacia_id: int, telefone: str, payload: FarmaciaPayload, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    f = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada.")
    plano_anterior = f.plano
    f.nome = sanitizar(payload.nome); f.cnpj = payload.cnpj; f.email = payload.email.strip().lower()
    f.telefone_contato = payload.telefone_contato; f.bairros = payload.bairros
    f.plano = payload.plano or "lead"; f.ativo = payload.ativo; f.atende_manipulado = payload.atende_manipulado or 0

    # Se mudou para plano pago e não tem assinatura, criar
    if f.plano != "lead" and f.plano != plano_anterior and f.asaas_customer_id and not f.asaas_subscription_id:
        preco_chave = f"preco_plano_{f.plano}"
        valor = float(get_config(db, preco_chave, "299.00"))
        sub_id = asaas_criar_assinatura(f.asaas_customer_id, valor, f"DoseMed — Plano {f.plano.capitalize()} — {f.nome}")
        if sub_id:
            f.asaas_subscription_id = sub_id

    db.commit()
    return serializar_farmacia(f, db)


@app.post("/admin/farmacias/{farmacia_id}/faturar-leads")
def faturar_leads(farmacia_id: int, telefone: str, db: Session = Depends(get_db)):
    """Gera cobrança Asaas pelos leads do mês atual ainda não faturados."""
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    f = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada.")
    if not f.asaas_customer_id:
        raise HTTPException(status_code=400, detail="Farmácia sem cliente Asaas cadastrado.")

    inicio_mes = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)
    leads_nao_faturados = db.query(Lead).filter(
        Lead.farmacia_id == farmacia_id,
        Lead.criado_em >= inicio_mes,
        Lead.asaas_charge_id.is_(None)
    ).all()

    if not leads_nao_faturados:
        return {"mensagem": "Nenhum lead a faturar.", "total": 0, "valor": 0}

    valor_total = sum(l.preco_cobrado or 0 for l in leads_nao_faturados)
    mes_atual = datetime.utcnow().strftime("%m/%Y")
    charge_id = asaas_criar_cobranca(
        f.asaas_customer_id, valor_total,
        f"DoseMed — {len(leads_nao_faturados)} leads em {mes_atual} — {f.nome}"
    )
    if charge_id:
        for l in leads_nao_faturados:
            l.asaas_charge_id = charge_id
        db.commit()
        mensagem = "Cobrança PIX gerada com sucesso no Asaas."
    else:
        mensagem = "Leads contabilizados, mas cobrança Asaas falhou — verifique o CNPJ da farmácia e o log."

    return {"mensagem": mensagem, "total_leads": len(leads_nao_faturados),
            "valor": round(valor_total, 2), "asaas_charge_id": charge_id}


@app.get("/admin/leads")
def listar_leads(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    leads = db.query(Lead).order_by(Lead.criado_em.desc()).limit(200).all()
    return [{
        "id": l.id, "farmacia_id": l.farmacia_id,
        "farmacia_nome": l.farmacia.nome if l.farmacia else "?",
        "bairro": l.usuario_bairro, "medicamento": l.medicamento,
        "principio_ativo": l.principio_ativo, "manipulado": bool(l.manipulado),
        "origem": l.origem, "status": l.status,
        "preco_cobrado": l.preco_cobrado, "asaas_charge_id": l.asaas_charge_id,
        "criado_em": str(l.criado_em.date()) if l.criado_em else None,
    } for l in leads]


@app.post("/admin/leads/gerar-manual")
def gerar_leads_manual(telefone: str, db: Session = Depends(get_db)):
    """Dispara geração de leads manualmente (sem esperar cron)."""
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    hoje = date.today()
    dias = int(get_config(db, "dias_aviso_vencimento", "30"))
    limite = hoje + timedelta(days=dias)
    usuarios = db.query(Usuario).all()
    total = 0
    for u in usuarios:
        itens = db.query(Estoque).filter(
            Estoque.usuario_id == u.telefone,
            Estoque.status != "consumido",
            Estoque.data_validade.isnot(None),
            Estoque.data_validade <= limite
        ).all()
        for item in itens:
            total += gerar_leads_para_item(db, item, u, origem="manual")
    return {"leads_gerados": total}


# --- Configurações (admin) ---

@app.get("/admin/configuracoes")
def listar_configuracoes(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    return [{"chave": c.chave, "valor": c.valor, "descricao": c.descricao}
            for c in db.query(Configuracao).order_by(Configuracao.chave).all()]


@app.put("/admin/configuracoes/{chave}")
def salvar_configuracao(chave: str, telefone: str, payload: dict, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    c = db.query(Configuracao).filter(Configuracao.chave == chave).first()
    if not c:
        raise HTTPException(status_code=404, detail="Configuração não encontrada.")
    c.valor = str(payload.get("valor", c.valor))
    db.commit()
    return {"chave": c.chave, "valor": c.valor}


@app.get("/admin/recebimentos/{farmacia_id}")
def recebimentos_farmacia(farmacia_id: int, telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    f = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada.")
    cobrancas = asaas_listar_cobrancas(f.asaas_customer_id or "")
    return {"farmacia": f.nome, "cobrancas": cobrancas}


# --- Push Notifications ---

@app.get("/push/vapid-public-key")
def vapid_public_key():
    key = os.getenv("VAPID_PUBLIC_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="VAPID não configurado.")
    return {"key": key}


@app.post("/push/subscribe")
def push_subscribe(payload: PushSubPayload, db: Session = Depends(get_db)):
    telefone = validar_telefone(payload.telefone)
    # Upsert: atualiza se endpoint já existe para o usuário
    sub = db.query(PushSub).filter(PushSub.endpoint == payload.endpoint).first()
    if sub:
        sub.usuario_id = telefone
        sub.p256dh = payload.p256dh
        sub.auth = payload.auth
    else:
        sub = PushSub(usuario_id=telefone, endpoint=payload.endpoint,
                      p256dh=payload.p256dh, auth=payload.auth)
        db.add(sub)
    db.commit()
    return {"ok": True}


@app.post("/push/testar")
def push_testar(telefone: str, db: Session = Depends(get_db)):
    """Envia uma notificação de teste imediata ao usuário."""
    telefone = validar_telefone(telefone)
    subs = db.query(PushSub).filter(PushSub.usuario_id == telefone).all()
    if not subs:
        raise HTTPException(status_code=400, detail="Nenhuma assinatura push encontrada. Ative as notificações primeiro.")
    ok = sum(1 for s in subs if enviar_push(s, "💊 DoseMed", "Notificação de teste — alarmes funcionando!"))
    if not ok:
        raise HTTPException(status_code=503, detail="Falha ao enviar notificação. Verifique as chaves VAPID.")
    return {"ok": True, "enviadas": ok}


@app.delete("/push/subscribe")
def push_unsubscribe(telefone: str, endpoint: str, db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    db.query(PushSub).filter(
        PushSub.usuario_id == telefone,
        PushSub.endpoint == endpoint
    ).delete()
    db.commit()
    return {"ok": True}


# --- Alarmes ---

@app.get("/alarmes/{telefone}")
def listar_alarmes(telefone: str, db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    alarmes = db.query(AlarmeRemedio).filter(AlarmeRemedio.usuario_id == telefone).all()
    return [{"id": a.id, "nome_med": a.nome_med, "horario": a.horario,
             "dias": a.dias, "ativo": bool(a.ativo)} for a in alarmes]


@app.post("/alarmes")
def criar_alarme(payload: AlarmePayload, db: Session = Depends(get_db)):
    telefone = validar_telefone(payload.telefone)
    if not re.match(r"^\d{2}:\d{2}$", payload.horario):
        raise HTTPException(status_code=400, detail="Horário inválido. Use HH:MM.")
    a = AlarmeRemedio(usuario_id=telefone, nome_med=sanitizar(payload.nome_med),
                      horario=payload.horario, dias=payload.dias or "1,2,3,4,5,6,7", ativo=1)
    db.add(a)
    db.commit()
    return {"id": a.id, "nome_med": a.nome_med, "horario": a.horario,
            "dias": a.dias, "ativo": True}


@app.put("/alarmes/{alarme_id}")
def editar_alarme(alarme_id: int, payload: AlarmePayload, db: Session = Depends(get_db)):
    a = db.query(AlarmeRemedio).filter(AlarmeRemedio.id == alarme_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Alarme não encontrado.")
    if payload.nome_med:
        a.nome_med = sanitizar(payload.nome_med)
    if payload.horario:
        if not re.match(r"^\d{2}:\d{2}$", payload.horario):
            raise HTTPException(status_code=400, detail="Horário inválido. Use HH:MM.")
        a.horario = payload.horario
    if payload.dias is not None:
        a.dias = payload.dias
    if payload.ativo is not None:
        a.ativo = payload.ativo
    db.commit()
    return {"id": a.id, "nome_med": a.nome_med, "horario": a.horario,
            "dias": a.dias, "ativo": bool(a.ativo)}


@app.delete("/alarmes/{alarme_id}")
def excluir_alarme(alarme_id: int, db: Session = Depends(get_db)):
    a = db.query(AlarmeRemedio).filter(AlarmeRemedio.id == alarme_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Alarme não encontrado.")
    db.delete(a)
    db.commit()
    return {"ok": True}


# --- Webhook Asaas ---

@app.post("/webhook/asaas")
async def webhook_asaas(request: Request, db: Session = Depends(get_db)):
    try:
        data = await request.json()
        evento = data.get("event", "")
        payment = data.get("payment", {})
        charge_id = payment.get("id", "")
        logger.info(f"[ASAAS] Webhook: {evento} — {charge_id}")
        if evento in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED") and charge_id:
            leads = db.query(Lead).filter(Lead.asaas_charge_id == charge_id).all()
            for l in leads:
                l.status = "pago"
            db.commit()
    except Exception as e:
        logger.error(f"[ASAAS] Webhook erro: {e}")
    return {"ok": True}


# --- Exportar PDF ---

@app.get("/exportar/{telefone}", response_class=HTMLResponse)
def exportar(telefone: str, db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    atualizar_status_estoque(db, telefone)
    itens = db.query(Estoque).filter(
        Estoque.usuario_id == telefone,
        Estoque.status != "consumido"
    ).all()

    def linhas(grupo):
        items_grupo = sort_por_vencimento([serializar_item(i) for i in itens if i.status == grupo])
        if not items_grupo:
            return '<tr><td colspan="6" style="color:#aaa;font-style:italic;padding:8px 12px">Nenhum item</td></tr>'
        rows = ""
        for i in items_grupo:
            preco = i["preco_real"] if i["preco_real"] else i["preco_estimado"]
            total = preco * i["quantidade"]
            rows += f"""<tr>
              <td>{i['nome']}{'<span class="badge-manip">Manip.</span>' if i['manipulado'] else ''}</td>
              <td>{i['principio_ativo'] or '—'}</td>
              <td>{i['miligramas'] or '—'}</td>
              <td>{i['fabricante'] or '—'}</td>
              <td>{i['data_vencimento'] or '—'}</td>
              <td>R$ {total:.2f}</td>
            </tr>"""
        return rows

    prejuizo = sum(valor_efetivo(i) * (i.quantidade or 1) for i in itens if i.status == "vencido")
    em_risco = sum(valor_efetivo(i) * (i.quantidade or 1) for i in itens if i.status == "atencao")
    hoje = date.today().strftime("%d/%m/%Y")

    html = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<title>DoseMed — Estoque de {usuario.nome}</title>
<style>
  body{{font-family:Arial,sans-serif;font-size:12px;color:#333;margin:24px}}
  h1{{color:#2563eb;font-size:18px;margin-bottom:2px}}
  .sub{{color:#888;font-size:11px;margin-bottom:16px}}
  .resumo{{display:flex;gap:24px;margin-bottom:20px}}
  .card{{border:1px solid #eee;border-radius:8px;padding:12px 20px;min-width:140px}}
  .card .val{{font-size:22px;font-weight:900}}
  .red{{color:#ef4444}}.amber{{color:#f59e0b}}.gray{{color:#9ca3af}}
  table{{width:100%;border-collapse:collapse;margin-bottom:20px}}
  th{{background:#f3f4f6;padding:8px 10px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em}}
  td{{padding:7px 10px;border-bottom:1px solid #f3f4f6;vertical-align:top}}
  .sec-title{{font-size:13px;font-weight:bold;margin:16px 0 6px;padding:4px 10px;border-radius:4px}}
  .sec-vencido{{background:#fee2e2;color:#b91c1c}}
  .sec-atencao{{background:#fef3c7;color:#92400e}}
  .sec-ok{{background:#d1fae5;color:#065f46}}
  .badge-manip{{font-size:9px;background:#ede9fe;color:#7c3aed;padding:1px 5px;border-radius:3px;margin-left:4px;vertical-align:middle}}
  .disclaimer{{font-size:10px;color:#9ca3af;border-top:1px solid #eee;padding-top:10px;margin-top:10px}}
  .btn{{display:inline-block;background:#2563eb;color:#fff;padding:8px 20px;border:none;border-radius:8px;cursor:pointer;font-size:13px;margin-bottom:16px}}
  @media print{{.btn{{display:none}}}}
</style></head><body>
<button class="btn" onclick="window.print()">Imprimir / Salvar PDF</button>
<h1>💊 DoseMed — Estoque de Medicamentos</h1>
<div class="sub">{usuario.nome} · {usuario.telefone} · Gerado em {hoje}</div>
<div class="resumo">
  <div class="card"><div style="font-size:11px;color:#888">Prejuízo acumulado</div><div class="val red">R$ {prejuizo:.2f}</div><div style="font-size:10px;color:#aaa">Vencidos</div></div>
  <div class="card"><div style="font-size:11px;color:#888">Em risco</div><div class="val amber">R$ {em_risco:.2f}</div><div style="font-size:10px;color:#aaa">Vencem em 30 dias</div></div>
  <div class="card"><div style="font-size:11px;color:#888">Total de itens</div><div class="val gray">{len(itens)}</div><div style="font-size:10px;color:#aaa">No estoque</div></div>
</div>
<div class="sec-title sec-vencido">Vencidos ({sum(1 for i in itens if i.status=='vencido')})</div>
<table><tr><th>Medicamento</th><th>Princípio ativo</th><th>Dosagem</th><th>Fabricante</th><th>Vencimento</th><th>Valor</th></tr>{linhas('vencido')}</table>
<div class="sec-title sec-atencao">Atenção — Vencem em até 30 dias ({sum(1 for i in itens if i.status=='atencao')})</div>
<table><tr><th>Medicamento</th><th>Princípio ativo</th><th>Dosagem</th><th>Fabricante</th><th>Vencimento</th><th>Valor</th></tr>{linhas('atencao')}</table>
<div class="sec-title sec-ok">Em dia ({sum(1 for i in itens if i.status=='ok')})</div>
<table><tr><th>Medicamento</th><th>Princípio ativo</th><th>Dosagem</th><th>Fabricante</th><th>Vencimento</th><th>Valor</th></tr>{linhas('ok')}</table>
<div class="disclaimer">Relatório gerado pelo DoseMed em {hoje}. Este documento é apenas informativo e não substitui orientação médica ou farmacêutica.</div>
</body></html>"""
    return HTMLResponse(content=html)


# --- Reconhecer imagem ---

@app.post("/reconhecer-imagem")
@limiter.limit("15/hour")
async def reconhecer_imagem(request: Request, arquivo: UploadFile = File(...)):
    if not ANTHROPIC_OK:
        raise HTTPException(status_code=503, detail="Reconhecimento de imagem não disponível.")
    api_key = get_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="Chave da API não configurada.")
    try:
        conteudo = await arquivo.read()
        conteudo = comprimir_imagem(conteudo)
        b64 = base64.standard_b64encode(conteudo).decode("utf-8")
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": (
                    "Esta é uma embalagem de medicamento. Extraia as informações e retorne SOMENTE um JSON válido, "
                    "sem texto adicional:\n"
                    '{"nome_medicamento":"nome comercial","principio_ativo":"substância ativa",'
                    '"miligramas":"dosagem ex 500mg","fabricante":"laboratório fabricante"}\n'
                    "Use null para campos não encontrados."
                )}
            ]}]
        )
        texto = msg.content[0].text.strip()
        try:
            dados = json.loads(texto)
        except Exception:
            match = re.search(r'\{.*\}', texto, re.DOTALL)
            dados = json.loads(match.group()) if match else {}
        logger.info("Imagem reconhecida com sucesso")
        return {
            "nome_medicamento": dados.get("nome_medicamento"),
            "principio_ativo": dados.get("principio_ativo"),
            "miligramas": dados.get("miligramas"),
            "fabricante": dados.get("fabricante"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao reconhecer imagem: {e}")
        raise HTTPException(status_code=500, detail="Erro ao processar imagem.")


@app.post("/confirmar-chegada")
def confirmar_chegada(payload: ConfirmarChegadaPayload, db: Session = Depends(get_db)):
    pedido = db.query(Pedido).filter(Pedido.id == payload.pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado.")
    data_val = None
    if payload.validade:
        try:
            data_val = date.fromisoformat(payload.validade)
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de validade inválido.")
    pedido.status = "entregue" if payload.validade else "aguardando_validade"
    db.commit()
    if payload.validade:
        preco, categoria = calcular_preco(pedido.medicamento_nome)
        item = Estoque(
            usuario_id=pedido.usuario_id, nome_medicamento=pedido.medicamento_nome,
            data_validade=data_val, categoria_valor=categoria,
            preco_estimado=preco, status=calcular_status(data_val)
        )
        db.add(item)
        db.commit()
        return {"mensagem": "Entrega confirmada e estoque atualizado!"}
    return {"mensagem": "Entrega confirmada. Informe a validade para atualizar o estoque."}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return await http_exception_handler(request, exc)
    logger.error(f"Erro não tratado em {request.url}: {exc}", exc_info=True)
    return HTMLResponse(status_code=500, content='{"detail":"Erro interno. Tente novamente."}')


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
