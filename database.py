# database.py — engine, session, migrations, auth dependencies

import os
import logging
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, Header, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from slowapi import Limiter
from slowapi.util import get_remote_address

load_dotenv()

from models import (
    Base, Usuario, Farmacia, Configuracao,
    Estoque, Pedido, CodigoRecuperacao, LogBusca, Lead, PushSub,
    AlarmeRemedio, OrcamentoSolicitacao, OrcamentoResposta,
    LogEvento, AnuncioPush, InteresseAnuncio, Feedback,
)

logger = logging.getLogger("dosemed")

# --- Rate limiter (declared here so routers can import it) ---
limiter = Limiter(key_func=get_remote_address, default_limits=[])

# --- Database ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dosemed.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)

# --- Migrations (safe add-only) ---
_migracoes = {
    "usuarios": ["pin TEXT", "email TEXT", "bairro TEXT", "aceite_lgpd TIMESTAMP", "genero TEXT", "session_token TEXT", "pin_tentativas INTEGER DEFAULT 0", "pin_bloqueado_ate TIMESTAMP"],
    "estoque": ["iniciado INTEGER DEFAULT 0", "data_consumo TIMESTAMP", "uso_continuo INTEGER DEFAULT 0"],
    "anuncios_push": [
        "produto TEXT", "preco_de REAL", "preco_por REAL",
        "data_expiracao DATE", "tem_entrega INTEGER DEFAULT 0",
        "valor_frete REAL", "formas_pagamento TEXT",
        "whatsapp_contato TEXT", "categorias TEXT",
    ],
    "log_buscas": ["bairro TEXT"],
    "leads": ["asaas_charge_id TEXT", "criado_em TIMESTAMP"],
    "farmacias": [
        "atende_manipulado INTEGER DEFAULT 0",
        "asaas_customer_id TEXT",
        "asaas_subscription_id TEXT",
        "criado_em TIMESTAMP",
        "pin TEXT",
        "session_token TEXT",
        "pin_tentativas INTEGER DEFAULT 0",
        "pin_bloqueado_ate TIMESTAMP",
        "rating_total REAL DEFAULT 0",
        "rating_count INTEGER DEFAULT 0",
        "origem TEXT DEFAULT 'admin'",
    ],
    "alarmes": [],
    "push_subs": [],
    "orcamentos_solicitacoes": ["entregue INTEGER"],
}
with engine.connect() as conn:
    for tabela, colunas in _migracoes.items():
        for col_def in colunas:
            col_nome = col_def.split()[0]
            try:
                conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {col_def}"))
                conn.commit()
                logger.info(f"Migração: {tabela}.{col_nome} adicionado.")
            except Exception as ex:
                conn.rollback()
                msg = str(ex).lower()
                if "already exists" not in msg and "duplicate column" not in msg:
                    logger.warning(f"Migração {tabela}.{col_nome}: {ex}")

# Fix PostgreSQL sequences after data migration
if not DATABASE_URL.startswith("sqlite"):
    _tabelas_seq = ["estoque", "pedidos", "codigos_recuperacao", "log_buscas",
                    "farmacias", "leads", "push_subs", "alarmes",
                    "orcamentos_solicitacoes", "orcamentos_respostas",
                    "log_eventos", "anuncios_push", "feedbacks", "interesses_anuncios"]
    with engine.connect() as conn:
        for _t in _tabelas_seq:
            try:
                conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{_t}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {_t}), 1))"
                ))
                conn.commit()
            except Exception as _ex:
                logger.warning(f"Sequence fix {_t}: {_ex}")

# Seed default configurations
_CONFIG_DEFAULT = {
    "preco_lead_comum":      ("5.00",  "Preço por lead comum enviado à farmácia (R$)"),
    "preco_lead_manipulado": ("15.00", "Preço por lead de manipulado enviado à farmácia (R$)"),
    "preco_plano_basico":    ("299.00","Assinatura mensal plano básico (R$/mês)"),
    "preco_plano_pro":       ("499.00","Assinatura mensal plano pro (R$/mês)"),
    "preco_plano_manipulado":("699.00","Assinatura mensal plano manipulação (R$/mês)"),
    "dias_aviso_vencimento": ("30",    "Dias antes do vencimento para gerar lead"),
    "asaas_env":             ("sandbox","Ambiente Asaas: sandbox | production"),
    "dias_alerta_reposicao": ("5",     "Dias restantes para disparar alerta de reposição de remédio"),
    "minutos_disputa_lead":  ("30",    "Minutos que farmácias têm para responder um orçamento"),
    "max_farmacias_orcamento":("4",    "Máximo de farmácias notificadas por orçamento"),
    "preco_anuncio_bairro":  ("30.00", "Preço anúncio push — público bairro (R$)"),
    "preco_anuncio_todos":   ("70.00", "Preço anúncio push — público todos os bairros (R$)"),
}
with SessionLocal() as _sess:
    for chave, (valor, descricao) in _CONFIG_DEFAULT.items():
        if not _sess.query(Configuracao).filter(Configuracao.chave == chave).first():
            _sess.add(Configuracao(chave=chave, valor=valor, descricao=descricao))
    _sess.commit()


# --- Session dependency ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Config helper ---
def get_config(db: Session, chave: str, default: str = "") -> str:
    c = db.query(Configuracao).filter(Configuracao.chave == chave).first()
    return c.valor if c else default


# --- Auth dependencies ---
def get_usuario_autenticado(x_token: Optional[str] = Header(None), db: Session = Depends(get_db)) -> Usuario:
    if not x_token:
        raise HTTPException(status_code=401, detail="Sessão obrigatória. Faça login novamente.")
    u = db.query(Usuario).filter(Usuario.session_token == x_token).first()
    if not u:
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada. Faça login novamente.")
    return u


def get_farmacia_autenticada(x_token: Optional[str] = Header(None), db: Session = Depends(get_db)) -> Farmacia:
    if not x_token:
        raise HTTPException(status_code=401, detail="Sessão obrigatória. Faça login novamente.")
    f = db.query(Farmacia).filter(Farmacia.session_token == x_token).first()
    if not f:
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada. Faça login novamente.")
    return f
