# routers/orcamento.py — /orcamento/*, /usuario/*/orcamentos, /confirmar-chegada, /estoque/*/solicitar-reposicao

import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from config import ADMIN_PHONE
from database import get_config, get_db
from helpers import (
    calcular_preco,
    calcular_status,
    enviar_email,
    enviar_push,
    html_orcamento_farmacia,
    html_orcamento_ganhou,
    html_orcamento_perdeu,
    sanitizar,
    validar_telefone,
)
from models import (
    Estoque,
    Farmacia,
    Lead,
    OrcamentoResposta,
    OrcamentoSolicitacao,
    Pedido,
    PushSub,
    Usuario,
)
from schemas import AvaliarFarmaciaPayload, ConfirmarChegadaPayload, EntregaPayload, OrcamentoRespostaPayload

router = APIRouter()


@router.post("/estoque/{item_id}/solicitar-reposicao")
def solicitar_reposicao(item_id: int, telefone: str, db: Session = Depends(get_db)):
    """Usuário quer reabastecer — cria solicitação de orçamento e notifica farmácias."""
    tel = validar_telefone(telefone)
    item = db.query(Estoque).filter(Estoque.id == item_id, Estoque.usuario_id == tel).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item não encontrado.")

    minutos = int(get_config(db, "minutos_disputa_lead", "30"))
    expira = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=minutos)
    sol = OrcamentoSolicitacao(
        usuario_id=tel, nome_med=item.nome_medicamento,
        quantidade_restante=item.quantidade,
        status="coletando", expira_em=expira,
    )
    db.add(sol)
    db.flush()

    usuario = db.query(Usuario).filter(Usuario.telefone == tel).first()

    max_farm = int(get_config(db, "max_farmacias_orcamento", "4"))
    farmacias = db.query(Farmacia).filter(Farmacia.ativo == 1).all()
    enviados = 0
    preco_chave = "preco_lead_manipulado" if item.manipulado else "preco_lead_comum"
    preco_lead = float(get_config(db, preco_chave, "5.00"))

    for f in farmacias:
        if enviados >= max_farm:
            break
        if usuario and usuario.bairro:
            bairros_f = [b.lower().strip() for b in (f.bairros or "").split(",") if b.strip()]
            if bairros_f and usuario.bairro.lower().strip() not in bairros_f:
                continue
        if item.manipulado and not f.atende_manipulado:
            continue

        token = secrets.token_urlsafe(16)
        db.add(OrcamentoResposta(
            solicitacao_id=sol.id, farmacia_id=f.id,
            token=token, status="pendente",
        ))
        db.add(Lead(
            farmacia_id=f.id,
            usuario_bairro=usuario.bairro if usuario else None,
            medicamento=item.nome_medicamento,
            principio_ativo=item.principio_ativo,
            miligramas=item.miligramas,
            manipulado=item.manipulado or 0,
            origem="consumido", status="enviado",
            preco_cobrado=preco_lead,
        ))
        if f.email:
            html = html_orcamento_farmacia(
                f.nome, usuario.bairro if usuario else "—",
                item.nome_medicamento, item.principio_ativo or "",
                item.miligramas or "", bool(item.manipulado), token,
            )
            enviar_email(f.email, f"DoseMed — Orçamento: {item.nome_medicamento}", html)
        enviados += 1

    db.commit()
    return {
        "ok": True, "solicitacao_id": sol.id,
        "mensagem": f"Solicitação enviada para {enviados} farmácia(s). Aguarde os orçamentos." if enviados
                    else "Solicitação registrada. Entraremos em contato em breve.",
    }


@router.get("/orcamento/{token}")
def ver_orcamento(token: str, db: Session = Depends(get_db)):
    resp = db.query(OrcamentoResposta).filter(OrcamentoResposta.token == token).first()
    if not resp:
        raise HTTPException(status_code=404, detail="Orçamento não encontrado.")
    sol = resp.solicitacao
    f = resp.farmacia
    expirou = bool(sol.expira_em and datetime.now(timezone.utc).replace(tzinfo=None) > sol.expira_em)
    return {
        "token": token,
        "farmacia_nome": f.nome,
        "medicamento": sol.nome_med,
        "quantidade": sol.quantidade_restante,
        "status_resposta": resp.status,
        "status_solicitacao": sol.status,
        "expirou": expirou,
        "preco": resp.preco,
        "prazo_entrega": resp.prazo_entrega,
        "formas_pagamento": resp.formas_pagamento,
        "respondido_em": str(resp.respondido_em) if resp.respondido_em else None,
    }


@router.post("/orcamento/{token}/responder")
def responder_orcamento(token: str, payload: OrcamentoRespostaPayload, db: Session = Depends(get_db)):
    resp = db.query(OrcamentoResposta).filter(OrcamentoResposta.token == token).first()
    if not resp:
        raise HTTPException(status_code=404, detail="Orçamento não encontrado.")
    if resp.status != "pendente":
        raise HTTPException(status_code=400, detail="Este orçamento já foi respondido ou encerrado.")
    sol = resp.solicitacao
    if sol.status not in ("coletando", "aguardando_usuario"):
        raise HTTPException(status_code=400, detail="Solicitação encerrada.")
    if sol.expira_em and datetime.now(timezone.utc).replace(tzinfo=None) > sol.expira_em:
        resp.status = "expirou"
        db.commit()
        raise HTTPException(status_code=410, detail="Prazo de resposta expirado.")
    if payload.preco <= 0:
        raise HTTPException(status_code=400, detail="Preço deve ser maior que zero.")

    resp.preco = payload.preco
    resp.prazo_entrega = sanitizar(payload.prazo_entrega)[:100]
    resp.formas_pagamento = payload.formas_pagamento[:100]
    resp.status = "respondido"
    resp.respondido_em = datetime.now(timezone.utc).replace(tzinfo=None)
    sol.status = "aguardando_usuario"
    db.commit()

    subs = db.query(PushSub).filter(PushSub.usuario_id == sol.usuario_id).all()
    usuario = db.query(Usuario).filter(Usuario.telefone == sol.usuario_id).first()
    nome = usuario.nome.split()[0] if usuario else "você"
    f = resp.farmacia
    for sub in subs:
        enviar_push(sub, "💊 Orçamento recebido!",
                    f"{nome}, {f.nome} enviou uma proposta para {sol.nome_med}. Veja agora!")

    return {"ok": True, "mensagem": "Orçamento enviado! O paciente será notificado."}


@router.get("/usuario/{telefone}/orcamentos")
def listar_orcamentos(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    sols = db.query(OrcamentoSolicitacao).filter(
        OrcamentoSolicitacao.usuario_id == tel
    ).order_by(OrcamentoSolicitacao.criado_em.desc()).limit(20).all()

    result = []
    for sol in sols:
        respostas = db.query(OrcamentoResposta).filter(
            OrcamentoResposta.solicitacao_id == sol.id,
            OrcamentoResposta.status.in_(["respondido", "ganhou", "perdeu"])
        ).order_by(OrcamentoResposta.preco).all()
        result.append({
            "id": sol.id,
            "medicamento": sol.nome_med,
            "status": sol.status,
            "entregue": sol.entregue,
            "modalidade": sol.modalidade,
            "avaliacao": sol.avaliacao,
            "criado_em": str(sol.criado_em.date()) if sol.criado_em else None,
            "expira_em": str(sol.expira_em) if sol.expira_em else None,
            "total_pendente": db.query(OrcamentoResposta).filter(
                OrcamentoResposta.solicitacao_id == sol.id,
                OrcamentoResposta.status == "pendente"
            ).count(),
            "respostas": [{
                "id": r.id,
                "farmacia_nome": r.farmacia.nome,
                "farmacia_nota": round(r.farmacia.rating_total / r.farmacia.rating_count, 1)
                                 if r.farmacia.rating_count else None,
                "preco": r.preco,
                "prazo_entrega": r.prazo_entrega,
                "formas_pagamento": r.formas_pagamento,
                "status": r.status,
            } for r in respostas],
        })
    return result


@router.post("/orcamento/{solicitacao_id}/escolher/{resposta_id}")
def escolher_farmacia(solicitacao_id: int, resposta_id: int, telefone: str,
                      modalidade: str = "entrega", db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    sol = db.query(OrcamentoSolicitacao).filter(
        OrcamentoSolicitacao.id == solicitacao_id,
        OrcamentoSolicitacao.usuario_id == tel
    ).first()
    if not sol:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    if sol.status != "aguardando_usuario":
        raise HTTPException(status_code=400, detail="Solicitação não está aguardando escolha.")

    vencedora = db.query(OrcamentoResposta).filter(
        OrcamentoResposta.id == resposta_id,
        OrcamentoResposta.solicitacao_id == solicitacao_id,
        OrcamentoResposta.status == "respondido"
    ).first()
    if not vencedora:
        raise HTTPException(status_code=404, detail="Resposta não encontrada.")

    vencedora.status = "ganhou"
    db.query(OrcamentoResposta).filter(
        OrcamentoResposta.solicitacao_id == solicitacao_id,
        OrcamentoResposta.id != resposta_id,
        OrcamentoResposta.status == "respondido"
    ).update({"status": "perdeu"})
    sol.status = "fechado"
    sol.modalidade = modalidade if modalidade in ("entrega", "retirada") else "entrega"
    db.commit()

    f = vencedora.farmacia
    usuario = db.query(Usuario).filter(Usuario.telefone == sol.usuario_id).first()
    bairro = usuario.bairro if usuario else "—"

    if f.email:
        enviar_email(f.email, f"DoseMed — Você foi escolhido! {sol.nome_med}",
                     html_orcamento_ganhou(f.nome, sol.nome_med, bairro,
                                           vencedora.preco, vencedora.prazo_entrega or "—",
                                           vencedora.formas_pagamento or "—"))
    if f.telefone_contato:
        for sub in db.query(PushSub).filter(PushSub.usuario_id == f.telefone_contato).all():
            enviar_push(sub, "🏆 Você foi escolhido!",
                        f"{sol.nome_med} — cliente em {bairro} escolheu sua proposta. Prepare o pedido!")

    perdedoras = db.query(OrcamentoResposta).filter(
        OrcamentoResposta.solicitacao_id == solicitacao_id,
        OrcamentoResposta.status == "perdeu"
    ).all()
    for p in perdedoras:
        if p.farmacia.email:
            enviar_email(p.farmacia.email, f"DoseMed — Resultado do orçamento: {sol.nome_med}",
                         html_orcamento_perdeu(p.farmacia.nome, sol.nome_med))

    return {"ok": True, "farmacia_nome": f.nome,
            "mensagem": f"{f.nome} foi escolhida! Aguarde o contato da farmácia."}


@router.post("/orcamento/{sol_id}/confirmar-entrega")
def confirmar_entrega_orcamento(sol_id: int, payload: EntregaPayload, db: Session = Depends(get_db)):
    tel = validar_telefone(payload.telefone)
    sol = db.query(OrcamentoSolicitacao).filter(
        OrcamentoSolicitacao.id == sol_id,
        OrcamentoSolicitacao.usuario_id == tel
    ).first()
    if not sol:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    if sol.status != "fechado":
        raise HTTPException(status_code=400, detail="Solicitação não está fechada.")

    sol.entregue = 1 if payload.entregue else 0
    db.commit()

    if not payload.entregue:
        vencedora = db.query(OrcamentoResposta).filter(
            OrcamentoResposta.solicitacao_id == sol_id,
            OrcamentoResposta.status == "ganhou"
        ).first()
        farmacia_nome = vencedora.farmacia.nome if vencedora else "farmácia desconhecida"
        motivo = sanitizar(payload.motivo or "Sem motivo informado")[:200]
        admin_subs = db.query(PushSub).filter(PushSub.usuario_id == ADMIN_PHONE).all()
        for sub in admin_subs:
            enviar_push(sub, "⚠️ Problema de entrega",
                        f"{sol.nome_med} — {farmacia_nome}: {motivo}")
        import logging
        logging.getLogger("dosemed").warning(f"Entrega NÃO confirmada: sol_id={sol_id}, farmacia={farmacia_nome}, motivo={motivo}")

    return {"ok": True}


@router.post("/orcamento/{sol_id}/avaliar-farmacia")
def avaliar_farmacia(sol_id: int, payload: AvaliarFarmaciaPayload, db: Session = Depends(get_db)):
    tel = validar_telefone(payload.telefone)
    sol = db.query(OrcamentoSolicitacao).filter(
        OrcamentoSolicitacao.id == sol_id,
        OrcamentoSolicitacao.usuario_id == tel
    ).first()
    if not sol:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    if sol.status != "fechado" or sol.entregue != 1:
        raise HTTPException(status_code=400, detail="Só é possível avaliar após confirmar a entrega.")
    if sol.avaliacao:
        raise HTTPException(status_code=400, detail="Esta entrega já foi avaliada.")
    if not 1 <= payload.rating <= 5:
        raise HTTPException(status_code=400, detail="Nota deve ser entre 1 e 5.")

    vencedora = db.query(OrcamentoResposta).filter(
        OrcamentoResposta.solicitacao_id == sol_id,
        OrcamentoResposta.status == "ganhou"
    ).first()
    if not vencedora:
        raise HTTPException(status_code=400, detail="Farmácia vencedora não encontrada.")

    sol.avaliacao = payload.rating
    f = vencedora.farmacia
    f.rating_total = (f.rating_total or 0.0) + payload.rating
    f.rating_count = (f.rating_count or 0) + 1
    db.commit()
    return {"ok": True, "mensagem": "Avaliação registrada! Obrigado pelo feedback."}


@router.post("/confirmar-chegada")
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
            preco_estimado=preco, status=calcular_status(data_val),
            quantidade=1, manipulado=0, iniciado=0,
            principio_ativo=None, miligramas=None, fabricante=None, preco_real=None,
        )
        db.add(item)
        db.commit()
        return {"mensagem": "Entrega confirmada e estoque atualizado!"}
    return {"mensagem": "Entrega confirmada. Informe a validade para atualizar o estoque."}
