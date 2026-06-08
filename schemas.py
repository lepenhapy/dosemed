# schemas.py — all Pydantic payload models

from typing import Optional
from pydantic import BaseModel


class UsuarioPayload(BaseModel):
    telefone: str
    nome: str
    email: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    data_nascimento: Optional[str] = None  # YYYY-MM-DD
    endereco_completo: Optional[str] = None
    instrucoes_portaria: Optional[str] = None
    aceite_lgpd: Optional[bool] = False
    genero: Optional[str] = None  # M | F


class WebhookPayload(BaseModel):
    telefone: str
    medicamento: str
    principio_ativo: Optional[str] = None
    miligramas: Optional[str] = None
    fabricante: Optional[str] = None
    validade: Optional[str] = None
    quantidade: Optional[int] = 1
    manipulado: Optional[bool] = False
    uso_continuo: Optional[bool] = False
    preco_real: Optional[float] = None
    nome_usuario: Optional[str] = None
    posologia: Optional[str] = None


class EditarUsuarioPayload(BaseModel):
    nome: Optional[str] = None
    email: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    data_nascimento: Optional[str] = None  # YYYY-MM-DD
    endereco_completo: Optional[str] = None
    instrucoes_portaria: Optional[str] = None
    genero: Optional[str] = None  # M | F


class RecuperarPinPayload(BaseModel):
    telefone: str


class VerificarCodigoPayload(BaseModel):
    telefone: str
    codigo: str
    novo_pin: str


class EditarItemPayload(BaseModel):
    nome_medicamento: Optional[str] = None
    principio_ativo: Optional[str] = None
    uso_continuo: Optional[bool] = None
    miligramas: Optional[str] = None
    fabricante: Optional[str] = None
    validade: Optional[str] = None
    quantidade: Optional[int] = None
    manipulado: Optional[bool] = None
    preco_real: Optional[float] = None
    posologia: Optional[str] = None


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


class ExcluirContaPayload(BaseModel):
    pin: str
    confirmacao: str   # deve ser exatamente "EXCLUIR"


class OrcamentoRespostaPayload(BaseModel):
    preco: float
    prazo_entrega: str
    formas_pagamento: str  # ex: "pix,cartao,dinheiro"


class AnuncioCriarPayload(BaseModel):
    telefone: str
    pin: str
    texto: str
    servico: Optional[str] = None  # ID do serviço clínico (ex: "pressao", "glicose")
    titulo: Optional[str] = None
    publico: str = "bairro"    # bairro | todos
    genero_alvo: Optional[str] = None  # M | F | None
    produto: Optional[str] = None
    preco_de: Optional[float] = None
    preco_por: Optional[float] = None
    data_expiracao: Optional[str] = None   # YYYY-MM-DD
    tem_entrega: Optional[bool] = False
    valor_frete: Optional[float] = None
    formas_pagamento: Optional[str] = None  # JSON: ["pix","cartao","dinheiro"]
    whatsapp_contato: Optional[str] = None
    categorias: Optional[str] = None        # JSON: ["diabetes","hipertensao"]
    faixa_etaria: Optional[str] = None      # todos | 18-35 | 36-55 | 56+


class InteressePayload(BaseModel):
    telefone: str


class ConfirmarChegadaPayload(BaseModel):
    pedido_id: int
    validade: Optional[str] = None


class FeedbackPayload(BaseModel):
    usuario_id: Optional[str] = None
    nota: int                    # 1–5
    categoria: str               # sugestao | elogio | reclamacao | bug
    mensagem: str
    cidade: Optional[str] = None
    bairro: Optional[str] = None


class EntregaPayload(BaseModel):
    telefone: str
    entregue: bool
    motivo: Optional[str] = None


class AvaliarFarmaciaPayload(BaseModel):
    telefone: str
    rating: int  # 1–5


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
    dias_tratamento: Optional[int] = None


class FarmaciaSetPinPayload(BaseModel):
    telefone: str
    pin: str


class FarmaciaPerfilPayload(BaseModel):
    bairros: Optional[str] = None
    atende_manipulado: Optional[int] = None
    servicos: Optional[str] = None  # JSON: ["pressao","glicose"]


class FarmaciaCadastroPayload(BaseModel):
    telefone: str
    nome: str
    email: str
    cnpj: Optional[str] = None
    bairros: Optional[str] = None
    atende_manipulado: Optional[int] = 0


class FarmaciaPayload(BaseModel):
    nome: str
    cnpj: Optional[str] = None
    email: str
    telefone_contato: Optional[str] = None
    bairros: Optional[str] = None
    plano: Optional[str] = "lead"
    ativo: Optional[int] = 1
    atende_manipulado: Optional[int] = 0
    origem: Optional[str] = None
