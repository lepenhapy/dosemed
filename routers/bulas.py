# routers/bulas.py — /bulas-index, /bulas/buscar, /bulas/enriquecer, /bulas/{nome}

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db, limiter
from helpers import (
    ANTHROPIC_OK,
    BULAS,
    _salvar_bulas,
    buscar_bula_local,
    get_api_key,
    registrar_busca,
    sanitizar,
)
from schemas import EnriquecerPayload

router = APIRouter()


@router.get("/bulas-index")
def bulas_index():
    return [
        {"nomes": b.get("nomes", []), "principio_ativo": b.get("principio_ativo", ""), "sintomas": b.get("sintomas", [])}
        for b in BULAS
    ]


@router.get("/bulas/buscar")
def buscar_bulas_multi(q: str = "", db: Session = Depends(get_db)):
    """Retorna múltiplos resultados por nome, princípio ativo ou sintoma."""
    termo = q.lower().strip()
    if len(termo) < 2:
        return []
    resultados = []
    for b in BULAS:
        nomes = [n.lower() for n in b.get("nomes", [])]
        pa = b.get("principio_ativo", "").lower()
        sintomas = [s.lower() for s in b.get("sintomas", [])]
        indicacao = b.get("indicacao", "").lower()
        score = 0
        if any(termo in n or n in termo for n in nomes): score = 4
        elif termo in pa or pa in termo:                  score = 3
        elif any(termo in s for s in sintomas):           score = 2
        elif termo in indicacao:                          score = 1
        if score:
            resultados.append((score, b))
    resultados.sort(key=lambda x: -x[0])
    return [b for _, b in resultados[:12]]


@router.get("/bulas/{nome}")
def buscar_bula(nome: str, db: Session = Depends(get_db)):
    resultado = buscar_bula_local(nome)
    if resultado:
        return resultado
    raise HTTPException(status_code=404, detail="Medicamento não encontrado na biblioteca.")


@router.post("/bulas/enriquecer")
@limiter.limit("20/hour")
async def enriquecer_bula(request: Request, payload: EnriquecerPayload, db: Session = Depends(get_db)):
    import helpers as _helpers

    nome = sanitizar(payload.nome)
    pa = sanitizar(payload.principio_ativo or "")
    fabricante = sanitizar(payload.fabricante or "")
    if not nome and not pa:
        raise HTTPException(status_code=400, detail="Nome inválido.")

    termos_genericos = {"medicamento genérico", "genérico", "medicamento", "remédio"}
    termo_busca = pa if nome.lower() in termos_genericos and pa else nome

    existente = buscar_bula_local(termo_busca) or (pa and buscar_bula_local(pa))
    if existente:
        return existente

    if not ANTHROPIC_OK:
        raise HTTPException(status_code=503, detail="Enriquecimento de bulas não disponível.")
    api_key = get_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="Chave da API não configurada.")

    registrar_busca(db, "enriquecimento", termo_busca, payload.usuario_id)

    contexto_extra = ""
    if pa:
        contexto_extra += f"\nPrincípio ativo: {pa}"
    if fabricante:
        contexto_extra += f"\nFabricante: {fabricante}"

    prompt = f"""Você é um farmacêutico especialista. Crie uma entrada de bula resumida e segura para o medicamento "{termo_busca}".{contexto_extra}
Retorne SOMENTE um JSON válido, sem texto adicional, com exatamente esta estrutura:
{{
  "nomes": ["nome comercial", "outros nomes"],
  "principio_ativo": "substância ativa",
  "categoria": "categoria terapêutica",
  "indicacao": "para que serve (2-3 frases)",
  "posologia": "dose e frequência típica",
  "contraindicacoes": "quem não deve usar",
  "avisos": "avisos importantes de segurança",
  "receita": true ou false,
  "sintomas": ["sintoma1", "sintoma2", "sintoma3"],
  "dicas": ["dica prática 1", "dica prática 2", "dica prática 3"]
}}
Sintomas: palavras-chave em português que um paciente usaria para descrever por que tomaria este medicamento.
Dicas: orientações práticas do dia a dia (horário, alimento, duração máxima, cuidados especiais).
Se não conhecer o medicamento com certeza, retorne {{"erro": "desconhecido"}}."""

    import logging
    logger = logging.getLogger("dosemed")

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        texto = msg.content[0].text.strip()
        try:
            dados = json.loads(texto)
        except Exception:
            match = re.search(r'\{.*\}', texto, re.DOTALL)
            dados = json.loads(match.group()) if match else {}

        if "erro" in dados:
            raise HTTPException(status_code=404, detail=f"Medicamento '{nome}' não reconhecido.")

        # Salva no arquivo e na memória global (helpers.BULAS)
        _helpers.BULAS.append(dados)
        _salvar_bulas(_helpers.BULAS)
        logger.info(f"Bula enriquecida e salva: {nome}")
        return dados
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao enriquecer bula de {nome}: {e}")
        raise HTTPException(status_code=500, detail="Erro ao buscar informações do medicamento.")
