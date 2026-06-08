# routers/auth.py — /usuario CRUD, /auth/*, /notificacoes/*

import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from config import ADMIN_PHONE, PIN_MAX_TENTATIVAS, PIN_BLOQUEIO_MINUTOS
from database import get_db, get_usuario_autenticado, limiter
from helpers import (
    validar_telefone, validar_email, sanitizar, hash_pin,
    gerar_codigo_recuperacao, enviar_email, html_recuperacao,
    html_vencimento, atualizar_status_estoque, serializar_item,
)
from datetime import date as _date
from models import (
    Usuario, Farmacia, CodigoRecuperacao, Estoque, LogEvento,
    LogBusca, OrcamentoSolicitacao, OrcamentoResposta, PushSub,
    AlarmeRemedio, Pedido,
)
from schemas import (
    UsuarioPayload, EditarUsuarioPayload, ExcluirContaPayload,
    PinPayload, RecuperarPinPayload, VerificarCodigoPayload,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Usuario CRUD
# ---------------------------------------------------------------------------

@router.get("/usuario/{telefone}")
def get_usuario(telefone: str, db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    session_token = None
    if not usuario.pin:
        # Reutiliza token existente para não invalidar sessão ativa
        if not usuario.session_token:
            session_token = secrets.token_urlsafe(32)
            usuario.session_token = session_token
            db.commit()
        else:
            session_token = usuario.session_token
    return {
        "telefone": usuario.telefone,
        "nome": usuario.nome,
        "email": usuario.email,
        "bairro": usuario.bairro,
        "cidade": usuario.cidade,
        "data_nascimento": str(usuario.data_nascimento) if usuario.data_nascimento else None,
        "genero": usuario.genero,
        "endereco": usuario.endereco_completo,
        "instrucoes_portaria": usuario.instrucoes_portaria,
        "tem_pin": bool(usuario.pin),
        "tem_email": bool(usuario.email),
        "aceite_lgpd": bool(usuario.aceite_lgpd),
        "session_token": session_token,
    }


@router.post("/usuario")
@limiter.limit("10/hour")
async def criar_usuario(request: Request, payload: UsuarioPayload, db: Session = Depends(get_db)):
    telefone = validar_telefone(payload.telefone)
    if db.query(Usuario).filter(Usuario.telefone == telefone).first():
        raise HTTPException(status_code=409, detail="Telefone já cadastrado.")
    email = None
    if payload.email:
        email_val = payload.email.strip().lower()
        if not validar_email(email_val):
            raise HTTPException(status_code=400, detail="E-mail inválido.")
        email = email_val
    genero = payload.genero if payload.genero in ("M", "F") else None
    data_nasc = None
    if payload.data_nascimento:
        try:
            data_nasc = _date.fromisoformat(payload.data_nascimento)
        except ValueError:
            pass
    usuario = Usuario(
        telefone=telefone,
        nome=sanitizar(payload.nome),
        email=email,
        bairro=sanitizar(payload.bairro or ""),
        cidade=sanitizar(payload.cidade or ""),
        data_nascimento=data_nasc,
        endereco_completo=sanitizar(payload.endereco_completo or ""),
        instrucoes_portaria=sanitizar(payload.instrucoes_portaria or ""),
        aceite_lgpd=datetime.now(timezone.utc).replace(tzinfo=None) if payload.aceite_lgpd else None,
        genero=genero
    )
    token = secrets.token_urlsafe(32)
    usuario.session_token = token
    db.add(usuario)
    db.add(LogEvento(tipo="cadastro_usuario"))
    db.commit()
    import logging
    logging.getLogger("dosemed").info(f"Novo usuário: {telefone}")
    return {"mensagem": "Usuário cadastrado com sucesso!", "telefone": telefone, "session_token": token}


@router.put("/usuario/{telefone}")
def editar_usuario(telefone: str, payload: EditarUsuarioPayload, usuario_auth: Usuario = Depends(get_usuario_autenticado), db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    if usuario_auth.telefone != telefone:
        raise HTTPException(status_code=403, detail="Acesso negado.")
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
    if payload.cidade is not None:
        usuario.cidade = sanitizar(payload.cidade)
    if payload.data_nascimento is not None:
        try:
            usuario.data_nascimento = _date.fromisoformat(payload.data_nascimento) if payload.data_nascimento else None
        except ValueError:
            pass
    if payload.endereco_completo is not None:
        usuario.endereco_completo = sanitizar(payload.endereco_completo)
    if payload.instrucoes_portaria is not None:
        usuario.instrucoes_portaria = sanitizar(payload.instrucoes_portaria)
    if payload.genero in ("M", "F"):
        usuario.genero = payload.genero
    elif payload.genero == "":
        usuario.genero = None
    db.commit()
    return {"mensagem": "Perfil atualizado com sucesso."}


@router.delete("/usuario/{telefone}")
def excluir_usuario(telefone: str, payload: ExcluirContaPayload, db: Session = Depends(get_db)):
    """Exclusão definitiva da conta do usuário e todos os seus dados (LGPD art. 18)."""
    telefone = validar_telefone(telefone)
    if payload.confirmacao != "EXCLUIR":
        raise HTTPException(status_code=400, detail="Confirmação inválida. Digite exatamente: EXCLUIR")

    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    pin = re.sub(r"\D", "", payload.pin)
    if usuario.pin and hash_pin(pin, telefone) != usuario.pin:
        raise HTTPException(status_code=401, detail="PIN incorreto.")

    db.query(LogBusca).filter(LogBusca.usuario_id == telefone).update({"usuario_id": None})

    sols = db.query(OrcamentoSolicitacao).filter(OrcamentoSolicitacao.usuario_id == telefone).all()
    for sol in sols:
        db.query(OrcamentoResposta).filter(OrcamentoResposta.solicitacao_id == sol.id).delete()
    db.query(OrcamentoSolicitacao).filter(OrcamentoSolicitacao.usuario_id == telefone).delete()
    db.query(PushSub).filter(PushSub.usuario_id == telefone).delete()
    db.query(AlarmeRemedio).filter(AlarmeRemedio.usuario_id == telefone).delete()
    db.query(Estoque).filter(Estoque.usuario_id == telefone).delete()
    db.query(Pedido).filter(Pedido.usuario_id == telefone).delete()
    db.query(CodigoRecuperacao).filter(CodigoRecuperacao.telefone == telefone).delete()
    db.delete(usuario)
    db.add(LogEvento(tipo="exclusao_usuario"))
    db.commit()

    import logging
    logging.getLogger("dosemed").info(f"Conta excluída (LGPD): {telefone}")
    return {"ok": True, "mensagem": "Conta excluída com sucesso. Todos os seus dados foram removidos."}


@router.post("/usuario/{telefone}/aceitar-lgpd")
def aceitar_lgpd(telefone: str, db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    usuario.aceite_lgpd = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Auth / PIN
# ---------------------------------------------------------------------------

@router.post("/auth/definir-pin")
def definir_pin(payload: PinPayload, db: Session = Depends(get_db)):
    telefone = validar_telefone(payload.telefone)
    pin = re.sub(r"\D", "", payload.pin)
    if not (4 <= len(pin) <= 6):
        raise HTTPException(status_code=400, detail="PIN deve ter 4 a 6 dígitos.")
    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    if usuario.pin:
        raise HTTPException(status_code=400, detail="PIN já definido. Use a recuperação de PIN para alterá-lo.")
    usuario.pin = hash_pin(pin, telefone)
    token = secrets.token_urlsafe(32)
    usuario.session_token = token
    db.commit()
    return {"mensagem": "PIN definido com sucesso.", "session_token": token}


@router.post("/auth/verificar-pin")
@limiter.limit("30/minute")
async def verificar_pin(request: Request, payload: PinPayload, db: Session = Depends(get_db)):
    digitos = re.sub(r"\D", "", payload.telefone)
    pin = re.sub(r"\D", "", payload.pin)

    agora = datetime.now(timezone.utc).replace(tzinfo=None)

    # --- Farmácia ---
    farmacia = db.query(Farmacia).filter(Farmacia.telefone_contato == digitos).first()
    if farmacia:
        if farmacia.pin:
            bloqueado_ate = getattr(farmacia, "pin_bloqueado_ate", None)
            if bloqueado_ate and bloqueado_ate > agora:
                restam = int((bloqueado_ate - agora).total_seconds() / 60) + 1
                raise HTTPException(status_code=429, detail=f"Conta bloqueada. Tente novamente em {restam} minuto(s).")
            if hash_pin(pin, digitos) != farmacia.pin:
                tentativas = (getattr(farmacia, "pin_tentativas", 0) or 0) + 1
                try:
                    farmacia.pin_tentativas = tentativas
                    if tentativas >= PIN_MAX_TENTATIVAS:
                        farmacia.pin_bloqueado_ate = agora + timedelta(minutes=PIN_BLOQUEIO_MINUTOS)
                        farmacia.pin_tentativas = 0
                        db.commit()
                        raise HTTPException(status_code=429, detail=f"Muitas tentativas. Conta bloqueada por {PIN_BLOQUEIO_MINUTOS} minutos.")
                    db.commit()
                except HTTPException:
                    raise
                except Exception:
                    db.rollback()
                restam = PIN_MAX_TENTATIVAS - tentativas
                raise HTTPException(status_code=401, detail=f"PIN incorreto. {restam} tentativa(s) restante(s).")
            try:
                farmacia.pin_tentativas = 0
                farmacia.pin_bloqueado_ate = None
            except Exception:
                pass
        token = secrets.token_urlsafe(32)
        farmacia.session_token = token
        db.commit()
        return {
            "ok": True, "tipo": "farmacia",
            "farmacia_id": farmacia.id, "farmacia_nome": farmacia.nome,
            "session_token": token,
        }

    # --- Usuário ---
    telefone = validar_telefone(payload.telefone)
    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    if not usuario.pin:
        token = secrets.token_urlsafe(32)
        usuario.session_token = token
        db.commit()
        return {"ok": True, "tipo": "usuario", "mensagem": "Sem PIN definido.", "session_token": token}

    bloqueado_ate = getattr(usuario, "pin_bloqueado_ate", None)
    if bloqueado_ate and bloqueado_ate > agora:
        restam = int((bloqueado_ate - agora).total_seconds() / 60) + 1
        raise HTTPException(status_code=429, detail=f"Conta bloqueada. Tente novamente em {restam} minuto(s).")

    if hash_pin(pin, telefone) != usuario.pin:
        tentativas = (getattr(usuario, "pin_tentativas", 0) or 0) + 1
        try:
            usuario.pin_tentativas = tentativas
            if tentativas >= PIN_MAX_TENTATIVAS:
                usuario.pin_bloqueado_ate = agora + timedelta(minutes=PIN_BLOQUEIO_MINUTOS)
                usuario.pin_tentativas = 0
                db.commit()
                raise HTTPException(status_code=429, detail=f"Muitas tentativas. Conta bloqueada por {PIN_BLOQUEIO_MINUTOS} minutos.")
            db.commit()
        except HTTPException:
            raise
        except Exception:
            db.rollback()
        restam = PIN_MAX_TENTATIVAS - tentativas
        raise HTTPException(status_code=401, detail=f"PIN incorreto. {restam} tentativa(s) restante(s).")

    usuario.pin_tentativas = 0
    usuario.pin_bloqueado_ate = None
    token = secrets.token_urlsafe(32)
    usuario.session_token = token
    db.commit()
    return {"ok": True, "tipo": "usuario", "mensagem": "PIN verificado.", "session_token": token}


@router.post("/auth/recuperar-pin")
@limiter.limit("3/hour")
async def recuperar_pin(request: Request, payload: RecuperarPinPayload, db: Session = Depends(get_db)):
    telefone = validar_telefone(payload.telefone)
    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    if not usuario.email:
        raise HTTPException(status_code=400, detail="Nenhum e-mail cadastrado. Informe seu e-mail no perfil para usar esta função.")

    db.query(CodigoRecuperacao).filter(
        CodigoRecuperacao.telefone == telefone,
        CodigoRecuperacao.usado == 0
    ).update({"usado": 1})
    db.commit()

    codigo = gerar_codigo_recuperacao()
    expira = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=15)
    db.add(CodigoRecuperacao(telefone=telefone, codigo=codigo, expira_em=expira))
    db.commit()

    enviado, email_erro = enviar_email(
        usuario.email,
        "DoseMed — Código para redefinir seu PIN",
        html_recuperacao(usuario.nome, codigo)
    )

    if not enviado:
        smtp_user = os.getenv("SMTP_USER", "")
        smtp_pass = os.getenv("SMTP_PASSWORD", "")
        smtp_real = smtp_user and smtp_user != "seuemail@gmail.com" and smtp_pass and smtp_pass != "suasenhadoapp"
        if not smtp_real:
            return {"mensagem": f"[DEV] Código: {codigo}", "codigo": codigo, "email_mascarado": usuario.email}
        raise HTTPException(status_code=503, detail=f"Erro ao enviar e-mail: {email_erro}")

    partes = usuario.email.split("@")
    mascarado = partes[0][:2] + "***@" + partes[1]
    return {"mensagem": f"Código enviado para {mascarado}. Válido por 15 minutos.", "email_mascarado": mascarado}


@router.post("/auth/verificar-codigo")
def verificar_codigo(payload: VerificarCodigoPayload, db: Session = Depends(get_db)):
    telefone = validar_telefone(payload.telefone)
    pin = re.sub(r"\D", "", payload.novo_pin)
    if len(pin) < 4:
        raise HTTPException(status_code=400, detail="Novo PIN deve ter ao menos 4 dígitos.")

    agora = datetime.now(timezone.utc).replace(tzinfo=None)
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


# ---------------------------------------------------------------------------
# Notificações por e-mail
# ---------------------------------------------------------------------------

@router.post("/notificacoes/vencimentos")
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
        ok, _ = enviar_email(
            u.email,
            "DoseMed — Medicamentos com atenção no seu estoque",
            html_vencimento(u.nome, lista)
        )
        if ok:
            enviados += 1
        else:
            erros += 1

    import logging
    logging.getLogger("dosemed").info(f"Notificações de vencimento: {enviados} enviadas, {erros} erros")
    return {"enviados": enviados, "erros": erros, "total_usuarios_com_email": len(usuarios)}


@router.post("/notificacoes/testar-email")
def testar_email(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    usuario = db.query(Usuario).filter(Usuario.telefone == tel).first()
    if not usuario or not usuario.email:
        raise HTTPException(status_code=400, detail="Usuário não encontrado ou sem e-mail.")
    ok, email_erro = enviar_email(
        usuario.email,
        "DoseMed — Teste de e-mail",
        f"<p>Olá, <strong>{usuario.nome}</strong>! Seu e-mail está configurado corretamente no DoseMed. 💊</p>"
    )
    if ok:
        return {"mensagem": f"E-mail de teste enviado para {usuario.email}"}
    raise HTTPException(status_code=503, detail=f"Erro ao enviar e-mail: {email_erro}")
