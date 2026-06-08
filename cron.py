# cron.py — background scheduled jobs

import gzip
import json
import logging
import os
import smtplib
from datetime import date, datetime, timedelta, timezone
from email import encoders as _enc
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from database import SessionLocal, get_config
from helpers import (
    calcular_doses_por_dia,
    criar_orcamento_auto,
    enviar_email,
    enviar_push,
    gerar_leads_para_item,
    html_relatorio_farmacia,
)
from models import (
    AlarmeRemedio,
    Estoque,
    Farmacia,
    InteresseAnuncio,
    LogBusca,
    OrcamentoResposta,
    OrcamentoSolicitacao,
    PushSub,
    Usuario,
    Lead,
    AnuncioPush,
)

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    SCHEDULER_OK = True
except ImportError:
    SCHEDULER_OK = False

logger = logging.getLogger("dosemed")


# ---------------------------------------------------------------------------
# Cron jobs
# ---------------------------------------------------------------------------

def cron_notificacoes_vencimento():
    db = SessionLocal()
    try:
        hoje = date.today()
        dias = int(get_config(db, "dias_aviso_vencimento", "30"))
        limite = hoje + timedelta(days=dias)
        usuarios = db.query(Usuario).all()
        leads_gerados = 0

        for u in usuarios:
            itens_atencao = db.query(Estoque).filter(
                Estoque.usuario_id == u.telefone,
                Estoque.status != "consumido",
                Estoque.data_validade.isnot(None),
                Estoque.data_validade <= limite
            ).all()

            if not itens_atencao:
                continue

            for item in itens_atencao:
                leads_gerados += gerar_leads_para_item(db, item, u, origem="cron")

        logger.info(f"[CRON vencimentos] {leads_gerados} leads gerados")
    except Exception as e:
        logger.error(f"[CRON] Erro: {e}")
    finally:
        db.close()


def cron_disparar_alarmes():
    from helpers import PUSH_OK
    if not PUSH_OK:
        return
    db = SessionLocal()
    try:
        agora_brt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
        horario_atual = agora_brt.strftime("%H:%M")
        dia_semana = str(agora_brt.isoweekday())  # 1=seg, 7=dom

        # Desativar alarmes cujo tratamento expirou
        hoje = agora_brt.date()
        expirados = db.query(AlarmeRemedio).filter(
            AlarmeRemedio.ativo == 1,
            AlarmeRemedio.dias_tratamento.isnot(None),
        ).all()
        for a in expirados:
            if a.criado_em and a.dias_tratamento:
                encerra = (a.criado_em + timedelta(days=a.dias_tratamento)).date()
                if encerra <= hoje:
                    a.ativo = 0
        db.commit()

        alarmes = db.query(AlarmeRemedio).filter(
            AlarmeRemedio.ativo == 1,
            AlarmeRemedio.horario == horario_atual
        ).all()

        disparados = 0
        for a in alarmes:
            if dia_semana not in a.dias.split(","):
                continue
            subs = db.query(PushSub).filter(PushSub.usuario_id == a.usuario_id).all()
            for sub in subs:
                tag = f"alarme-{a.nome_med[:30].lower().replace(' ', '-')}"
                if enviar_push(sub, "💊 DoseMed", f"Hora de tomar {a.nome_med}", tag=tag):
                    disparados += 1
        if disparados:
            logger.info(f"[ALARMES] {disparados} notificações push enviadas ({horario_atual} BRT)")
    except Exception as e:
        logger.error(f"[ALARMES] Erro: {e}")
    finally:
        db.close()


def cron_verificar_estoque_baixo():
    """Notifica via push usuários cujo estoque (em uso) está prestes a acabar.
    Para medicamentos de uso contínuo, cria orçamento automático."""
    db = SessionLocal()
    try:
        dias_alerta = int(get_config(db, "dias_alerta_reposicao", "5"))
        itens = db.query(Estoque).filter(
            Estoque.iniciado == 1,
            Estoque.status != "consumido"
        ).all()
        notificados = 0
        auto_criados = 0
        for item in itens:
            doses = calcular_doses_por_dia(db, item.usuario_id, item.nome_medicamento)
            if not doses or doses <= 0:
                continue
            dias_restantes = (item.quantidade or 1) / doses
            if dias_restantes > dias_alerta:
                continue
            usuario = db.query(Usuario).filter(Usuario.telefone == item.usuario_id).first()
            nome = usuario.nome.split()[0] if usuario else "você"

            if item.uso_continuo:
                sol_id, enviados = criar_orcamento_auto(db, item, usuario)
                if sol_id:
                    auto_criados += 1
                    subs = db.query(PushSub).filter(PushSub.usuario_id == item.usuario_id).all()
                    for sub in subs:
                        msg = (f"{nome}, criamos um orçamento automático para {item.nome_medicamento}. "
                               f"Aguarde as propostas no app!")
                        if enviar_push(sub, "🔄 Reposição automática!", msg):
                            notificados += 1
                continue  # uso_continuo sem sol ativa já enviou push acima (ou já havia sol)

            subs = db.query(PushSub).filter(PushSub.usuario_id == item.usuario_id).all()
            if not subs:
                continue
            for sub in subs:
                if enviar_push(sub, "💊 Estoque baixo!",
                               f"{nome}, {item.nome_medicamento} está acabando "
                               f"(≈{max(1, int(dias_restantes))} dia(s)). Deseja reabastecer?"):
                    notificados += 1

        if notificados or auto_criados:
            logger.info(f"[ESTOQUE BAIXO] {notificados} push enviados, {auto_criados} orçamentos automáticos")
    except Exception as e:
        logger.error(f"[ESTOQUE BAIXO] Erro: {e}")
    finally:
        db.close()


def cron_encerrar_orcamentos_expirados():
    """Marca como sem_resposta as solicitações que expiraram sem nenhuma proposta."""
    from helpers import PUSH_OK
    db = SessionLocal()
    try:
        agora = datetime.now(timezone.utc).replace(tzinfo=None)
        expiradas = db.query(OrcamentoSolicitacao).filter(
            OrcamentoSolicitacao.status == "coletando",
            OrcamentoSolicitacao.expira_em <= agora
        ).all()
        for sol in expiradas:
            sol.status = "sem_resposta"
            if PUSH_OK:
                subs = db.query(PushSub).filter(PushSub.usuario_id == sol.usuario_id).all()
                for sub in subs:
                    enviar_push(sub, "💊 DoseMed",
                                f"Nenhuma farmácia respondeu para {sol.nome_med}. Tente de novo pelo app.")
        if expiradas:
            db.commit()
            logger.info(f"[ORCAMENTOS] {len(expiradas)} solicitações encerradas como sem_resposta")
    except Exception as e:
        logger.error(f"[ORCAMENTOS EXPIRADOS] Erro: {e}")
    finally:
        db.close()


def cron_expirar_interesses():
    db = SessionLocal()
    try:
        agora = datetime.now(timezone.utc).replace(tzinfo=None)
        vencidos = db.query(InteresseAnuncio).filter(
            InteresseAnuncio.status == "ativo",
            InteresseAnuncio.expira_em.isnot(None),
            InteresseAnuncio.expira_em <= agora
        ).all()
        for i in vencidos:
            i.status = "expirado"
        cutoff = agora - timedelta(days=7)
        db.query(InteresseAnuncio).filter(
            InteresseAnuncio.status == "expirado",
            InteresseAnuncio.expira_em <= cutoff
        ).delete()
        db.commit()
        if vencidos:
            logger.info(f"[CRON interesses] {len(vencidos)} expirados")
    except Exception as e:
        logger.error(f"[CRON interesses] Erro: {e}")
    finally:
        db.close()


def cron_backup_db():
    """Dump JSON de todas as tabelas principais, comprimido e enviado por e-mail."""
    backup_email = os.getenv("BACKUP_EMAIL", os.getenv("SMTP_USER", "")).strip()
    if not backup_email:
        logger.warning("[BACKUP] BACKUP_EMAIL não configurado — backup ignorado")
        return

    db = SessionLocal()
    try:
        tabelas = [
            ("usuarios",              Usuario),
            ("estoque",               Estoque),
            ("farmacias",             Farmacia),
            ("alarmes",               AlarmeRemedio),
            ("leads",                 Lead),
            ("push_subs",             PushSub),
            ("orcamentos_solic",      OrcamentoSolicitacao),
            ("orcamentos_resp",       OrcamentoResposta),
        ]

        def _serial(o):
            if hasattr(o, "isoformat"):
                return o.isoformat()
            return str(o)

        dump: dict = {}
        for nome, modelo in tabelas:
            rows = db.query(modelo).all()
            dump[nome] = [
                {c.name: getattr(row, c.name) for c in modelo.__table__.columns}
                for row in rows
            ]

        json_bytes = json.dumps(dump, default=_serial, ensure_ascii=False).encode("utf-8")
        gz_bytes   = gzip.compress(json_bytes, compresslevel=9)

        hoje    = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
        n_users = len(dump.get("usuarios", []))
        n_meds  = len(dump.get("estoque",  []))
        fname   = f"dosemed-backup-{hoje}.json.gz"

        host  = os.getenv("SMTP_HOST",     "").strip()
        port  = int(os.getenv("SMTP_PORT", "587").strip() or "587")
        user  = os.getenv("SMTP_USER",     "").strip()
        senha = os.getenv("SMTP_PASSWORD", "").replace(" ", "")

        if not (host and user and senha):
            logger.warning("[BACKUP] SMTP não configurado — backup não enviado")
            return

        import socket, ssl as _ssl
        msg = MIMEMultipart()
        msg["Subject"] = f"DoseMed — Backup diário {hoje}"
        msg["From"]    = user
        msg["To"]      = backup_email
        msg.attach(MIMEText(
            f"<p style='font-family:Arial'>Backup automático de <b>{hoje}</b>.<br>"
            f"Usuários: <b>{n_users}</b> · Itens em estoque: <b>{n_meds}</b><br>"
            f"Tamanho: {len(gz_bytes)/1024:.1f} KB (gzip)</p>",
            "html"
        ))
        part = MIMEBase("application", "octet-stream")
        part.set_payload(gz_bytes)
        _enc.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
        msg.attach(part)

        try:
            host_ip = socket.gethostbyname(host)
            ctx = _ssl.create_default_context()
            if port == 465:
                with smtplib.SMTP_SSL(host_ip, port, context=ctx, timeout=20) as s:
                    s.login(user, senha)
                    s.sendmail(user, backup_email, msg.as_string())
            else:
                with smtplib.SMTP(host_ip, port, timeout=20) as s:
                    s.ehlo(); s.starttls(context=ctx); s.login(user, senha)
                    s.sendmail(user, backup_email, msg.as_string())
            logger.info(f"[BACKUP] {fname} enviado para {backup_email} ({len(gz_bytes)/1024:.1f} KB)")
        except Exception as e:
            logger.error(f"[BACKUP] Falha ao enviar e-mail: {e}")

    except Exception as e:
        logger.error(f"[BACKUP] Erro ao gerar dump: {e}")
    finally:
        db.close()


def cron_limpar_log_busca():
    """Remove registros de LogBusca com mais de 90 dias — política de retenção LGPD."""
    db = SessionLocal()
    try:
        corte = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=90)
        deletados = db.query(LogBusca).filter(LogBusca.timestamp < corte).delete()
        db.commit()
        if deletados:
            logger.info(f"[LGPD] {deletados} registros de LogBusca expirados removidos (>90 dias)")
    except Exception as e:
        logger.error(f"[LGPD] Erro ao limpar log_buscas: {e}")
    finally:
        db.close()


def cron_relatorio_semanal_farmacias():
    """Envia relatório semanal segmentado (sexo, faixa etária, bairro) para farmácias Pro/Manipulado."""
    db = SessionLocal()
    try:
        agora = datetime.now(timezone.utc).replace(tzinfo=None)
        inicio_semana = agora - timedelta(days=7)

        farmacias = db.query(Farmacia).filter(
            Farmacia.ativo == 1,
            Farmacia.plano.in_(["pro", "manipulado"]),
            Farmacia.email.isnot(None),
        ).all()

        semana_str = inicio_semana.strftime("%d/%m") + " a " + (agora - timedelta(days=1)).strftime("%d/%m/%Y")
        enviados = 0

        for f in farmacias:
            bairros_f = [b.lower().strip() for b in (f.bairros or "").split(",") if b.strip()]

            # Buscas da semana no bairro da farmácia
            q = db.query(LogBusca).filter(LogBusca.timestamp >= inicio_semana)
            if bairros_f:
                q = q.filter(LogBusca.bairro.in_(bairros_f))

            logs = q.all()
            if not logs:
                continue

            # Top medicamentos e sintomas
            med_count: dict = {}
            sint_count: dict = {}
            for l in logs:
                if l.tipo == "medicamento":
                    med_count[l.termo] = med_count.get(l.termo, 0) + 1
                elif l.tipo in ("sintoma", "bula"):
                    sint_count[l.termo] = sint_count.get(l.termo, 0) + 1

            buscas_med = sorted([{"termo": k, "total": v} for k, v in med_count.items()], key=lambda x: -x["total"])
            buscas_sint = sorted([{"termo": k, "total": v} for k, v in sint_count.items()], key=lambda x: -x["total"])

            # Perfil demográfico — usa campos gravados no próprio log (usuario_id é hash irreversível)
            por_sexo: dict = {"F": 0, "M": 0, "?": 0}
            por_faixa: dict = {"<18": 0, "18-35": 0, "36-55": 0, "56+": 0, "?": 0}
            por_bairro: dict = {}
            vistos: set = set()  # evita contar o mesmo hash duas vezes no mesmo relatório

            ano_atual = agora.year
            for l in logs:
                uid = l.usuario_id
                if uid and uid not in vistos:
                    vistos.add(uid)
                    g = l.genero if l.genero in ("F", "M") else "?"
                    por_sexo[g] = por_sexo.get(g, 0) + 1
                    if l.ano_nasc:
                        idade = ano_atual - l.ano_nasc
                        if idade < 18:
                            por_faixa["<18"] += 1
                        elif idade <= 35:
                            por_faixa["18-35"] += 1
                        elif idade <= 55:
                            por_faixa["36-55"] += 1
                        else:
                            por_faixa["56+"] += 1
                    else:
                        por_faixa["?"] += 1

                if l.bairro:
                    por_bairro[l.bairro] = por_bairro.get(l.bairro, 0) + 1

            html = html_relatorio_farmacia(f.nome, semana_str, buscas_med, buscas_sint,
                                           por_sexo, por_faixa, por_bairro)
            ok, err = enviar_email(f.email, f"DoseMed — Relatório semanal · {semana_str}", html)
            if ok:
                enviados += 1
            else:
                logger.warning(f"[RELATORIO] Falha ao enviar para {f.nome}: {err}")

        logger.info(f"[RELATORIO] {enviados} relatórios semanais enviados")
    except Exception as e:
        logger.error(f"[RELATORIO] Erro: {e}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

if SCHEDULER_OK:
    _scheduler = BackgroundScheduler(daemon=True)
    # Horários em UTC: 12:00 UTC = 09:00 BRT (UTC-3)
    _scheduler.add_job(cron_notificacoes_vencimento, CronTrigger(hour=12, minute=0),
                       id="vencimentos", replace_existing=True, max_instances=1, coalesce=True)
    _scheduler.add_job(cron_disparar_alarmes, CronTrigger(minute="*"),
                       id="alarmes", replace_existing=True, max_instances=1, coalesce=True)
    _scheduler.add_job(cron_verificar_estoque_baixo, CronTrigger(hour=12, minute=15),
                       id="estoque_baixo", replace_existing=True, max_instances=1, coalesce=True)
    _scheduler.add_job(cron_encerrar_orcamentos_expirados, CronTrigger(minute="*/5"),
                       id="orcamentos_expirados", replace_existing=True, max_instances=1, coalesce=True)
    _scheduler.add_job(cron_expirar_interesses, CronTrigger(hour=3, minute=0),
                       id="interesses", replace_existing=True, max_instances=1, coalesce=True)
    _scheduler.add_job(cron_backup_db, CronTrigger(hour=6, minute=30),
                       id="backup_db", replace_existing=True, max_instances=1, coalesce=True)
    _scheduler.add_job(cron_relatorio_semanal_farmacias, CronTrigger(day_of_week="mon", hour=12, minute=0),
                       id="relatorio_semanal", replace_existing=True, max_instances=1, coalesce=True)
    _scheduler.add_job(cron_limpar_log_busca, CronTrigger(hour=4, minute=0),
                       id="limpar_log_busca", replace_existing=True, max_instances=1, coalesce=True)
else:
    _scheduler = None
