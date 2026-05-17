# routers/publico.py — páginas públicas sem autenticação (LGPD, Termos)

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_STYLE = """
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo} — DoseMed</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #f9fafb; color: #374151; line-height: 1.65; font-size: 15px; }
  .wrap { max-width: 720px; margin: 0 auto; padding: 32px 20px 64px; }
  header { margin-bottom: 32px; }
  .logo { display: inline-flex; align-items: center; gap: 8px; font-size: 20px;
          font-weight: 800; color: #2563eb; text-decoration: none; }
  .logo span { font-size: 24px; }
  nav { display: flex; gap: 8px; margin-top: 24px; flex-wrap: wrap; }
  nav a { padding: 7px 16px; border-radius: 999px; font-size: 13px; font-weight: 600;
          text-decoration: none; transition: background .15s; }
  nav a.ativo { background: #2563eb; color: #fff; }
  nav a:not(.ativo) { background: #e5e7eb; color: #6b7280; }
  nav a:not(.ativo):hover { background: #d1d5db; }
  .voltar { display: inline-flex; align-items: center; gap: 6px; font-size: 13px;
            color: #9ca3af; text-decoration: none; margin-top: 8px; }
  .voltar:hover { color: #6b7280; }
  h1 { font-size: 22px; font-weight: 800; color: #111827; margin-bottom: 6px; }
  .subtitulo { font-size: 13px; color: #9ca3af; margin-bottom: 32px; }
  section { margin-bottom: 28px; }
  h2 { font-size: 13px; font-weight: 700; text-transform: uppercase;
       letter-spacing: .05em; color: #2563eb; margin-bottom: 10px; }
  p { color: #4b5563; margin-bottom: 8px; }
  ul { padding-left: 20px; color: #4b5563; }
  ul li { margin-bottom: 5px; }
  .box { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
         padding: 16px 20px; margin-top: 8px; }
  .box p, .box li { font-size: 14px; }
  .tag { display: inline-block; background: #eff6ff; color: #1d4ed8;
         font-size: 11px; font-weight: 700; padding: 2px 8px;
         border-radius: 999px; margin-bottom: 8px; }
  .alerta { background: #fef3c7; border: 1px solid #fde68a; border-radius: 10px;
             padding: 12px 16px; font-size: 13px; color: #92400e; }
  .dpo { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 10px;
         padding: 14px 18px; }
  .dpo p { color: #166534; font-size: 14px; margin-bottom: 4px; }
  .dpo a { color: #15803d; }
  footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid #e5e7eb;
           font-size: 12px; color: #9ca3af; text-align: center; }
  footer a { color: #9ca3af; }
  @media (max-width: 480px) { h1 { font-size: 19px; } }
</style>
"""

_ATUALIZADO = "16 de maio de 2026"


@router.get("/privacidade", response_class=HTMLResponse, include_in_schema=False)
def pagina_privacidade():
    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>{_STYLE.replace('{titulo}', 'Política de Privacidade')}</head>
<body>
<div class="wrap">
  <header>
    <a class="logo" href="/"><span>💊</span> DoseMed</a>
    <a class="voltar" href="/">&#8592; voltar ao app</a>
    <nav>
      <a href="/privacidade" class="ativo">Política de Privacidade</a>
      <a href="/termos">Termos de Uso</a>
    </nav>
  </header>

  <h1>Política de Privacidade</h1>
  <p class="subtitulo">Última atualização: {_ATUALIZADO} · Em conformidade com a LGPD — Lei nº 13.709/2018</p>

  <section>
    <h2>1. Quem somos</h2>
    <div class="box">
      <p><strong>DoseMed</strong> é um serviço de controle de medicamentos em casa, disponível em <strong>dosemed.app</strong>. O responsável pelo tratamento de dados pessoais é o operador do serviço DoseMed.</p>
      <p>Este documento explica como coletamos, usamos e protegemos seus dados, em conformidade com a Lei Geral de Proteção de Dados Pessoais (LGPD — Lei nº 13.709/2018).</p>
    </div>
  </section>

  <section>
    <h2>2. Dados coletados e finalidades</h2>
    <div class="box">
      <span class="tag">Base legal: consentimento (art. 7º, I) e legítimo interesse (art. 7º, IX)</span>
      <ul>
        <li><strong>Número de telefone</strong> — identificação única da conta. Nunca é exibido a terceiros.</li>
        <li><strong>Nome</strong> — exibição no app. Obrigatório.</li>
        <li><strong>E-mail</strong> (opcional) — recuperação de PIN e comunicações transacionais.</li>
        <li><strong>Bairro e cidade</strong> (opcionais) — localização regional para alertas de farmácias próximas.</li>
        <li><strong>Data de nascimento</strong> (opcional) — cálculo de faixa etária para relatórios regionais anônimos enviados a farmácias parceiras. Apenas o <em>ano</em> é usado nos relatórios.</li>
        <li><strong>Sexo</strong> (opcional) — mesma finalidade dos relatórios regionais, de forma agregada e anônima.</li>
        <li><strong>Medicamentos do estoque</strong> — funcionalidade principal do serviço.</li>
        <li><strong>Termos de busca</strong> — armazenados com identificador pseudonimizado (hash SHA-256 do telefone, irreversível) por até 90 dias, para melhoria do serviço.</li>
      </ul>
    </div>
  </section>

  <section>
    <h2>3. Dados que NÃO coletamos</h2>
    <div class="box">
      <ul>
        <li>Endereço IP — não é armazenado em nenhuma tabela</li>
        <li>Cookies de rastreamento — não usamos Google Analytics, Meta Pixel ou ferramentas similares</li>
        <li>Dados de pagamento — processados diretamente pelo Asaas (farmácias) sem trânsito pelo DoseMed</li>
        <li>Localização GPS — nunca solicitada</li>
      </ul>
    </div>
  </section>

  <section>
    <h2>4. Prazo de retenção</h2>
    <div class="box">
      <ul>
        <li><strong>Dados da conta</strong> — mantidos enquanto a conta estiver ativa.</li>
        <li><strong>Logs de busca</strong> — deletados automaticamente após <strong>90 dias</strong>.</li>
        <li><strong>Exclusão de conta</strong> — todos os dados (estoque, alarmes, histórico, logs) são removidos de forma imediata e permanente. Ver art. 18, VI da LGPD.</li>
      </ul>
    </div>
  </section>

  <section>
    <h2>5. Compartilhamento de dados</h2>
    <div class="box">
      <p>O DoseMed <strong>não vende dados pessoais</strong>. O compartilhamento ocorre apenas nos casos:</p>
      <ul>
        <li><strong>Farmácias parceiras</strong> — recebem apenas: bairro do usuário, nome e dados do medicamento, quando o usuário <em>solicita explicitamente</em> um orçamento. O telefone nunca é compartilhado.</li>
        <li><strong>Relatórios regionais</strong> — farmácias Pro/Manipulado recebem dados <em>agregados e anônimos</em> (ex.: "30% dos usuários da região são do sexo feminino"). Nenhum usuário é identificado individualmente.</li>
        <li><strong>Asaas</strong> — processador de pagamentos para cobrança de farmácias. Dados de pacientes nunca são transmitidos.</li>
      </ul>
    </div>
  </section>

  <section>
    <h2>6. Seus direitos (LGPD art. 18)</h2>
    <div class="box">
      <ul>
        <li><strong>Acesso</strong> — baixe todos os seus dados pelo botão "Baixar meus dados" no perfil.</li>
        <li><strong>Correção</strong> — edite nome, e-mail, bairro e demais informações diretamente no perfil.</li>
        <li><strong>Exclusão</strong> — use "Excluir minha conta" no perfil. A remoção é imediata e total.</li>
        <li><strong>Portabilidade</strong> — o arquivo de exportação está em formato JSON legível por qualquer sistema.</li>
        <li><strong>Revogação do consentimento</strong> — entre em contato com o encarregado abaixo.</li>
        <li><strong>Oposição</strong> — você pode opor-se a qualquer tratamento não essencial pelo mesmo canal.</li>
      </ul>
    </div>
  </section>

  <section>
    <h2>7. Segurança</h2>
    <div class="box">
      <ul>
        <li>Autenticação por PIN com hash SHA-256 — a senha nunca é armazenada em texto claro.</li>
        <li>Comunicação exclusivamente via HTTPS.</li>
        <li>Tokens de sessão revogados a cada logout.</li>
        <li>Cabeçalhos de segurança HTTP (X-Frame-Options, X-Content-Type-Options, CSP) aplicados em todas as respostas.</li>
        <li>Backups diários com criptografia em trânsito.</li>
      </ul>
    </div>
  </section>

  <section>
    <h2>8. Encarregado de dados (DPO)</h2>
    <div class="dpo">
      <p><strong>Encarregado:</strong> Equipe DoseMed</p>
      <p><strong>E-mail:</strong> <a href="mailto:localclientboost@gmail.com">localclientboost@gmail.com</a></p>
      <p style="margin-top:8px;font-size:13px;">Respondemos todas as solicitações relacionadas a dados pessoais em até 15 dias úteis, conforme art. 18, §5º da LGPD.</p>
    </div>
  </section>

  <section>
    <h2>9. Alterações nesta política</h2>
    <p>Qualquer atualização relevante será comunicada no app antes de entrar em vigor. A data da última atualização é sempre exibida no topo desta página.</p>
  </section>

  <div class="alerta">
    ⚠️ As informações de bula disponíveis no DoseMed têm caráter exclusivamente educativo e <strong>não substituem orientação médica ou farmacêutica</strong>. Em emergências, ligue <strong>192 (SAMU)</strong> ou <strong>193 (Bombeiros)</strong>.
  </div>

  <footer>
    <p>DoseMed · <a href="/privacidade">Privacidade</a> · <a href="/termos">Termos de Uso</a> · <a href="mailto:localclientboost@gmail.com">localclientboost@gmail.com</a></p>
    <p style="margin-top:6px">Última atualização: {_ATUALIZADO}</p>
  </footer>
</div>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/termos", response_class=HTMLResponse, include_in_schema=False)
def pagina_termos():
    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>{_STYLE.replace('{titulo}', 'Termos de Uso')}</head>
<body>
<div class="wrap">
  <header>
    <a class="logo" href="/"><span>💊</span> DoseMed</a>
    <a class="voltar" href="/">&#8592; voltar ao app</a>
    <nav>
      <a href="/privacidade">Política de Privacidade</a>
      <a href="/termos" class="ativo">Termos de Uso</a>
    </nav>
  </header>

  <h1>Termos de Uso</h1>
  <p class="subtitulo">Última atualização: {_ATUALIZADO} · Leia antes de usar o DoseMed</p>

  <section>
    <h2>1. O que é o DoseMed</h2>
    <div class="box">
      <p>O DoseMed é uma ferramenta digital para <strong>controle pessoal de medicamentos em casa</strong>: estoque, validades, alarmes de horário e orçamento com farmácias.</p>
      <div class="alerta" style="margin-top:12px">
        🩺 O DoseMed <strong>não é um serviço médico</strong>. Nenhuma informação do app substitui consulta com médico, farmacêutico ou profissional de saúde habilitado.
      </div>
    </div>
  </section>

  <section>
    <h2>2. Aceitação</h2>
    <div class="box">
      <p>Ao se cadastrar e aceitar estes termos, você confirma que tem pelo menos 18 anos (ou usa o serviço com supervisão de responsável legal) e concorda com estas condições. O uso continuado após qualquer atualização equivale à aceitação das novas regras.</p>
    </div>
  </section>

  <section>
    <h2>3. Responsabilidades do usuário</h2>
    <div class="box">
      <ul>
        <li>Manter o PIN em segurança e não compartilhar a conta.</li>
        <li>Inserir informações corretas sobre os medicamentos — o app não valida prescrições médicas.</li>
        <li>Consultar sempre o profissional de saúde antes de iniciar, alterar ou suspender qualquer tratamento.</li>
        <li>Não usar o serviço para fins ilícitos ou em desacordo com a legislação brasileira.</li>
      </ul>
    </div>
  </section>

  <section>
    <h2>4. Limitações do serviço</h2>
    <div class="box">
      <ul>
        <li><strong>Alarmes e notificações</strong> dependem de o dispositivo estar conectado à internet e com as notificações push ativadas. O DoseMed não garante entrega em 100% das situações.</li>
        <li><strong>Informações de bula</strong> são fornecidas como referência educacional. Podem estar desatualizadas — sempre consulte a bula original do fabricante.</li>
        <li><strong>Orçamentos com farmácias</strong> são acordos entre o usuário e a farmácia. O DoseMed facilita o contato mas não é parte da transação comercial e não garante preço, prazo ou disponibilidade.</li>
        <li><strong>Disponibilidade</strong> — o serviço é fornecido "como está". Pode haver interrupções por manutenção ou causas técnicas. Não garantimos disponibilidade de 100%.</li>
      </ul>
    </div>
  </section>

  <section>
    <h2>5. Planos e cobranças (farmácias)</h2>
    <div class="box">
      <p>Os planos pagos (Básico, Pro, Manipulado) são destinados exclusivamente a farmácias cadastradas. O faturamento é mensal via Asaas (PIX, cartão ou boleto). Não há fidelidade — o cancelamento pode ser feito a qualquer momento sem multa, com efeito no próximo ciclo de cobrança.</p>
      <p>O DoseMed pode ajustar preços mediante aviso prévio de 30 dias por e-mail.</p>
    </div>
  </section>

  <section>
    <h2>6. Conteúdo do usuário</h2>
    <div class="box">
      <p>Os dados inseridos (estoque, alarmes, buscas) pertencem ao usuário. O DoseMed não reivindica propriedade sobre eles. Ao usar o serviço, você concede ao DoseMed licença limitada para processar esses dados com a única finalidade de prestar o serviço descrito nestes termos.</p>
    </div>
  </section>

  <section>
    <h2>7. Cancelamento e exclusão</h2>
    <div class="box">
      <p>Você pode excluir sua conta a qualquer momento pelo perfil no app (Perfil → Excluir minha conta). Todos os dados são removidos imediatamente e de forma permanente, conforme a Política de Privacidade.</p>
    </div>
  </section>

  <section>
    <h2>8. Propriedade intelectual</h2>
    <div class="box">
      <p>O código, design, marca e conteúdo do DoseMed são protegidos por direitos autorais. É proibido reproduzir, distribuir ou criar obras derivadas sem autorização expressa.</p>
    </div>
  </section>

  <section>
    <h2>9. Lei aplicável e foro</h2>
    <div class="box">
      <p>Estes termos são regidos pelas leis da República Federativa do Brasil. Eventuais conflitos serão resolvidos no foro da comarca onde o serviço está registrado, com renúncia a qualquer outro por mais privilegiado que seja.</p>
    </div>
  </section>

  <section>
    <h2>10. Contato</h2>
    <div class="dpo">
      <p><strong>Dúvidas sobre estes termos:</strong> <a href="mailto:localclientboost@gmail.com">localclientboost@gmail.com</a></p>
      <p><strong>Suporte geral:</strong> <a href="mailto:localclientboost@gmail.com">localclientboost@gmail.com</a></p>
    </div>
  </section>

  <footer>
    <p>DoseMed · <a href="/privacidade">Privacidade</a> · <a href="/termos">Termos de Uso</a> · <a href="mailto:localclientboost@gmail.com">localclientboost@gmail.com</a></p>
    <p style="margin-top:6px">Última atualização: {_ATUALIZADO}</p>
  </footer>
</div>
</body>
</html>"""
    return HTMLResponse(content=html)
