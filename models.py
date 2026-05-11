from sqlalchemy import Column, String, Float, Date, ForeignKey, Integer, DateTime, Text, JSON
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()


class Usuario(Base):
    __tablename__ = "usuarios"
    telefone = Column(String, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    email = Column(String, nullable=True)
    endereco_completo = Column(String)
    instrucoes_portaria = Column(String)
    pin = Column(String, nullable=True)
    bairro = Column(String, nullable=True)
    aceite_lgpd = Column(DateTime, nullable=True)
    estoque = relationship("Estoque", back_populates="usuario")
    pedidos = relationship("Pedido", back_populates="usuario")


class Estoque(Base):
    __tablename__ = "estoque"
    id = Column(Integer, primary_key=True, autoincrement=True)
    usuario_id = Column(String, ForeignKey("usuarios.telefone"), nullable=False)
    nome_medicamento = Column(String, nullable=False)
    principio_ativo = Column(String, nullable=True)
    miligramas = Column(String, nullable=True)
    fabricante = Column(String, nullable=True)
    data_validade = Column(Date, nullable=True)
    quantidade = Column(Integer, default=1)
    manipulado = Column(Integer, default=0)
    iniciado = Column(Integer, default=0)
    data_consumo = Column(DateTime, nullable=True)
    preco_real = Column(Float, nullable=True)
    categoria_valor = Column(String, default="baixo")
    preco_estimado = Column(Float, default=10.0)
    status = Column(String, default="ok")
    usuario = relationship("Usuario", back_populates="estoque")


class Pedido(Base):
    __tablename__ = "pedidos"
    id = Column(Integer, primary_key=True, autoincrement=True)
    usuario_id = Column(String, ForeignKey("usuarios.telefone"), nullable=False)
    farmacia_id = Column(String, nullable=True)
    medicamento_nome = Column(String, nullable=False)
    status = Column(String, default="cotacao")
    usuario = relationship("Usuario", back_populates="pedidos")


class CodigoRecuperacao(Base):
    __tablename__ = "codigos_recuperacao"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telefone = Column(String, nullable=False)
    codigo = Column(String, nullable=False)
    expira_em = Column(DateTime, nullable=False)
    usado = Column(Integer, default=0)


class LogBusca(Base):
    __tablename__ = "log_buscas"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    tipo = Column(String, nullable=False)
    termo = Column(String, nullable=False)
    ddd = Column(String, nullable=True)
    estado = Column(String, nullable=True)
    bairro = Column(String, nullable=True)
    usuario_id = Column(String, nullable=True)


class Farmacia(Base):
    __tablename__ = "farmacias"
    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, nullable=False)
    cnpj = Column(String, nullable=True)
    email = Column(String, nullable=False)
    telefone_contato = Column(String, nullable=True)
    bairros = Column(String, nullable=True)
    plano = Column(String, default="lead")           # lead | basico | pro | manipulado
    ativo = Column(Integer, default=1)
    atende_manipulado = Column(Integer, default=0)
    asaas_customer_id = Column(String, nullable=True)
    asaas_subscription_id = Column(String, nullable=True)
    pin = Column(String, nullable=True)
    rating_total = Column(Float, default=0.0)
    rating_count = Column(Integer, default=0)
    criado_em = Column(DateTime, default=datetime.utcnow)
    leads = relationship("Lead", back_populates="farmacia")
    orcamento_respostas = relationship("OrcamentoResposta", back_populates="farmacia")


class OrcamentoSolicitacao(Base):
    __tablename__ = "orcamentos_solicitacoes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    usuario_id = Column(String, ForeignKey("usuarios.telefone"), nullable=False)
    nome_med = Column(String, nullable=False)
    quantidade_restante = Column(Integer, nullable=True)
    status = Column(String, default="coletando")    # coletando | aguardando_usuario | fechado | cancelado | sem_resposta
    criado_em = Column(DateTime, default=datetime.utcnow)
    expira_em = Column(DateTime, nullable=True)
    respostas = relationship("OrcamentoResposta", back_populates="solicitacao")


class OrcamentoResposta(Base):
    __tablename__ = "orcamentos_respostas"
    id = Column(Integer, primary_key=True, autoincrement=True)
    solicitacao_id = Column(Integer, ForeignKey("orcamentos_solicitacoes.id"), nullable=False)
    farmacia_id = Column(Integer, ForeignKey("farmacias.id"), nullable=False)
    token = Column(String, unique=True, nullable=False)
    preco = Column(Float, nullable=True)
    prazo_entrega = Column(String, nullable=True)
    formas_pagamento = Column(String, nullable=True)  # json: ["pix","cartao","dinheiro"]
    status = Column(String, default="pendente")       # pendente | respondido | ganhou | perdeu | expirou
    respondido_em = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow)
    solicitacao = relationship("OrcamentoSolicitacao", back_populates="respostas")
    farmacia = relationship("Farmacia", back_populates="orcamento_respostas")


class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True, autoincrement=True)
    farmacia_id = Column(Integer, ForeignKey("farmacias.id"), nullable=False)
    usuario_bairro = Column(String, nullable=True)
    medicamento = Column(String, nullable=False)
    principio_ativo = Column(String, nullable=True)
    miligramas = Column(String, nullable=True)
    manipulado = Column(Integer, default=0)
    origem = Column(String, default="cron")          # cron | consumido | manual
    status = Column(String, default="enviado")       # enviado | visualizado | convertido
    preco_cobrado = Column(Float, nullable=True)
    asaas_charge_id = Column(String, nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow)
    farmacia = relationship("Farmacia", back_populates="leads")


class Configuracao(Base):
    __tablename__ = "configuracoes"
    chave = Column(String, primary_key=True)
    valor = Column(String, nullable=False)
    descricao = Column(String, nullable=True)


class PushSub(Base):
    __tablename__ = "push_subs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    usuario_id = Column(String, ForeignKey("usuarios.telefone"), nullable=False)
    endpoint = Column(String, nullable=False, unique=True)
    p256dh = Column(String, nullable=False)
    auth = Column(String, nullable=False)
    criado_em = Column(DateTime, default=datetime.utcnow)


class AlarmeRemedio(Base):
    __tablename__ = "alarmes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    usuario_id = Column(String, ForeignKey("usuarios.telefone"), nullable=False)
    nome_med = Column(String, nullable=False)
    horario = Column(String, nullable=False)   # "HH:MM" UTC-3
    dias = Column(String, default="1,2,3,4,5,6,7")  # 1=seg … 7=dom
    ativo = Column(Integer, default=1)
    criado_em = Column(DateTime, default=datetime.utcnow)
