# routers/admin.py — /admin/* (relatorios, crescimento, reset, insights, leads, configuracoes, feedbacks, anuncios)

import logging
import re
from datetime import datetime, timedelta, date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from config import ADMIN_PHONE, TEST_PHONES
from database import get_config, get_db
from helpers import (
    gerar_leads_para_item,
    validar_telefone,
)
from models import (
    AnuncioPush,
    AlarmeRemedio,
    Configuracao,
    CodigoRecuperacao,
    Estoque,
    Farmacia,
    Feedback,
    InteresseAnuncio,
    Lead,
    LogBusca,
    LogEvento,
    OrcamentoResposta,
    OrcamentoSolicitacao,
    Pedido,
    PushSub,
    Usuario,
)
from routers.farmacia import _disparar_anuncio_push, serializar_farmacia

logger = logging.getLogger("dosemed")

router = APIRouter()


@router.get("/admin/relatorios")
def admin_relatorios(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    logs = db.query(LogBusca).all()

    por_tipo: dict[str, int] = {}
    for l in logs:
        por_tipo[l.tipo] = por_tipo.get(l.tipo, 0) + 1

    contagem_termos: dict[str, int] = {}
    for l in logs:
        t = l.termo.lower()
        contagem_termos[t] = contagem_termos.get(t, 0) + 1
    top_termos = sorted(contagem_termos.items(), key=lambda x: -x[1])[:15]

    por_estado: dict[str, int] = {}
    for l in logs:
        e = l.estado or "??"
        por_estado[e] = por_estado.get(e, 0) + 1
    top_estados = sorted(por_estado.items(), key=lambda x: -x[1])[:10]

    por_dia: dict[str, int] = {}
    cutoff = datetime.utcnow().date() - timedelta(days=30)
    for l in logs:
        if l.timestamp and l.timestamp.date() >= cutoff:
            d = str(l.timestamp.date())
            por_dia[d] = por_dia.get(d, 0) + 1

    usuarios_unicos = len(set(l.usuario_id for l in logs if l.usuario_id))

    total_usuarios = db.query(Usuario).count()
    usuarios_reais = db.query(Usuario).filter(Usuario.telefone.notin_(TEST_PHONES)).count()
    total_itens = db.query(Estoque).filter(Estoque.status != "consumido").count()

    agora = datetime.utcnow()
    corte_7d  = agora - timedelta(days=7)
    corte_30d = agora - timedelta(days=30)
    novos_7d  = db.query(Usuario).filter(Usuario.aceite_lgpd >= corte_7d).count()
    novos_30d = db.query(Usuario).filter(Usuario.aceite_lgpd >= corte_30d).count()

    usuarios_todos = db.query(Usuario).all()
    bairro_cnt: dict[str, int] = {}
    for u in usuarios_todos:
        b = (u.bairro or "").strip() or "Não informado"
        bairro_cnt[b] = bairro_cnt.get(b, 0) + 1
    por_bairro = [{"bairro": b, "count": c}
                  for b, c in sorted(bairro_cnt.items(), key=lambda x: -x[1])]

    farmacias_todas = db.query(Farmacia).all()
    farm_por_plano: dict[str, int] = {}
    farm_por_origem: dict[str, int] = {}
    farm_bairros: dict[str, int] = {}
    for f in farmacias_todas:
        farm_por_plano[f.plano or "lead"] = farm_por_plano.get(f.plano or "lead", 0) + 1
        org = f.origem or "admin"
        farm_por_origem[org] = farm_por_origem.get(org, 0) + 1
        for b in (f.bairros or "").split(","):
            b = b.strip()
            if b:
                farm_bairros[b] = farm_bairros.get(b, 0) + 1
    farm_bairros_list = sorted(farm_bairros.items(), key=lambda x: -x[1])

    itens_ativos = db.query(Estoque).filter(Estoque.status != "consumido").all()
    continuo_cnt: dict[str, int] = {}
    for it in itens_ativos:
        if it.uso_continuo:
            k = it.nome_medicamento.strip().lower()
            continuo_cnt[k] = continuo_cnt.get(k, 0) + 1
    top_continuo = sorted(continuo_cnt.items(), key=lambda x: -x[1])[:15]

    usuarios_com_itens = db.query(Usuario).filter(Usuario.telefone.notin_(TEST_PHONES)).all()
    por_usuario = []
    for u in usuarios_com_itens:
        itens_u = [it for it in itens_ativos if it.usuario_id == u.telefone]
        if itens_u:
            por_usuario.append({
                "nome": u.nome,
                "bairro": u.bairro,
                "total": len(itens_u),
                "continuo": sum(1 for it in itens_u if it.uso_continuo),
                "meds": [{"nome": it.nome_medicamento, "continuo": bool(it.uso_continuo),
                           "miligramas": it.miligramas} for it in itens_u],
            })
    por_usuario.sort(key=lambda x: -x["total"])

    return {
        "total_buscas": len(logs),
        "usuarios_unicos_buscas": usuarios_unicos,
        "total_usuarios": total_usuarios,
        "usuarios_reais": usuarios_reais,
        "novos_usuarios_7d": novos_7d,
        "novos_usuarios_30d": novos_30d,
        "total_itens_estoque": total_itens,
        "uso_continuo_total": sum(1 for it in itens_ativos if it.uso_continuo),
        "top_uso_continuo": [{"nome": n, "count": c} for n, c in top_continuo],
        "por_usuario": por_usuario,
        "por_tipo": por_tipo,
        "top_termos": [{"termo": t, "count": c} for t, c in top_termos],
        "top_estados": [{"estado": e, "count": c} for e, c in top_estados],
        "por_dia": [{"data": d, "count": c} for d, c in sorted(por_dia.items())],
        "por_bairro": por_bairro,
        "farmacias": {
            "total": len(farmacias_todas),
            "ativas": sum(1 for f in farmacias_todas if f.ativo),
            "por_plano": farm_por_plano,
            "por_origem": farm_por_origem,
            "bairros_cobertos": [{"bairro": b, "farmacias": c} for b, c in farm_bairros_list],
        },
    }


@router.get("/admin/crescimento")
def admin_crescimento(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    agora = datetime.utcnow()
    corte_30d = agora - timedelta(days=30)
    corte_7d  = agora - timedelta(days=7)
    corte_mes = agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    eventos = db.query(LogEvento).all()
    eventos_30d = [ev for ev in eventos if ev.criado_em and ev.criado_em >= corte_30d]

    cadastros_dia: dict[str, int] = {}
    exclusoes_dia: dict[str, int] = {}
    for ev in eventos_30d:
        d = str(ev.criado_em.date())
        if ev.tipo in ("cadastro_usuario", "cadastro_farmacia"):
            cadastros_dia[d] = cadastros_dia.get(d, 0) + 1
        elif ev.tipo in ("exclusao_usuario", "exclusao_farmacia"):
            exclusoes_dia[d] = exclusoes_dia.get(d, 0) + 1

    todos_dias = sorted(set(list(cadastros_dia.keys()) + list(exclusoes_dia.keys())))

    total_cadastros = sum(1 for ev in eventos if ev.tipo in ("cadastro_usuario", "cadastro_farmacia"))
    total_exclusoes = sum(1 for ev in eventos if ev.tipo in ("exclusao_usuario", "exclusao_farmacia"))
    cadastros_7d = sum(1 for ev in eventos if ev.tipo in ("cadastro_usuario", "cadastro_farmacia") and ev.criado_em and ev.criado_em >= corte_7d)
    cadastros_mes = sum(1 for ev in eventos if ev.tipo in ("cadastro_usuario", "cadastro_farmacia") and ev.criado_em and ev.criado_em >= corte_mes)
    exclusoes_7d = sum(1 for ev in eventos if ev.tipo in ("exclusao_usuario", "exclusao_farmacia") and ev.criado_em and ev.criado_em >= corte_7d)

    return {
        "total_cadastros": total_cadastros,
        "total_exclusoes": total_exclusoes,
        "cadastros_7d": cadastros_7d,
        "cadastros_mes": cadastros_mes,
        "exclusoes_7d": exclusoes_7d,
        "por_dia": [
            {"data": d, "cadastros": cadastros_dia.get(d, 0), "exclusoes": exclusoes_dia.get(d, 0)}
            for d in todos_dias
        ],
    }


@router.post("/admin/reset-dados")
def admin_reset_dados(telefone: str, confirmar: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    if confirmar != "CONFIRMAR_RESET":
        raise HTTPException(status_code=400, detail="Confirmação inválida. Passe confirmar=CONFIRMAR_RESET")

    FARM_TESTE = 8
    PHONES_PRESERVAR = [ADMIN_PHONE, "6599339250"]

    usuarios = db.query(Usuario).filter(Usuario.telefone.notin_(PHONES_PRESERVAR)).all()
    excluidos = 0
    for u in usuarios:
        db.query(LogBusca).filter(LogBusca.usuario_id == u.telefone).update({"usuario_id": None})
        sols = db.query(OrcamentoSolicitacao).filter(OrcamentoSolicitacao.usuario_id == u.telefone).all()
        for sol in sols:
            db.query(OrcamentoResposta).filter(OrcamentoResposta.solicitacao_id == sol.id).delete()
        db.query(OrcamentoSolicitacao).filter(OrcamentoSolicitacao.usuario_id == u.telefone).delete()
        db.query(PushSub).filter(PushSub.usuario_id == u.telefone).delete()
        db.query(AlarmeRemedio).filter(AlarmeRemedio.usuario_id == u.telefone).delete()
        db.query(Estoque).filter(Estoque.usuario_id == u.telefone).delete()
        db.query(Pedido).filter(Pedido.usuario_id == u.telefone).delete()
        db.query(CodigoRecuperacao).filter(CodigoRecuperacao.telefone == u.telefone).delete()
        db.delete(u)
        excluidos += 1

    db.query(Lead).filter(Lead.farmacia_id == FARM_TESTE).delete()
    db.query(LogEvento).delete()

    db.commit()
    logger.info(f"[ADMIN] Reset de dados: {excluidos} usuários excluídos por {tel}")
    return {"ok": True, "usuarios_excluidos": excluidos}


@router.get("/admin/insights")
def admin_insights(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")

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

    itens_com_preco = (
        db.query(Estoque, Usuario.bairro)
        .join(Usuario, Estoque.usuario_id == Usuario.telefone)
        .filter(Estoque.preco_real.isnot(None), Usuario.bairro.isnot(None))
        .all()
    )
    agg_ticket: dict = {}
    for item, bairro in itens_com_preco:
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


@router.get("/admin/leads")
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


@router.post("/admin/leads/gerar-manual")
def gerar_leads_manual(telefone: str, db: Session = Depends(get_db)):
    """Dispara geração de leads manualmente (sem esperar cron)."""
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    try:
        hoje = date.today()
        dias = int(get_config(db, "dias_aviso_vencimento", "30"))
        limite = hoje + timedelta(days=dias)
        usuarios = db.query(Usuario).all()
        total = 0
        erros = []
        for u in usuarios:
            try:
                itens = db.query(Estoque).filter(
                    Estoque.usuario_id == u.telefone,
                    Estoque.status != "consumido",
                    Estoque.data_validade.isnot(None),
                    Estoque.data_validade <= limite
                ).all()
                for item in itens:
                    total += gerar_leads_para_item(db, item, u, origem="manual")
            except Exception as ex:
                erros.append(f"usuario {u.telefone}: {ex}")
                db.rollback()
        return {"leads_gerados": total, "erros": erros}
    except Exception as ex:
        import traceback
        return JSONResponse(status_code=500, content={"detail": str(ex), "trace": traceback.format_exc()[-800:]})


@router.post("/admin/reset-pin")
def admin_reset_pin(telefone: str, telefone_alvo: str, db: Session = Depends(get_db)):
    """Admin: zera o PIN de um usuário para que ele possa entrar e redefinir."""
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    alvo = validar_telefone(telefone_alvo)
    usuario = db.query(Usuario).filter(Usuario.telefone == alvo).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    usuario.pin = None
    db.commit()
    logger.info(f"[ADMIN] PIN zerado para {alvo} por {tel}")
    return {"ok": True, "mensagem": f"PIN de {usuario.nome} zerado. Ele pode entrar sem PIN e definir um novo."}


@router.delete("/admin/usuario/{telefone_alvo}")
def admin_excluir_usuario(telefone_alvo: str, telefone: str, db: Session = Depends(get_db)):
    """Admin: exclui qualquer usuário e todos os seus dados."""
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    alvo = validar_telefone(telefone_alvo)
    usuario = db.query(Usuario).filter(Usuario.telefone == alvo).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    db.query(LogBusca).filter(LogBusca.usuario_id == alvo).update({"usuario_id": None})
    sols = db.query(OrcamentoSolicitacao).filter(OrcamentoSolicitacao.usuario_id == alvo).all()
    for sol in sols:
        db.query(OrcamentoResposta).filter(OrcamentoResposta.solicitacao_id == sol.id).delete()
    db.query(OrcamentoSolicitacao).filter(OrcamentoSolicitacao.usuario_id == alvo).delete()
    db.query(PushSub).filter(PushSub.usuario_id == alvo).delete()
    db.query(AlarmeRemedio).filter(AlarmeRemedio.usuario_id == alvo).delete()
    db.query(Estoque).filter(Estoque.usuario_id == alvo).delete()
    db.query(Pedido).filter(Pedido.usuario_id == alvo).delete()
    db.query(CodigoRecuperacao).filter(CodigoRecuperacao.telefone == alvo).delete()
    db.delete(usuario)
    db.add(LogEvento(tipo="exclusao_usuario"))
    db.commit()
    logger.info(f"[ADMIN] Usuário {alvo} excluído por {tel}")
    return {"ok": True, "mensagem": f"Usuário {alvo} excluído com sucesso."}


@router.get("/admin/configuracoes")
def listar_configuracoes(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    return [{"chave": c.chave, "valor": c.valor, "descricao": c.descricao}
            for c in db.query(Configuracao).order_by(Configuracao.chave).all()]


@router.put("/admin/configuracoes/{chave}")
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


@router.get("/admin/buscas-recentes")
def admin_buscas_recentes(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    buscas = db.query(LogBusca).order_by(LogBusca.timestamp.desc()).limit(200).all()
    return [{"id": b.id, "termo": b.termo, "tipo": b.tipo, "bairro": b.bairro,
             "estado": b.estado, "usuario_id": b.usuario_id,
             "data": b.timestamp.strftime("%d/%m %H:%M") if b.timestamp else None} for b in buscas]


@router.get("/admin/feedbacks")
def admin_feedbacks(telefone: str, db: Session = Depends(get_db)):
    if re.sub(r"\D", "", telefone) != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    feedbacks = db.query(Feedback).order_by(Feedback.criado_em.desc()).limit(200).all()
    return [
        {
            "id": f.id,
            "usuario_id": f.usuario_id,
            "nota": f.nota,
            "categoria": f.categoria,
            "mensagem": f.mensagem,
            "cidade": f.cidade,
            "bairro": f.bairro,
            "criado_em": f.criado_em.isoformat() if f.criado_em else None,
        }
        for f in feedbacks
    ]


@router.get("/admin/anuncios")
def admin_listar_anuncios(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    anuncios = db.query(AnuncioPush).order_by(AnuncioPush.criado_em.desc()).all()
    total_receita = sum(a.preco for a in anuncios if a.status in ("pago", "disparado"))
    return {
        "total_receita": round(total_receita, 2),
        "total_anuncios": len(anuncios),
        "anuncios": [{
            "id": a.id,
            "farmacia": a.farmacia.nome if a.farmacia else "—",
            "texto": a.texto[:60],
            "titulo": a.titulo,
            "publico": a.publico,
            "genero_alvo": a.genero_alvo or "todos",
            "status": a.status,
            "preco": a.preco,
            "total_enviados": a.total_enviados,
            "produto": a.produto,
            "preco_de": a.preco_de,
            "preco_por": a.preco_por,
            "data_expiracao": str(a.data_expiracao) if a.data_expiracao else None,
            "categorias": a.categorias,
            "total_interesses": db.query(InteresseAnuncio).filter(InteresseAnuncio.anuncio_id == a.id).count(),
            "criado_em": str(a.criado_em.date()) if a.criado_em else None,
        } for a in anuncios]
    }


@router.post("/admin/anuncio/{anuncio_id}/confirmar-pagamento")
def admin_confirmar_pagamento_anuncio(anuncio_id: int, telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    anuncio = db.query(AnuncioPush).filter(AnuncioPush.id == anuncio_id).first()
    if not anuncio:
        raise HTTPException(status_code=404, detail="Anúncio não encontrado.")
    if anuncio.status != "aguardando_pagamento":
        raise HTTPException(status_code=400, detail=f"Status atual: {anuncio.status}. Só é possível confirmar quando aguardando pagamento.")
    anuncio.status = "pago"
    db.commit()
    return {"ok": True}


@router.post("/admin/anuncio/{anuncio_id}/disparar")
def admin_disparar_anuncio(anuncio_id: int, telefone: str, modo_teste: bool = False, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    anuncio = db.query(AnuncioPush).filter(AnuncioPush.id == anuncio_id).first()
    if not anuncio:
        raise HTTPException(status_code=404, detail="Anúncio não encontrado.")
    if not modo_teste:
        if anuncio.status == "disparado":
            raise HTTPException(status_code=400, detail="Anúncio já foi disparado.")
        if anuncio.status != "pago":
            raise HTTPException(status_code=400, detail="Pagamento ainda não confirmado. Confirme o pagamento antes de disparar.")
    total = _disparar_anuncio_push(anuncio, db, modo_teste=modo_teste)
    return {"ok": True, "total_enviados": total, "modo_teste": modo_teste}


@router.delete("/admin/anuncio/{anuncio_id}")
def admin_excluir_anuncio(anuncio_id: int, telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    anuncio = db.query(AnuncioPush).filter(AnuncioPush.id == anuncio_id).first()
    if not anuncio:
        raise HTTPException(status_code=404, detail="Anúncio não encontrado.")
    db.delete(anuncio)
    db.commit()
    return {"ok": True}
