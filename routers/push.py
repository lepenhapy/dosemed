# routers/push.py — /push/*

import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from helpers import enviar_push, validar_telefone
from models import PushSub
from schemas import PushSubPayload

router = APIRouter()


@router.get("/push/vapid-public-key")
def vapid_public_key():
    key = os.getenv("VAPID_PUBLIC_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="VAPID não configurado.")
    return {"key": key}


@router.post("/push/subscribe")
def push_subscribe(payload: PushSubPayload, db: Session = Depends(get_db)):
    telefone = validar_telefone(payload.telefone)
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


@router.post("/push/testar")
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


@router.delete("/push/subscribe")
def push_unsubscribe(telefone: str, endpoint: str, db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    db.query(PushSub).filter(
        PushSub.usuario_id == telefone,
        PushSub.endpoint == endpoint
    ).delete()
    db.commit()
    return {"ok": True}
