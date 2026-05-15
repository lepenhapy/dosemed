# routers/estoque.py — /dashboard/*, /historico/*, /estoque/*, /webhook, /reconhecer-imagem

import base64
import json
import re
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from config import ADMIN_PHONE
from database import get_db, get_usuario_autenticado, limiter
from helpers import (
    ANTHROPIC_OK,
    atualizar_status_estoque,
    calcular_preco,
    calcular_status,
    comprimir_imagem,
    get_api_key,
    precos_medios_sistema,
    sanitizar,
    serializar_item,
    sort_por_vencimento,
    validar_telefone,
    valor_efetivo,
)
from models import Estoque, Usuario
from schemas import EditarItemPayload, WebhookPayload

router = APIRouter()


@router.post("/webhook")
def webhook(payload: WebhookPayload, db: Session = Depends(get_db)):
    import logging
    logger = logging.getLogger("dosemed")

    telefone = validar_telefone(payload.telefone)
    medicamento = sanitizar(payload.medicamento)

    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        nome = sanitizar(payload.nome_usuario or "Usuário")
        usuario = Usuario(telefone=telefone, nome=nome)
        db.add(usuario)
        db.commit()

    ja_tem = db.query(Estoque).filter(
        Estoque.usuario_id == telefone,
        Estoque.nome_medicamento.ilike(f"%{medicamento}%")
    ).first()

    if ja_tem and not payload.validade and payload.preco_real is None:
        msg = (
            f"Atenção: '{ja_tem.nome_medicamento}' está no estoque mas VENCIDO. Recomenda-se repor."
            if ja_tem.status == "vencido"
            else f"Você já tem '{ja_tem.nome_medicamento}' em estoque!"
        )
        return {"mensagem": msg, "status_item": ja_tem.status,
                "data_vencimento": str(ja_tem.data_validade) if ja_tem.data_validade else None}

    alerta_pa = None
    if payload.principio_ativo:
        pa = sanitizar(payload.principio_ativo)
        mesmo_pa = db.query(Estoque).filter(
            Estoque.usuario_id == telefone,
            Estoque.principio_ativo.ilike(f"%{pa}%"),
            Estoque.status.notin_(["consumido", "vencido"])
        ).first()
        if mesmo_pa:
            alerta_pa = f"Atenção: você já tem '{mesmo_pa.nome_medicamento}' com o mesmo princípio ativo em estoque."

    preco, categoria = calcular_preco(medicamento)
    data_val = None
    if payload.validade:
        try:
            data_val = date.fromisoformat(payload.validade)
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de validade inválido. Use AAAA-MM-DD.")

    status = calcular_status(data_val)
    item = Estoque(
        usuario_id=telefone,
        nome_medicamento=medicamento,
        principio_ativo=sanitizar(payload.principio_ativo or ""),
        miligramas=sanitizar(payload.miligramas or ""),
        fabricante=sanitizar(payload.fabricante or ""),
        quantidade=max(1, payload.quantidade or 1),
        manipulado=1 if payload.manipulado else 0,
        uso_continuo=1 if payload.uso_continuo else 0,
        preco_real=payload.preco_real,
        data_validade=data_val,
        categoria_valor=categoria,
        preco_estimado=preco,
        status=status
    )
    db.add(item)
    db.commit()

    atualizar_status_estoque(db, telefone)
    todos = db.query(Estoque).filter(Estoque.usuario_id == telefone, Estoque.status != "consumido").all()
    _ps = precos_medios_sistema(db)
    prejuizo = sum(valor_efetivo(i, _ps) * (i.quantidade or 1) for i in todos if i.status == "vencido")
    em_risco = sum(valor_efetivo(i, _ps) * (i.quantidade or 1) for i in todos if i.status == "atencao")

    logger.info(f"Item cadastrado: {medicamento} — usuário {telefone}")
    return {
        "mensagem": f"'{medicamento}' cadastrado com sucesso!",
        "alerta_principio_ativo": alerta_pa,
        "preco_estimado": preco,
        "status": status,
        "prejuizo_acumulado": prejuizo,
        "valor_em_risco": em_risco
    }


@router.get("/dashboard/{telefone}")
def dashboard(telefone: str, usuario_auth: Usuario = Depends(get_usuario_autenticado), db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    if usuario_auth.telefone != telefone:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    usuario = usuario_auth

    atualizar_status_estoque(db, telefone)
    itens = db.query(Estoque).filter(
        Estoque.usuario_id == telefone,
        Estoque.status != "consumido"
    ).all()

    vencidos = sort_por_vencimento([serializar_item(i) for i in itens if i.status == "vencido"])
    atencao = sort_por_vencimento([serializar_item(i) for i in itens if i.status == "atencao"])
    ok = sort_por_vencimento([serializar_item(i) for i in itens if i.status == "ok"])

    _ps = precos_medios_sistema(db)
    prejuizo = sum(valor_efetivo(i, _ps) * (i.quantidade or 1) for i in itens if i.status == "vencido")
    em_risco = sum(valor_efetivo(i, _ps) * (i.quantidade or 1) for i in itens if i.status == "atencao")

    return {
        "usuario": {
            "telefone": usuario.telefone,
            "nome": usuario.nome,
            "email": usuario.email,
            "bairro": usuario.bairro,
            "genero": usuario.genero,
            "data_nascimento": str(usuario.data_nascimento) if usuario.data_nascimento else None,
            "endereco": usuario.endereco_completo,
            "instrucoes_portaria": usuario.instrucoes_portaria,
            "is_admin": usuario.telefone == ADMIN_PHONE,
            "tem_email": bool(usuario.email),
            "aceite_lgpd": bool(usuario.aceite_lgpd)
        },
        "prejuizo_acumulado": prejuizo,
        "valor_em_risco": em_risco,
        "estoque": {"vencidos": vencidos, "atencao": atencao, "ok": ok}
    }


@router.get("/historico/{telefone}")
def historico(telefone: str, usuario_auth: Usuario = Depends(get_usuario_autenticado), db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    if usuario_auth.telefone != telefone:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    itens = (
        db.query(Estoque)
        .filter(Estoque.usuario_id == telefone, Estoque.status == "consumido")
        .order_by(Estoque.data_consumo.desc().nulls_last(), Estoque.id.desc())
        .all()
    )

    consumidos = [serializar_item(i) for i in itens]

    por_mes: dict = {}
    total_valor = 0.0
    for c in consumidos:
        mes = (c["data_consumo"] or "?")[:7]
        if mes not in por_mes:
            por_mes[mes] = {"itens": [], "valor": 0.0}
        valor = (c["preco_real"] or c["preco_estimado"] or 0) * c["quantidade"]
        por_mes[mes]["itens"].append(c)
        por_mes[mes]["valor"] += valor
        total_valor += valor

    meses = [
        {"mes": m, "itens": v["itens"], "valor": round(v["valor"], 2)}
        for m, v in sorted(por_mes.items(), reverse=True)
    ]

    return {
        "total_consumidos": len(consumidos),
        "total_valor_consumido": round(total_valor, 2),
        "por_mes": meses
    }


@router.put("/estoque/{item_id}")
def editar_item(item_id: int, payload: EditarItemPayload, usuario_auth: Usuario = Depends(get_usuario_autenticado), db: Session = Depends(get_db)):
    item = db.query(Estoque).filter(Estoque.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item não encontrado.")
    if item.usuario_id != usuario_auth.telefone:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    if payload.nome_medicamento is not None:
        item.nome_medicamento = sanitizar(payload.nome_medicamento)
        item.preco_estimado, item.categoria_valor = calcular_preco(item.nome_medicamento)
    if payload.principio_ativo is not None:
        item.principio_ativo = sanitizar(payload.principio_ativo)
    if payload.miligramas is not None:
        item.miligramas = sanitizar(payload.miligramas)
    if payload.fabricante is not None:
        item.fabricante = sanitizar(payload.fabricante)
    if payload.validade is not None:
        try:
            item.data_validade = date.fromisoformat(payload.validade) if payload.validade else None
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de validade inválido.")
        item.status = calcular_status(item.data_validade)
    if payload.quantidade is not None:
        item.quantidade = max(1, payload.quantidade)
    if payload.manipulado is not None:
        item.manipulado = 1 if payload.manipulado else 0
    if payload.uso_continuo is not None:
        item.uso_continuo = 1 if payload.uso_continuo else 0
    if payload.preco_real is not None:
        item.preco_real = payload.preco_real if payload.preco_real > 0 else None
    db.commit()
    return {"mensagem": "Medicamento atualizado.", "item": serializar_item(item)}


@router.post("/estoque/{item_id}/iniciar")
def iniciar_item(item_id: int, usuario_auth: Usuario = Depends(get_usuario_autenticado), db: Session = Depends(get_db)):
    item = db.query(Estoque).filter(Estoque.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item não encontrado.")
    if item.usuario_id != usuario_auth.telefone:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    item.iniciado = 0 if item.iniciado else 1
    db.commit()
    return {"iniciado": bool(item.iniciado), "item": serializar_item(item)}


@router.post("/estoque/{item_id}/consumir")
def consumir_item(item_id: int, usuario_auth: Usuario = Depends(get_usuario_autenticado), db: Session = Depends(get_db)):
    item = db.query(Estoque).filter(Estoque.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item não encontrado.")
    if item.usuario_id != usuario_auth.telefone:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    item.status = "consumido"
    item.data_consumo = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"mensagem": f"'{item.nome_medicamento}' marcado como consumido."}


@router.delete("/estoque/{item_id}")
def excluir_item(item_id: int, usuario_auth: Usuario = Depends(get_usuario_autenticado), db: Session = Depends(get_db)):
    item = db.query(Estoque).filter(Estoque.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item não encontrado.")
    if item.usuario_id != usuario_auth.telefone:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    db.delete(item)
    db.commit()
    return {"mensagem": "Medicamento removido."}


@router.post("/reconhecer-imagem")
@limiter.limit("15/hour")
async def reconhecer_imagem(request: Request, arquivo: UploadFile = File(...)):
    import logging
    logger = logging.getLogger("dosemed")

    if not ANTHROPIC_OK:
        raise HTTPException(status_code=503, detail="Reconhecimento de imagem não disponível.")
    api_key = get_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="Chave da API não configurada.")
    try:
        import anthropic
        conteudo = await arquivo.read()
        conteudo = comprimir_imagem(conteudo)
        b64 = base64.standard_b64encode(conteudo).decode("utf-8")
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": (
                    "Esta é uma embalagem de medicamento brasileiro. Leia todas as informações visíveis e retorne SOMENTE um JSON válido, sem texto adicional.\n"
                    "Regras importantes:\n"
                    "1. nome_medicamento: use o nome comercial oficial (como registrado na ANVISA), corrigindo abreviações ou erros de impressão/OCR. Ex: 'Dorflex' e não 'DORFLEX ®'\n"
                    "2. principio_ativo: use a DCI (Denominação Comum Internacional) em português, grafada corretamente. Ex: 'dipirona monoidratada', 'losartana potássica'\n"
                    "3. miligramas: apenas o número e unidade. Ex: '500mg', '10mg/ml'\n"
                    "4. fabricante: nome do laboratório como aparece na embalagem\n"
                    "Se não conseguir ler um campo com confiança, use null.\n"
                    'Retorne exatamente: {"nome_medicamento":"...","principio_ativo":"...","miligramas":"...","fabricante":"..."}'
                )}
            ]}]
        )
        texto = msg.content[0].text.strip()
        try:
            dados = json.loads(texto)
        except Exception:
            match = re.search(r'\{.*\}', texto, re.DOTALL)
            dados = json.loads(match.group()) if match else {}
        logger.info("Imagem reconhecida com sucesso")
        return {
            "nome_medicamento": dados.get("nome_medicamento"),
            "principio_ativo": dados.get("principio_ativo"),
            "miligramas": dados.get("miligramas"),
            "fabricante": dados.get("fabricante"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao reconhecer imagem: {e}")
        raise HTTPException(status_code=500, detail="Erro ao processar imagem.")
