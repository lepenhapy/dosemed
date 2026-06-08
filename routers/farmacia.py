# routers/farmacia.py — /farmacia/*, /admin/farmacias/*, /admin/recebimentos/*

import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import ADMIN_PHONE
from database import get_config, get_db, get_farmacia_autenticada, limiter
from helpers import (
    asaas_criar_assinatura,
    asaas_criar_cliente,
    asaas_criar_cobranca,
    asaas_criar_link_pagamento,
    asaas_listar_cobrancas,
    enviar_email,
    hash_pin,
    html_anuncio_confirmacao,
    sanitizar,
    validar_email,
    validar_telefone,
)
from models import (
    AnuncioPush,
    Farmacia,
    InteresseAnuncio,
    Lead,
    LogBusca,
    LogEvento,
    OrcamentoResposta,
    OrcamentoSolicitacao,
)
from schemas import (
    AnuncioCriarPayload,
    ExcluirContaPayload,
    FarmaciaCadastroPayload,
    FarmaciaPayload,
    FarmaciaPerfilPayload,
    FarmaciaSetPinPayload,
)

logger = logging.getLogger("dosemed")

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def serializar_farmacia(f: Farmacia, db: Session) -> dict:
    total_leads = db.query(Lead).filter(Lead.farmacia_id == f.id).count()
    leads_mes = db.query(Lead).filter(
        Lead.farmacia_id == f.id,
        Lead.criado_em >= datetime.now(timezone.utc).replace(tzinfo=None).replace(day=1, hour=0, minute=0, second=0)
    ).count()
    return {
        "id": f.id, "nome": f.nome, "cnpj": f.cnpj, "email": f.email,
        "telefone_contato": f.telefone_contato, "bairros": f.bairros,
        "plano": f.plano, "ativo": bool(f.ativo), "atende_manipulado": bool(f.atende_manipulado),
        "asaas_customer_id": f.asaas_customer_id, "asaas_subscription_id": f.asaas_subscription_id,
        "total_leads": total_leads, "leads_mes": leads_mes,
        "origem": f.origem or "admin",
        "servicos": f.servicos,
        "criado_em": str(f.criado_em.date()) if f.criado_em else None,
    }


def _disparar_anuncio_push(anuncio: AnuncioPush, db: Session, modo_teste: bool = False) -> int:
    from helpers import enviar_push
    from models import Usuario, PushSub

    farmacia = db.query(Farmacia).filter(Farmacia.id == anuncio.farmacia_id).first()

    if modo_teste:
        subs = db.query(PushSub).filter(PushSub.usuario_id == ADMIN_PHONE).all()
        titulo = (anuncio.titulo or (farmacia.nome if farmacia else "DoseMed")) + " [TESTE]"
        total = sum(1 for sub in subs if enviar_push(sub, titulo, anuncio.texto))
        logger.info(f"[ANUNCIO TESTE] {anuncio.id}: {total} push enviados apenas ao admin")
        return total

    bairros_farm: set[str] = set()
    if anuncio.publico == "bairro" and farmacia and farmacia.bairros:
        bairros_farm = {b.strip().lower() for b in farmacia.bairros.split(",") if b.strip()}

    usuarios = db.query(Usuario).all()
    telefones_alvo: set[str] = set()
    for u in usuarios:
        if bairros_farm:
            if (u.bairro or "").strip().lower() not in bairros_farm:
                continue
        if anuncio.genero_alvo in ("M", "F"):
            if u.genero != anuncio.genero_alvo:
                continue
        if anuncio.faixa_etaria in ("18-35", "36-55", "56+") and u.data_nascimento:
            from datetime import date as _date
            idade = (_date.today() - u.data_nascimento).days // 365
            fa = anuncio.faixa_etaria
            if fa == "18-35" and not (18 <= idade <= 35):
                continue
            elif fa == "36-55" and not (36 <= idade <= 55):
                continue
            elif fa == "56+" and idade < 56:
                continue
        telefones_alvo.add(u.telefone)

    # Filtro por categoria terapêutica — só envia para quem tem o medicamento relevante
    import json as _json
    from config import CATEGORIAS_TERAPEUTICAS
    from models import Estoque

    cats_filter: list[str] = []
    if anuncio.categorias:
        try:
            cats_filter = _json.loads(anuncio.categorias)
        except Exception:
            pass

    if cats_filter:
        def _med_match(nome: str) -> bool:
            nome_l = nome.lower()
            for cat_id in cats_filter:
                cat = next((c for c in CATEGORIAS_TERAPEUTICAS if c["id"] == cat_id), None)
                if cat and any(kw in nome_l or nome_l in kw for kw in cat["kw"]):
                    return True
            return False

        itens = db.query(Estoque).filter(
            Estoque.usuario_id.in_(telefones_alvo),
            Estoque.status != "consumido"
        ).all()
        usuarios_com_cat = {i.usuario_id for i in itens if _med_match(i.nome_medicamento)}
        telefones_alvo = telefones_alvo & usuarios_com_cat

    from helpers import enviar_push
    subs = db.query(PushSub).filter(PushSub.usuario_id.in_(telefones_alvo)).all()
    titulo = anuncio.titulo or (farmacia.nome if farmacia else "DoseMed")
    total = sum(1 for sub in subs if enviar_push(sub, titulo, anuncio.texto))

    anuncio.status = "disparado"
    anuncio.total_enviados = total
    anuncio.disparado_em = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()

    if farmacia and farmacia.email:
        enviar_email(farmacia.email, f"DoseMed — Anúncio disparado: {anuncio.texto[:40]}...",
                     html_anuncio_confirmacao(farmacia.nome, anuncio.texto, total, anuncio.publico))
    logger.info(f"[ANUNCIO] {anuncio.id} disparado: {total} push enviados")
    return total


# ---------------------------------------------------------------------------
# Admin: farmacias
# ---------------------------------------------------------------------------

@router.get("/admin/farmacias")
def listar_farmacias(telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    farmacias = db.query(Farmacia).order_by(Farmacia.nome).all()
    return [serializar_farmacia(f, db) for f in farmacias]


@router.post("/admin/farmacias")
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

    cid, asaas_erro_cli = asaas_criar_cliente(f.nome, f.email, f.cnpj)
    if cid:
        f.asaas_customer_id = cid
        if payload.plano and payload.plano != "lead":
            preco_chave = f"preco_plano_{payload.plano}"
            valor = float(get_config(db, preco_chave, "299.00"))
            sub_id = asaas_criar_assinatura(cid, valor, f"DoseMed — Plano {payload.plano.capitalize()} — {f.nome}")
            if sub_id:
                f.asaas_subscription_id = sub_id
    elif asaas_erro_cli:
        logger.warning(f"Farmácia {f.nome}: cliente Asaas não criado — {asaas_erro_cli}")

    db.commit()
    return serializar_farmacia(f, db)


@router.put("/admin/farmacias/{farmacia_id}")
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
    if payload.origem is not None:
        f.origem = payload.origem

    if f.plano != "lead" and f.plano != plano_anterior and f.asaas_customer_id and not f.asaas_subscription_id:
        preco_chave = f"preco_plano_{f.plano}"
        valor = float(get_config(db, preco_chave, "299.00"))
        sub_id = asaas_criar_assinatura(f.asaas_customer_id, valor, f"DoseMed — Plano {f.plano.capitalize()} — {f.nome}")
        if sub_id:
            f.asaas_subscription_id = sub_id

    db.commit()
    return serializar_farmacia(f, db)


@router.post("/admin/farmacias/{farmacia_id}/faturar-leads")
def faturar_leads(farmacia_id: int, telefone: str, db: Session = Depends(get_db)):
    """Gera cobrança Asaas pelos leads do mês atual ainda não faturados."""
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    f = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada.")
    if not f.asaas_customer_id:
        import os
        if not os.getenv("ASAAS_KEY"):
            raise HTTPException(status_code=400, detail="ASAAS_KEY não configurada — integração desativada.")
        if not f.cnpj:
            raise HTTPException(status_code=400, detail="Farmácia sem CNPJ cadastrado — necessário para criar cliente Asaas.")
        cid, asaas_erro_cli = asaas_criar_cliente(f.nome, f.email or "", f.cnpj)
        if not cid:
            raise HTTPException(status_code=502, detail=f"Falha ao criar cliente Asaas: {asaas_erro_cli}")
        f.asaas_customer_id = cid
        db.commit()
        logger.info(f"Asaas: cliente criado automaticamente para farmácia {f.id} ({f.nome}): {cid}")

    inicio_mes = datetime.now(timezone.utc).replace(tzinfo=None).replace(day=1, hour=0, minute=0, second=0)
    leads_nao_faturados = db.query(Lead).filter(
        Lead.farmacia_id == farmacia_id,
        Lead.criado_em >= inicio_mes,
        Lead.asaas_charge_id.is_(None)
    ).all()

    if not leads_nao_faturados:
        return {"mensagem": "Nenhum lead a faturar.", "total": 0, "valor": 0}

    valor_total = sum(l.preco_cobrado or 0 for l in leads_nao_faturados)
    mes_atual = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%m/%Y")
    charge_id, _, asaas_erro = asaas_criar_cobranca(
        f.asaas_customer_id, valor_total,
        f"DoseMed — {len(leads_nao_faturados)} leads em {mes_atual} — {f.nome}"
    )
    if charge_id:
        for l in leads_nao_faturados:
            l.asaas_charge_id = charge_id
        db.commit()
        mensagem = "Cobrança PIX gerada com sucesso no Asaas."
    else:
        mensagem = f"Leads contabilizados, mas cobrança Asaas falhou: {asaas_erro}"

    return {"mensagem": mensagem, "total_leads": len(leads_nao_faturados),
            "valor": round(valor_total, 2), "asaas_charge_id": charge_id}


@router.get("/admin/recebimentos/{farmacia_id}")
def recebimentos_farmacia(farmacia_id: int, telefone: str, db: Session = Depends(get_db)):
    tel = validar_telefone(telefone)
    if tel != ADMIN_PHONE:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    f = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada.")
    cobrancas = asaas_listar_cobrancas(f.asaas_customer_id or "")
    return {"farmacia": f.nome, "cobrancas": cobrancas}


# ---------------------------------------------------------------------------
# Farmácia portal (auto-cadastro, dashboard, perfil, etc.)
# ---------------------------------------------------------------------------

@router.post("/farmacia/cadastro")
@limiter.limit("10/hour")
async def farmacia_auto_cadastro(request: Request, payload: FarmaciaCadastroPayload, db: Session = Depends(get_db)):
    """Auto-cadastro público de farmácia. Inicia como plano lead, aguarda ativação admin."""
    digitos = re.sub(r"\D", "", payload.telefone)
    if not (10 <= len(digitos) <= 11):
        raise HTTPException(status_code=400, detail="Telefone inválido. Informe DDD + número.")
    if not validar_email(payload.email.strip()):
        raise HTTPException(status_code=400, detail="E-mail inválido.")
    from models import Usuario as _U
    if db.query(_U).filter(_U.telefone == digitos).first():
        raise HTTPException(status_code=409, detail="Telefone já cadastrado como paciente.")
    if db.query(Farmacia).filter(Farmacia.telefone_contato == digitos).first():
        raise HTTPException(status_code=409, detail="Farmácia já cadastrada com este telefone.")
    f = Farmacia(
        nome=sanitizar(payload.nome),
        email=payload.email.strip().lower(),
        cnpj=payload.cnpj,
        telefone_contato=digitos,
        bairros=payload.bairros,
        atende_manipulado=payload.atende_manipulado or 0,
        plano="lead",
        ativo=1,
    )
    try:
        f.origem = "auto"
    except Exception:
        pass
    token = secrets.token_urlsafe(32)
    f.session_token = token
    db.add(f)
    db.add(LogEvento(tipo="cadastro_farmacia"))
    db.commit()
    logger.info(f"Nova farmácia auto-cadastrada: {f.nome} ({digitos})")
    return {"ok": True, "farmacia_id": f.id, "mensagem": "Farmácia cadastrada! Defina seu PIN para acessar o painel.", "session_token": token}


@router.get("/farmacia/verificar-telefone")
def farmacia_verificar_telefone(telefone: str, db: Session = Depends(get_db)):
    digitos = re.sub(r"\D", "", telefone)
    f = db.query(Farmacia).filter(Farmacia.telefone_contato == digitos).first()
    if not f:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada com este telefone.")
    session_token = None
    if not f.pin:
        session_token = secrets.token_urlsafe(32)
        f.session_token = session_token
        db.commit()
    return {"tipo": "farmacia", "tem_pin": bool(f.pin), "farmacia_nome": f.nome, "farmacia_id": f.id, "session_token": session_token}


@router.post("/auth/farmacia/definir-pin")
def farmacia_definir_pin(payload: FarmaciaSetPinPayload, db: Session = Depends(get_db)):
    digitos = re.sub(r"\D", "", payload.telefone)
    f = db.query(Farmacia).filter(Farmacia.telefone_contato == digitos).first()
    if not f:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada com este telefone.")
    pin = re.sub(r"\D", "", payload.pin)
    if not (4 <= len(pin) <= 6):
        raise HTTPException(status_code=400, detail="PIN deve ter 4 a 6 dígitos.")
    if f.pin:
        raise HTTPException(status_code=400, detail="PIN já definido. Use a recuperação de PIN para alterá-lo.")
    f.pin = hash_pin(pin, digitos)
    token = secrets.token_urlsafe(32)
    f.session_token = token
    db.commit()
    return {"ok": True, "mensagem": "PIN da farmácia definido.", "session_token": token}


@router.get("/farmacia/dashboard")
def farmacia_dashboard(telefone: str, farmacia_auth: Farmacia = Depends(get_farmacia_autenticada), db: Session = Depends(get_db)):
    digitos = re.sub(r"\D", "", telefone)
    if farmacia_auth.telefone_contato != digitos:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    f = farmacia_auth

    cutoff_30d = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    leads_recentes = db.query(Lead).filter(
        Lead.farmacia_id == f.id,
        Lead.criado_em >= cutoff_30d
    ).order_by(Lead.criado_em.desc()).all()

    inicio_mes = datetime.now(timezone.utc).replace(tzinfo=None).replace(day=1, hour=0, minute=0, second=0)
    leads_mes = db.query(Lead).filter(
        Lead.farmacia_id == f.id,
        Lead.criado_em >= inicio_mes
    ).count()

    total_leads = db.query(Lead).filter(Lead.farmacia_id == f.id).count()
    rating = round(f.rating_total / f.rating_count, 1) if f.rating_count else None

    return {
        "farmacia": {
            "id": f.id, "nome": f.nome, "email": f.email,
            "telefone_contato": f.telefone_contato, "bairros": f.bairros,
            "plano": f.plano, "ativo": bool(f.ativo),
            "atende_manipulado": bool(f.atende_manipulado),
            "servicos": f.servicos,
            "rating": rating, "rating_count": f.rating_count,
        },
        "leads_total": total_leads,
        "leads_mes": leads_mes,
        "leads_recentes": [{
            "id": l.id, "bairro": l.usuario_bairro, "medicamento": l.medicamento,
            "principio_ativo": l.principio_ativo, "miligramas": l.miligramas,
            "manipulado": bool(l.manipulado), "status": l.status,
            "criado_em": str(l.criado_em.date()) if l.criado_em else None,
        } for l in leads_recentes],
    }


@router.put("/farmacia/perfil")
def farmacia_atualizar_perfil(telefone: str, payload: FarmaciaPerfilPayload, farmacia_auth: Farmacia = Depends(get_farmacia_autenticada), db: Session = Depends(get_db)):
    digitos = re.sub(r"\D", "", telefone)
    if farmacia_auth.telefone_contato != digitos:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    f = farmacia_auth
    if payload.bairros is not None:
        f.bairros = payload.bairros
    if payload.atende_manipulado is not None:
        f.atende_manipulado = payload.atende_manipulado
    if payload.servicos is not None:
        f.servicos = payload.servicos
    db.commit()
    return {"ok": True, "mensagem": "Perfil atualizado."}


@router.delete("/farmacia/conta")
def excluir_farmacia(telefone: str, payload: ExcluirContaPayload, db: Session = Depends(get_db)):
    """Exclusão definitiva da conta da farmácia (LGPD art. 18)."""
    digitos = re.sub(r"\D", "", telefone)
    if payload.confirmacao != "EXCLUIR":
        raise HTTPException(status_code=400, detail="Confirmação inválida. Digite exatamente: EXCLUIR")

    f = db.query(Farmacia).filter(Farmacia.telefone_contato == digitos).first()
    if not f:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada.")

    pin = re.sub(r"\D", "", payload.pin)
    if f.pin and hash_pin(pin, digitos) != f.pin:
        raise HTTPException(status_code=401, detail="PIN incorreto.")

    db.query(OrcamentoResposta).filter(OrcamentoResposta.farmacia_id == f.id).delete()
    db.query(Lead).filter(Lead.farmacia_id == f.id).delete()
    db.delete(f)
    db.add(LogEvento(tipo="exclusao_farmacia"))
    db.commit()

    logger.info(f"Farmácia excluída (LGPD): {digitos} — {f.nome}")
    return {"ok": True, "mensagem": "Conta da farmácia excluída com sucesso."}


@router.get("/farmacia/insights")
def farmacia_insights(telefone: str, farmacia_auth: Farmacia = Depends(get_farmacia_autenticada), db: Session = Depends(get_db)):
    digitos = re.sub(r"\D", "", telefone)
    f = farmacia_auth
    if f.telefone_contato != digitos:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    if f.plano not in ("pro", "manipulado"):
        raise HTTPException(status_code=403, detail="Insights disponíveis apenas para planos Pro e Manipulado.")

    bairros_f = [b.strip() for b in (f.bairros or "").split(",") if b.strip()]
    query = db.query(LogBusca)
    if bairros_f:
        query = query.filter(LogBusca.bairro.in_(bairros_f))
    else:
        query = query.filter(LogBusca.bairro.isnot(None))
    logs = query.order_by(LogBusca.timestamp.desc()).limit(500).all()

    contagem: dict[str, int] = {}
    for l in logs:
        t = l.termo.lower()
        contagem[t] = contagem.get(t, 0) + 1
    top_meds = sorted(contagem.items(), key=lambda x: -x[1])[:20]

    return {
        "bairros": bairros_f,
        "top_medicamentos": [{"termo": t, "buscas": c} for t, c in top_meds],
        "total_buscas": len(logs),
    }


# ---------------------------------------------------------------------------
# Farmácia: anúncios push
# ---------------------------------------------------------------------------

@router.post("/farmacia/anuncio/criar")
def farmacia_criar_anuncio(payload: AnuncioCriarPayload, db: Session = Depends(get_db)):
    import json as _json
    import os
    from datetime import date as _date

    digitos = re.sub(r"\D", "", payload.telefone)
    farm = db.query(Farmacia).filter(Farmacia.telefone_contato == digitos).first()
    if not farm:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada.")
    pin = re.sub(r"\D", "", payload.pin)
    if farm.pin and hash_pin(pin, digitos) != farm.pin:
        raise HTTPException(status_code=401, detail="PIN incorreto.")
    from config import SERVICOS_CLINICOS

    # Serviço clínico: auto-preenche categorias e texto se necessário
    servico_info = SERVICOS_CLINICOS.get(payload.servico or "") if payload.servico else None
    texto_final = payload.texto.strip()
    if servico_info and not texto_final:
        texto_final = f"{servico_info['label']} disponível em nossa farmácia! Sem agendamento."

    if not texto_final:
        raise HTTPException(status_code=400, detail="Texto do anúncio é obrigatório.")
    if payload.publico not in ("bairro", "todos"):
        raise HTTPException(status_code=400, detail="Público inválido. Use 'bairro' ou 'todos'.")

    chave_preco = "preco_anuncio_bairro" if payload.publico == "bairro" else "preco_anuncio_todos"
    preco = float(get_config(db, chave_preco, "30.00" if payload.publico == "bairro" else "70.00"))
    genero_alvo = payload.genero_alvo if payload.genero_alvo in ("M", "F") else None

    data_exp = None
    if payload.data_expiracao:
        try:
            data_exp = _date.fromisoformat(payload.data_expiracao)
        except ValueError:
            pass

    cats_json = None
    # Serviço clínico: usa as categorias do serviço
    if servico_info and servico_info["categorias"]:
        cats_json = _json.dumps(servico_info["categorias"])
    elif payload.categorias:
        try:
            cats = _json.loads(payload.categorias) if isinstance(payload.categorias, str) else payload.categorias
            cats_json = _json.dumps([c for c in cats if isinstance(c, str)])
        except Exception:
            pass

    faixa_etaria = payload.faixa_etaria if payload.faixa_etaria in ("18-35", "36-55", "56+") else None
    titulo_final = sanitizar(payload.titulo or "") or (servico_info["label"] if servico_info else None)
    anuncio = AnuncioPush(
        farmacia_id=farm.id,
        texto=sanitizar(texto_final),
        titulo=titulo_final,
        publico=payload.publico,
        genero_alvo=genero_alvo,
        faixa_etaria=faixa_etaria,
        preco=preco,
        status="aguardando_pagamento",
        produto=sanitizar(payload.produto or "")[:200] or None,
        preco_de=payload.preco_de,
        preco_por=payload.preco_por,
        data_expiracao=data_exp,
        tem_entrega=1 if payload.tem_entrega else 0,
        valor_frete=payload.valor_frete,
        formas_pagamento=payload.formas_pagamento,
        whatsapp_contato=re.sub(r"\D", "", payload.whatsapp_contato) if payload.whatsapp_contato else None,
        categorias=cats_json,
    )
    db.add(anuncio)
    db.flush()

    publico_desc = "seu bairro/região" if payload.publico == "bairro" else "todos os bairros"
    pix_link = link_id = asaas_erro = None
    pix_link, link_id, asaas_erro = asaas_criar_link_pagamento(
        preco,
        f"DoseMed Anúncio Push — {publico_desc}",
        f"anuncio_{anuncio.id}"
    )
    if pix_link:
        anuncio.pix_link = pix_link
    if link_id:
        anuncio.asaas_charge_id = link_id
    db.commit()

    if asaas_erro:
        logger.warning(f"Anúncio #{anuncio.id}: link Asaas falhou — {asaas_erro}")

    return {
        "ok": True,
        "anuncio_id": anuncio.id,
        "preco": preco,
        "publico": publico_desc,
        "pix_link": pix_link,
        "instrucao": "Clique em 'Pagar agora' para concluir o pagamento. Após a confirmação o anúncio entrará em análise." if pix_link
                     else f"Não foi possível gerar o link de pagamento ({asaas_erro}). Contate o suporte.",
    }


@router.get("/farmacia/precos-anuncio")
def farmacia_precos_anuncio(db: Session = Depends(get_db)):
    return {
        "bairro": float(get_config(db, "preco_anuncio_bairro", "30.00")),
        "todos": float(get_config(db, "preco_anuncio_todos", "70.00")),
    }


@router.get("/farmacia/anuncios")
def farmacia_listar_anuncios(telefone: str, farmacia_auth: Farmacia = Depends(get_farmacia_autenticada), db: Session = Depends(get_db)):
    digitos = re.sub(r"\D", "", telefone)
    farm = farmacia_auth
    if farm.telefone_contato != digitos:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    anuncios = db.query(AnuncioPush).filter(AnuncioPush.farmacia_id == farm.id).order_by(AnuncioPush.criado_em.desc()).limit(20).all()
    return [{
        "id": a.id, "texto": a.texto, "titulo": a.titulo, "publico": a.publico,
        "genero_alvo": a.genero_alvo, "status": a.status, "preco": a.preco,
        "pix_link": a.pix_link, "total_enviados": a.total_enviados,
        "produto": a.produto, "preco_de": a.preco_de, "preco_por": a.preco_por,
        "data_expiracao": str(a.data_expiracao) if a.data_expiracao else None,
        "tem_entrega": bool(a.tem_entrega), "valor_frete": a.valor_frete,
        "formas_pagamento": a.formas_pagamento, "whatsapp_contato": a.whatsapp_contato,
        "categorias": a.categorias,
        "total_interesses": db.query(InteresseAnuncio).filter(InteresseAnuncio.anuncio_id == a.id).count(),
        "criado_em": str(a.criado_em.date()) if a.criado_em else None,
        "disparado_em": str(a.disparado_em) if a.disparado_em else None,
    } for a in anuncios]


@router.get("/farmacia/orcamentos-respondidos")
def farmacia_orcamentos_respondidos(telefone: str, farmacia_auth: Farmacia = Depends(get_farmacia_autenticada), db: Session = Depends(get_db)):
    digitos = re.sub(r"\D", "", telefone)
    farm = farmacia_auth
    if farm.telefone_contato != digitos:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    respostas = (
        db.query(OrcamentoResposta)
        .filter(
            OrcamentoResposta.farmacia_id == farm.id,
            OrcamentoResposta.status.in_(["respondido", "ganhou", "perdeu"])
        )
        .order_by(OrcamentoResposta.respondido_em.desc())
        .limit(30)
        .all()
    )
    return [{
        "id": r.id,
        "medicamento": r.solicitacao.nome_med,
        "quantidade": r.solicitacao.quantidade_restante,
        "preco": r.preco,
        "prazo_entrega": r.prazo_entrega,
        "formas_pagamento": r.formas_pagamento,
        "status": r.status,
        "status_solicitacao": r.solicitacao.status,
        "respondido_em": str(r.respondido_em.date()) if r.respondido_em else None,
    } for r in respostas]


@router.get("/farmacia/metricas")
def farmacia_metricas(telefone: str, farmacia_auth: Farmacia = Depends(get_farmacia_autenticada), db: Session = Depends(get_db)):
    digitos = re.sub(r"\D", "", telefone)
    farm = farmacia_auth
    if farm.telefone_contato != digitos:
        raise HTTPException(status_code=403, detail="Acesso negado.")

    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    inicio_mes = agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if inicio_mes.month == 1:
        inicio_mes_ant = inicio_mes.replace(year=inicio_mes.year - 1, month=12)
    else:
        inicio_mes_ant = inicio_mes.replace(month=inicio_mes.month - 1)

    # Leads
    leads_total  = db.query(func.count(Lead.id)).filter(Lead.farmacia_id == farm.id).scalar() or 0
    leads_mes    = db.query(func.count(Lead.id)).filter(Lead.farmacia_id == farm.id, Lead.criado_em >= inicio_mes).scalar() or 0
    leads_mes_ant = db.query(func.count(Lead.id)).filter(Lead.farmacia_id == farm.id, Lead.criado_em >= inicio_mes_ant, Lead.criado_em < inicio_mes).scalar() or 0
    receita_leads = db.query(func.sum(Lead.preco_cobrado)).filter(Lead.farmacia_id == farm.id, Lead.preco_cobrado.isnot(None)).scalar() or 0.0

    # Orçamentos
    recebidos   = db.query(func.count(OrcamentoResposta.id)).filter(OrcamentoResposta.farmacia_id == farm.id).scalar() or 0
    respondidos = db.query(func.count(OrcamentoResposta.id)).filter(OrcamentoResposta.farmacia_id == farm.id, OrcamentoResposta.status.in_(["respondido", "ganhou", "perdeu"])).scalar() or 0
    ganhos      = db.query(func.count(OrcamentoResposta.id)).filter(OrcamentoResposta.farmacia_id == farm.id, OrcamentoResposta.status == "ganhou").scalar() or 0
    entregues   = db.query(func.count(OrcamentoResposta.id)).filter(OrcamentoResposta.farmacia_id == farm.id, OrcamentoResposta.status == "ganhou", OrcamentoResposta.solicitacao.has(entregue=1)).scalar() or 0

    # Urgentes: pendentes com menos de 15 min para expirar
    limite_urgente = agora + timedelta(minutes=15)
    urgentes = db.query(func.count(OrcamentoResposta.id)).filter(
        OrcamentoResposta.farmacia_id == farm.id,
        OrcamentoResposta.status == "pendente",
        OrcamentoResposta.solicitacao.has(OrcamentoSolicitacao.expira_em <= limite_urgente),
        OrcamentoResposta.solicitacao.has(OrcamentoSolicitacao.expira_em >= agora),
    ).scalar() or 0

    taxa_resposta   = round(respondidos / recebidos * 100, 1) if recebidos else 0
    taxa_conversao  = round(ganhos / respondidos * 100, 1) if respondidos else 0
    nota_media      = round(farm.rating_total / farm.rating_count, 1) if farm.rating_count else None

    return {
        "leads_total": leads_total,
        "leads_mes": leads_mes,
        "leads_mes_anterior": leads_mes_ant,
        "receita_leads": round(receita_leads, 2),
        "orcamentos_recebidos": recebidos,
        "orcamentos_respondidos": respondidos,
        "orcamentos_ganhos": ganhos,
        "orcamentos_entregues": entregues,
        "urgentes": urgentes,
        "taxa_resposta_pct": taxa_resposta,
        "taxa_conversao_pct": taxa_conversao,
        "nota_media": nota_media,
        "total_avaliacoes": farm.rating_count or 0,
    }
