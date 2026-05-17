# routers/alarmes.py — /alarmes/*

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db, get_usuario_autenticado
from helpers import sanitizar, validar_telefone
from models import AlarmeRemedio, Usuario
from schemas import AlarmePayload

router = APIRouter()


@router.get("/alarmes/{telefone}")
def listar_alarmes(telefone: str, usuario_auth: Usuario = Depends(get_usuario_autenticado), db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    if usuario_auth.telefone != telefone:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    alarmes = db.query(AlarmeRemedio).filter(AlarmeRemedio.usuario_id == telefone).all()
    return [{"id": a.id, "nome_med": a.nome_med, "horario": a.horario,
             "dias": a.dias, "ativo": bool(a.ativo),
             "dias_tratamento": a.dias_tratamento,
             "criado_em": a.criado_em.strftime("%Y-%m-%d") if a.criado_em else None} for a in alarmes]


@router.post("/alarmes")
def criar_alarme(payload: AlarmePayload, db: Session = Depends(get_db)):
    telefone = validar_telefone(payload.telefone)
    if not re.match(r"^\d{2}:\d{2}$", payload.horario):
        raise HTTPException(status_code=400, detail="Horário inválido. Use HH:MM.")
    a = AlarmeRemedio(usuario_id=telefone, nome_med=sanitizar(payload.nome_med),
                      horario=payload.horario, dias=payload.dias or "1,2,3,4,5,6,7", ativo=1,
                      dias_tratamento=payload.dias_tratamento)
    db.add(a)
    db.commit()
    return {"id": a.id, "nome_med": a.nome_med, "horario": a.horario,
            "dias": a.dias, "ativo": True,
            "dias_tratamento": a.dias_tratamento,
            "criado_em": a.criado_em.strftime("%Y-%m-%d") if a.criado_em else None}


@router.put("/alarmes/{alarme_id}")
def editar_alarme(alarme_id: int, payload: AlarmePayload, usuario_auth: Usuario = Depends(get_usuario_autenticado), db: Session = Depends(get_db)):
    a = db.query(AlarmeRemedio).filter(AlarmeRemedio.id == alarme_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Alarme não encontrado.")
    if a.usuario_id != usuario_auth.telefone:
        raise HTTPException(status_code=403, detail="Acesso negado.")
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
    if payload.dias_tratamento is not None:
        a.dias_tratamento = payload.dias_tratamento
    db.commit()
    return {"id": a.id, "nome_med": a.nome_med, "horario": a.horario,
            "dias": a.dias, "ativo": bool(a.ativo),
            "dias_tratamento": a.dias_tratamento,
            "criado_em": a.criado_em.strftime("%Y-%m-%d") if a.criado_em else None}


@router.delete("/alarmes/{alarme_id}")
def excluir_alarme(alarme_id: int, usuario_auth: Usuario = Depends(get_usuario_autenticado), db: Session = Depends(get_db)):
    a = db.query(AlarmeRemedio).filter(AlarmeRemedio.id == alarme_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Alarme não encontrado.")
    if a.usuario_id != usuario_auth.telefone:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    db.delete(a)
    db.commit()
    return {"ok": True}
