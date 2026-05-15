# routers/promocoes.py — /promocoes/*, /categorias, /precos/media

import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from config import CATEGORIAS_TERAPEUTICAS
from database import get_db
from helpers import enviar_email, html_interesse_promocao, precos_medios_sistema, validar_telefone
from models import AnuncioPush, Estoque, Farmacia, InteresseAnuncio, Usuario
from schemas import InteressePayload

router = APIRouter()


def _meds_match_categoria(nomes_meds: list[str], cat_id: str) -> bool:
    if cat_id == "outros":
        return True
    cat = next((c for c in CATEGORIAS_TERAPEUTICAS if c["id"] == cat_id), None)
    if not cat:
        return False
    for med in nomes_meds:
        med_l = med.lower()
        if any(kw in med_l or med_l in kw for kw in cat["kw"]):
            return True
    return False


@router.get("/categorias")
def listar_categorias():
    return [{"id": c["id"], "label": c["label"]} for c in CATEGORIAS_TERAPEUTICAS]


@router.get("/precos/media")
def precos_media(nome: str = None, db: Session = Depends(get_db)):
    medias = precos_medios_sistema(db)
    if nome:
        k = nome.lower().strip()
        matches = {
            k2: v for k2, v in medias.items()
            if k in k2 or k2 in k
        }
        return {"precos": [{"nome": k2, "media": v["media"], "amostras": v["amostras"]}
                           for k2, v in sorted(matches.items(), key=lambda x: -x[1]["amostras"])]}
    return {
        "precos": [
            {"nome": k, "media": v["media"], "amostras": v["amostras"]}
            for k, v in sorted(medias.items(), key=lambda x: x[0])
        ],
        "total_medicamentos": len(medias)
    }


@router.get("/promocoes")
def listar_promocoes(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    usuario = db.query(Usuario).filter(Usuario.telefone == tel).first()
    itens_usuario = db.query(Estoque).filter(
        Estoque.usuario_id == tel, Estoque.status != "consumido"
    ).all()
    nomes_meds = [i.nome_medicamento for i in itens_usuario]

    hoje = date.today()
    anuncios = db.query(AnuncioPush).filter(AnuncioPush.status == "disparado").all()

    result = []
    for a in anuncios:
        if a.data_expiracao and a.data_expiracao < hoje:
            continue

        if a.publico == "bairro":
            farmacia = db.query(Farmacia).filter(Farmacia.id == a.farmacia_id).first()
            if farmacia and farmacia.bairros:
                bairros_f = {b.strip().lower() for b in farmacia.bairros.split(",") if b.strip()}
                if bairros_f and (not usuario or not usuario.bairro or
                                  usuario.bairro.strip().lower() not in bairros_f):
                    continue

        if a.genero_alvo in ("M", "F"):
            if not usuario or usuario.genero != a.genero_alvo:
                continue

        cats = []
        if a.categorias:
            try:
                cats = json.loads(a.categorias)
            except Exception:
                cats = []

        if cats:
            if not any(_meds_match_categoria(nomes_meds, cat) for cat in cats):
                continue

        ja_tem = False
        if a.produto and nomes_meds:
            prod_l = a.produto.lower()
            for n in nomes_meds:
                n_l = n.lower()
                if prod_l in n_l or n_l in prod_l:
                    ja_tem = True
                    break

        interesse = db.query(InteresseAnuncio).filter(
            InteresseAnuncio.anuncio_id == a.id,
            InteresseAnuncio.usuario_id == tel,
            InteresseAnuncio.status == "ativo"
        ).first()

        farmacia = db.query(Farmacia).filter(Farmacia.id == a.farmacia_id).first()
        formas = []
        if a.formas_pagamento:
            try:
                formas = json.loads(a.formas_pagamento)
            except Exception:
                formas = []

        result.append({
            "id": a.id,
            "farmacia_nome": farmacia.nome if farmacia else "—",
            "titulo": a.titulo,
            "texto": a.texto,
            "produto": a.produto,
            "preco_de": a.preco_de,
            "preco_por": a.preco_por,
            "data_expiracao": str(a.data_expiracao) if a.data_expiracao else None,
            "tem_entrega": bool(a.tem_entrega),
            "valor_frete": a.valor_frete,
            "formas_pagamento": formas,
            "whatsapp_contato": a.whatsapp_contato,
            "categorias": cats,
            "ja_tem": ja_tem,
            "ja_interessou": bool(interesse),
            "disparado_em": str(a.disparado_em.date()) if a.disparado_em else None,
        })

    return result


@router.post("/promocoes/{anuncio_id}/interesse")
def manifestar_interesse(anuncio_id: int, payload: InteressePayload, db: Session = Depends(get_db)):
    tel = validar_telefone(payload.telefone)
    usuario = db.query(Usuario).filter(Usuario.telefone == tel).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    anuncio = db.query(AnuncioPush).filter(
        AnuncioPush.id == anuncio_id, AnuncioPush.status == "disparado"
    ).first()
    if not anuncio:
        raise HTTPException(status_code=404, detail="Promoção não encontrada.")
    if anuncio.data_expiracao and anuncio.data_expiracao < date.today():
        raise HTTPException(status_code=410, detail="Promoção expirada.")

    existente = db.query(InteresseAnuncio).filter(
        InteresseAnuncio.anuncio_id == anuncio_id,
        InteresseAnuncio.usuario_id == tel
    ).first()
    if existente:
        raise HTTPException(status_code=409, detail="Você já demonstrou interesse nesta promoção.")

    expira_em = None
    if anuncio.data_expiracao:
        d = anuncio.data_expiracao
        expira_em = datetime(d.year, d.month, d.day) + timedelta(days=7)

    interesse = InteresseAnuncio(
        anuncio_id=anuncio_id, usuario_id=tel,
        expira_em=expira_em, status="ativo"
    )
    db.add(interesse)
    db.commit()

    farmacia = db.query(Farmacia).filter(Farmacia.id == anuncio.farmacia_id).first()
    if farmacia and farmacia.email:
        produto = anuncio.produto or anuncio.titulo or "promoção"
        enviar_email(
            farmacia.email,
            f"DoseMed — Interesse: {produto}",
            html_interesse_promocao(
                farmacia.nome, usuario.nome, usuario.bairro or "—",
                tel, produto, anuncio.preco_por or anuncio.preco_de
            )
        )

    return {"ok": True, "mensagem": "Interesse registrado! A farmácia entrará em contato pelo WhatsApp."}


@router.get("/promocoes/historico/{telefone}")
def historico_promocoes(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    interesses = db.query(InteresseAnuncio).filter(
        InteresseAnuncio.usuario_id == tel
    ).order_by(InteresseAnuncio.criado_em.desc()).all()

    result = []
    hoje = date.today()
    for i in interesses:
        a = db.query(AnuncioPush).filter(AnuncioPush.id == i.anuncio_id).first()
        farmacia = db.query(Farmacia).filter(Farmacia.id == a.farmacia_id).first() if a else None
        expirou = (i.status == "expirado") or (a and a.data_expiracao and a.data_expiracao < hoje)
        result.append({
            "id": i.id,
            "anuncio_id": i.anuncio_id,
            "farmacia_nome": farmacia.nome if farmacia else "—",
            "farmacia_whatsapp": a.whatsapp_contato if a else None,
            "produto": a.produto if a else None,
            "titulo": a.titulo if a else None,
            "preco_por": a.preco_por if a else None,
            "data_expiracao": str(a.data_expiracao) if a and a.data_expiracao else None,
            "status": "expirado" if expirou else i.status,
            "criado_em": str(i.criado_em.date()) if i.criado_em else None,
            "expirou": bool(expirou),
        })
    return result
