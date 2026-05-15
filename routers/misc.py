# routers/misc.py — /health, /, static files, /feedback, /log-busca, /exportar/*, /usuario/*/exportar-dados, /webhook/asaas

import os
import re
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from database import get_db, get_usuario_autenticado, limiter
from helpers import (
    atualizar_status_estoque,
    precos_medios_sistema,
    registrar_busca,
    sanitizar,
    serializar_item,
    sort_por_vencimento,
    validar_telefone,
    valor_efetivo,
)
from models import (
    AlarmeRemedio,
    AnuncioPush,
    Estoque,
    Feedback,
    Lead,
    LogBusca,
    LogEvento,
    OrcamentoSolicitacao,
    Usuario,
)
from schemas import FeedbackPayload, LogBuscaPayload

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/")
def root():
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "..", "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


@router.get("/manifest.json")
def manifest():
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "..", "manifest.json"),
        media_type="application/manifest+json"
    )


@router.get("/sw.js")
def service_worker():
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "..", "sw.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


@router.get("/icon-{size}.png")
def icone(size: str):
    path = os.path.join(os.path.dirname(__file__), "..", f"icon-{size}.png")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Ícone não encontrado.")
    return FileResponse(path, media_type="image/png")


@router.post("/log-busca")
def log_busca(payload: LogBuscaPayload, db: Session = Depends(get_db)):
    registrar_busca(db, payload.tipo, payload.termo, payload.usuario_id)
    return {"ok": True}


@router.post("/feedback")
@limiter.limit("5/hour")
async def enviar_feedback(request: Request, payload: FeedbackPayload, db: Session = Depends(get_db)):
    if not (1 <= payload.nota <= 5):
        raise HTTPException(status_code=400, detail="Nota deve ser entre 1 e 5.")
    if payload.categoria not in {"sugestao", "elogio", "reclamacao", "bug"}:
        raise HTTPException(status_code=400, detail="Categoria inválida.")
    msg = re.sub(r"[<>]", "", (payload.mensagem or "").strip())[:1000]
    if len(msg) < 5:
        raise HTTPException(status_code=400, detail="Mensagem muito curta.")
    fb = Feedback(
        usuario_id=re.sub(r"\D", "", payload.usuario_id or "")[:20] or None,
        nota=payload.nota,
        categoria=payload.categoria,
        mensagem=msg,
        cidade=sanitizar(payload.cidade or "")[:100] or None,
        bairro=sanitizar(payload.bairro or "")[:100] or None,
    )
    db.add(fb)
    db.commit()
    import logging
    logging.getLogger("dosemed").info(f"Feedback recebido: nota={payload.nota} cat={payload.categoria}")
    return {"ok": True}


@router.get("/exportar/{telefone}", response_class=HTMLResponse)
def exportar(telefone: str, db: Session = Depends(get_db)):
    telefone = validar_telefone(telefone)
    usuario = db.query(Usuario).filter(Usuario.telefone == telefone).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    atualizar_status_estoque(db, telefone)
    itens = db.query(Estoque).filter(
        Estoque.usuario_id == telefone,
        Estoque.status != "consumido"
    ).all()

    def linhas(grupo):
        items_grupo = sort_por_vencimento([serializar_item(i) for i in itens if i.status == grupo])
        if not items_grupo:
            return '<tr><td colspan="6" style="color:#aaa;font-style:italic;padding:8px 12px">Nenhum item</td></tr>'
        rows = ""
        for i in items_grupo:
            preco = i["preco_real"] if i["preco_real"] else i["preco_estimado"]
            total = preco * i["quantidade"]
            rows += f"""<tr>
              <td>{i['nome']}{'<span class="badge-manip">Manip.</span>' if i['manipulado'] else ''}</td>
              <td>{i['principio_ativo'] or '—'}</td>
              <td>{i['miligramas'] or '—'}</td>
              <td>{i['fabricante'] or '—'}</td>
              <td>{i['data_vencimento'] or '—'}</td>
              <td>R$ {total:.2f}</td>
            </tr>"""
        return rows

    _ps = precos_medios_sistema(db)
    prejuizo = sum(valor_efetivo(i, _ps) * (i.quantidade or 1) for i in itens if i.status == "vencido")
    em_risco = sum(valor_efetivo(i, _ps) * (i.quantidade or 1) for i in itens if i.status == "atencao")
    hoje = date.today().strftime("%d/%m/%Y")

    html = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<title>DoseMed — Estoque de {usuario.nome}</title>
<style>
  body{{font-family:Arial,sans-serif;font-size:12px;color:#333;margin:24px}}
  h1{{color:#2563eb;font-size:18px;margin-bottom:2px}}
  .sub{{color:#888;font-size:11px;margin-bottom:16px}}
  .resumo{{display:flex;gap:24px;margin-bottom:20px}}
  .card{{border:1px solid #eee;border-radius:8px;padding:12px 20px;min-width:140px}}
  .card .val{{font-size:22px;font-weight:900}}
  .red{{color:#ef4444}}.amber{{color:#f59e0b}}.gray{{color:#9ca3af}}
  table{{width:100%;border-collapse:collapse;margin-bottom:20px}}
  th{{background:#f3f4f6;padding:8px 10px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em}}
  td{{padding:7px 10px;border-bottom:1px solid #f3f4f6;vertical-align:top}}
  .sec-title{{font-size:13px;font-weight:bold;margin:16px 0 6px;padding:4px 10px;border-radius:4px}}
  .sec-vencido{{background:#fee2e2;color:#b91c1c}}
  .sec-atencao{{background:#fef3c7;color:#92400e}}
  .sec-ok{{background:#d1fae5;color:#065f46}}
  .badge-manip{{font-size:9px;background:#ede9fe;color:#7c3aed;padding:1px 5px;border-radius:3px;margin-left:4px;vertical-align:middle}}
  .disclaimer{{font-size:10px;color:#9ca3af;border-top:1px solid #eee;padding-top:10px;margin-top:10px}}
  .btn{{display:inline-block;background:#2563eb;color:#fff;padding:8px 20px;border:none;border-radius:8px;cursor:pointer;font-size:13px;margin-bottom:16px}}
  @media print{{.btn{{display:none}}}}
</style></head><body>
<button class="btn" onclick="window.print()">Imprimir / Salvar PDF</button>
<h1>💊 DoseMed — Estoque de Medicamentos</h1>
<div class="sub">{usuario.nome} · {usuario.telefone} · Gerado em {hoje}</div>
<div class="resumo">
  <div class="card"><div style="font-size:11px;color:#888">Prejuízo acumulado</div><div class="val red">R$ {prejuizo:.2f}</div><div style="font-size:10px;color:#aaa">Vencidos</div></div>
  <div class="card"><div style="font-size:11px;color:#888">Em risco</div><div class="val amber">R$ {em_risco:.2f}</div><div style="font-size:10px;color:#aaa">Vencem em 30 dias</div></div>
  <div class="card"><div style="font-size:11px;color:#888">Total de itens</div><div class="val gray">{len(itens)}</div><div style="font-size:10px;color:#aaa">No estoque</div></div>
</div>
<div class="sec-title sec-vencido">Vencidos ({sum(1 for i in itens if i.status=='vencido')})</div>
<table><tr><th>Medicamento</th><th>Princípio ativo</th><th>Dosagem</th><th>Fabricante</th><th>Vencimento</th><th>Valor</th></tr>{linhas('vencido')}</table>
<div class="sec-title sec-atencao">Atenção — Vencem em até 30 dias ({sum(1 for i in itens if i.status=='atencao')})</div>
<table><tr><th>Medicamento</th><th>Princípio ativo</th><th>Dosagem</th><th>Fabricante</th><th>Vencimento</th><th>Valor</th></tr>{linhas('atencao')}</table>
<div class="sec-title sec-ok">Em dia ({sum(1 for i in itens if i.status=='ok')})</div>
<table><tr><th>Medicamento</th><th>Princípio ativo</th><th>Dosagem</th><th>Fabricante</th><th>Vencimento</th><th>Valor</th></tr>{linhas('ok')}</table>
<div class="disclaimer">Relatório gerado pelo DoseMed em {hoje}. Este documento é apenas informativo e não substitui orientação médica ou farmacêutica.</div>
</body></html>"""
    return HTMLResponse(content=html)


@router.get("/usuario/{telefone}/exportar-dados")
def exportar_dados_lgpd(
    telefone: str,
    usuario_auth: Usuario = Depends(get_usuario_autenticado),
    db: Session = Depends(get_db),
):
    """Portabilidade de dados — LGPD art. 17. Retorna JSON com todos os dados do usuário."""
    telefone = validar_telefone(telefone)
    if usuario_auth.telefone != telefone:
        raise HTTPException(status_code=403, detail="Acesso negado.")

    usuario = usuario_auth
    itens = db.query(Estoque).filter(Estoque.usuario_id == telefone).all()
    alarmes = db.query(AlarmeRemedio).filter(AlarmeRemedio.usuario_id == telefone).all()
    orcamentos = db.query(OrcamentoSolicitacao).filter(OrcamentoSolicitacao.usuario_id == telefone).all()
    buscas = db.query(LogBusca).filter(LogBusca.usuario_id == telefone).order_by(LogBusca.timestamp.desc()).limit(500).all()

    def fmt(v):
        if v is None:
            return None
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return v

    payload = {
        "exportado_em": datetime.utcnow().isoformat(),
        "fonte": "DoseMed — dosemed.onrender.com",
        "aviso_lgpd": "Exportação de dados pessoais conforme LGPD art. 17 (direito à portabilidade).",
        "perfil": {
            "nome": usuario.nome,
            "telefone": usuario.telefone,
            "email": usuario.email,
            "bairro": usuario.bairro,
            "genero": usuario.genero,
            "aceite_lgpd": fmt(usuario.aceite_lgpd),
        },
        "estoque": [
            {
                "nome": i.nome_medicamento,
                "principio_ativo": i.principio_ativo,
                "miligramas": i.miligramas,
                "fabricante": i.fabricante,
                "validade": fmt(i.data_validade),
                "quantidade": i.quantidade,
                "manipulado": bool(i.manipulado),
                "uso_continuo": bool(i.uso_continuo),
                "status": i.status,
                "preco_real": i.preco_real,
                "data_consumo": fmt(i.data_consumo),
            }
            for i in itens
        ],
        "alarmes": [
            {
                "medicamento": a.nome_med,
                "horario": a.horario,
                "dias": a.dias,
                "ativo": bool(a.ativo),
            }
            for a in alarmes
        ],
        "orcamentos": [
            {
                "medicamento": o.nome_med,
                "status": o.status,
                "criado_em": fmt(o.criado_em),
            }
            for o in orcamentos
        ],
        "historico_buscas": [
            {
                "tipo": b.tipo,
                "termo": b.termo,
                "data": fmt(b.timestamp),
            }
            for b in buscas
        ],
    }

    nome_arquivo = f"dosemed-dados-{telefone[-4:]}-{datetime.utcnow().strftime('%Y%m%d')}.json"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"'},
    )


@router.post("/webhook/asaas")
async def webhook_asaas(request: Request, db: Session = Depends(get_db)):
    import logging
    logger = logging.getLogger("dosemed")
    try:
        data = await request.json()
        evento = data.get("event", "")
        payment = data.get("payment", {})
        charge_id = payment.get("id", "")
        logger.info(f"[ASAAS] Webhook: {evento} — {charge_id}")
        if evento in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED") and charge_id:
            leads = db.query(Lead).filter(Lead.asaas_charge_id == charge_id).all()
            for l in leads:
                l.status = "pago"
            db.commit()
            anuncio = db.query(AnuncioPush).filter(AnuncioPush.asaas_charge_id == charge_id).first()
            if not anuncio:
                ext_ref = payment.get("externalReference", "")
                if ext_ref.startswith("anuncio_"):
                    try:
                        ad_id = int(ext_ref.split("_", 1)[1])
                        anuncio = db.query(AnuncioPush).filter(AnuncioPush.id == ad_id).first()
                    except (ValueError, IndexError):
                        pass
            if anuncio and anuncio.status == "aguardando_pagamento":
                anuncio.status = "pago"
                db.commit()
                logger.info(f"[ASAAS] Anúncio #{anuncio.id} marcado como pago — aguardando liberação admin")
    except Exception as e:
        import logging as _log
        _log.getLogger("dosemed").error(f"[ASAAS] Webhook erro: {e}")
    return {"ok": True}
