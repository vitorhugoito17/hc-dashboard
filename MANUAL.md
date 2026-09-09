# Manual de recuperação — Healthcare Database Dashboard

Escrito em setembro de 2026 para responder a uma pergunta específica: *e se eu perder
o acesso a esta conta do Claude?*

---

## 1. Leia isto primeiro

**Perder a conta do Claude não para nada.** O dashboard não é atualizado pelo Claude.
Ele é atualizado por uma rotina que roda no GitHub Actions, dentro do seu repositório,
no dia 11 de cada mês, sem ninguém logado em lugar nenhum. O Claude escreveu o robô;
quem executa o robô é o GitHub.

Prova disso, hoje: o ambiente onde tudo foi construído já foi reciclado — não existe
mais um único arquivo lá. O repositório continua no ar, com o `atualizador.py` de
149 KB, o HTML de 1,3 MB e a base de dados intactos, prontos para a próxima rodada.

O que você perde ao trocar de conta do Claude:

- a skill salva naquela conta (o texto completo está no **anexo** deste manual);
- o histórico da conversa;
- o contexto que o Claude tinha na cabeça.

O que você **não** perde: o dashboard, o robô, os dados, o histórico de rodadas, o
agendamento. Nada disso está dentro do Claude.

---

## 2. O que é realmente crítico

Só uma coisa: **a conta do GitHub `vitorhugoito17`**. Ela é o ponto único de falha de
verdade. Se você perder essa conta, perde o repositório, o robô e o agendamento.

Vale gastar dez minutos agora com três coisas:

1. **E-mail de recuperação e 2FA** configurados na conta do GitHub, com os códigos de
   backup guardados fora do computador.
2. **Um segundo dono.** Em `Settings → Collaborators` você adiciona alguém da Apex com
   acesso de escrita, ou — melhor — transfere o repositório para uma organização da
   Apex, onde mais de uma pessoa administra.
3. **Uma cópia local do repositório.** Baixe o ZIP em `Code → Download ZIP` de vez em
   quando, ou clone. São poucos megabytes e é a apólice mais barata que existe.

O repositório é público e **não contém nenhuma credencial** — o workflow usa o token
que o próprio GitHub injeta na execução. Não coloque token, senha ou chave lá dentro.

---

## 3. O que tem no repositório

`github.com/vitorhugoito17/hc-dashboard`

| Arquivo | O que é |
|---|---|
| `Healthcare_Database_Dashboard.html` | O dashboard. Arquivo único, abre com duplo clique, funciona offline. |
| `dados.json` | A base viva. É daqui que o dashboard lê quando tem internet. |
| `atualizador.py` | O robô inteiro: coletores da ANS, CNES, CNJ, IBGE e NIP. |
| `.github/workflows/atualizar.yml` | O agendamento e os passos da rodada. |
| `publicar.sh` | O commit do resultado, com rebase e nova tentativa se alguém empurrar no meio. |
| `agregado_ans.json` | Memória do mês anterior, usada para encadear as séries por variação. |
| `cnes_map.json` | O casamento entre as 166 unidades do dashboard e os códigos do CNES. |
| `diagnostico_*.json` | O que cada coletor viu, conferiu e recusou na última rodada. |

O dashboard busca a base nesta ordem: base publicada no GitHub → `dados.json` ao lado
do arquivo → cópia embutida no próprio HTML. Ele desenha na hora com a embutida e
troca em segundo plano quando a publicada chega, então nunca fica tela branca — e se a
rede da Apex bloquear o GitHub, ele simplesmente continua com a embutida.

---

## 4. Como operar sem Claude nenhum

**Forçar uma rodada.** No repositório, aba **Actions** → *Atualizar base do dashboard*
→ **Run workflow**. O campo `so` aceita:

- vazio → rodada completa (IPCA, beneficiários da ANS, leitos do CNES, NIP, CNJ);
- `nip` → só reclamações;
- `cnj` → só judicialização;
- `explorar` → só mapeia o schema das fontes que ainda não têm coletor.

Há ainda `refazer` (reprocessa uma competência da ANS, formato `AAAAMM`) e `descobrir`
(refaz o mapa unidade → CNES).

**Ler o resultado.** Se a rodada terminou verde e o `dados.json` mudou, entrou dado
novo. O selo no topo do dashboard diz até quando vai cada bloco.

**Entender uma recusa.** O robô foi construído sobre uma regra: *nunca gravar um número
que ele não consiga reproduzir*. Antes de escrever, cada coletor recalcula um mês que
a base já tem e compara. Se não bater, ele **não grava nada** e explica no
`diagnostico_*.json`. Rodada que não muda nada não é rodada que falhou — na maioria
das vezes é o robô se recusando a inventar. Vá no diagnóstico antes de mexer.

**Quando ficar preocupado.** Se passarem dois meses sem o `dados.json` mudar, abra a
aba Actions e veja se as execuções estão acontecendo. GitHub desativa workflow agendado
em repositório sem atividade por 60 dias — basta um commit qualquer para reativar.

---

## 5. Se precisar de um Claude novo

Numa conta nova, abra uma conversa e cole isto:

> Tenho um dashboard setorial de saúde suplementar que se atualiza sozinho por um robô
> em `github.com/vitorhugoito17/hc-dashboard`. Leia o `atualizador.py` e o
> `MANUAL.md` desse repositório antes de mexer em qualquer coisa.
>
> A regra da casa: nunca grave um número que você não consiga reproduzir contra um
> período que a base já tem. Se não bater, não grave e me diga por quê. Antes de
> escrever qualquer parser novo, rode uma passada de descoberta que baixe o arquivo
> mais recente da fonte e me mostre cabeçalho e amostra. Quando um coletor errar, faça
> ele se explicar — quais entidades caíram em cada balde, quais recortes candidatos e o
> erro de cada um — em vez de chutar.
>
> O robô roda no GitHub Actions, não na minha máquina: minha rede bloqueia os
> servidores de dados abertos e bloqueia download de `.py`.

Depois, se quiser a skill de volta naquela conta, peça: *"salve como skill o texto que
está no anexo do MANUAL.md do repositório"*.

---

## 6. Onde as coisas pararam (agosto de 2026)

**Automatizado e rodando:** IPCA (IBGE/SIDRA), beneficiários por operadora, região, UF,
contratação e faixa etária (ANS PDA-024), leitos por hospital (CNES), judicialização
(painel do CNJ) e reclamações NIP e IGR (ANS).

**Mapeado, com schema conhecido, sem coletor ainda:**

- **Reajustes** — PDA-043 traz contrato a contrato com `PC_PERCENTUAL` e
  `QT_BENEF_COMUNICADO`; média ponderada dá a série mensal. PDA-055 traz o agrupamento,
  que é o SME. É o mais fácil do que sobrou.
- **DIOPS** — balancete completo (`DATA`, `REG_ANS`, `CD_CONTA_CONTABIL`,
  `VL_SALDO_FINAL`). O mais pesado, e destrava de uma vez DRE, sinistralidade,
  provisões técnicas e depósitos judiciais.
- **SIP / mapa assistencial** — `QT_EVENTOS`, `QT_BENEF_FORA_CARENCIA`,
  `VL_DESPESA_ASST_LIQ` → volume por beneficiário, ticket e custo assistencial.
- **Ressarcimento ao SUS** — atenção: o arquivo de cobrança e arrecadação **não tem
  dimensão de operadora**, só o agregado do mercado. As sete séries por operadora não
  saem dali; o histórico de cobrança (`hc_ressarcimento_sus`) tem `CD_OPERADORA`.

**Tentado e não resolvido:** leitos por UF e por município. Seis recortes do CNES
testados contra os 23.251 leitos de São Paulo da base; o melhor deu 61.416. O universo
que o BBI usa é bem mais estreito do que qualquer filtro simples do cadastro. O
`diagnostico_leitos_uf.json` tem os números de cada tentativa.

**Sem endpoint:** ANAHP e Sindusfarma publicam PDF e planilha. Dá para extrair, mas
quebra quando mudam o layout. Assuma manual.

**Fora de escopo por decisão sua:** o comps (cotações e múltiplos).

---

## Anexo — a skill completa

Guarde este texto. Ele recria, em qualquer conta do Claude, tudo que foi aprendido
construindo isto. Salve como skill com o nome `dashboard-setorial-automatico`.

````markdown
---
name: dashboard-setorial-automatico
description: Construir e manter dashboards setoriais de arquivo único que se atualizam sozinhos a partir de fontes oficiais (ANS, CNES, CNJ, IBGE), com reconciliação obrigatória antes de gravar qualquer número.
---

# Dashboard setorial que se atualiza sozinho

Para construir, a partir de um relatório de sell-side ou de uma planilha-base, um
dashboard que continue certo depois que você sair — puxando as fontes oficiais
sozinho, todo mês, sem ninguém abrir nada.

## O princípio que vale mais que todos os outros

**Nunca grave um número que você não consegue reproduzir.**

Todo coletor, antes de escrever, recalcula um período que a base já tem e compara.
Bate dentro da tolerância? Grava. Não bate? **Não grava nada** e explica por quê.

Isso não é excesso de zelo. As fontes reprocessam o passado, mudam nome de coluna
sem avisar, e a definição de "grupo econômico" de quem publicou o relatório quase
nunca é a mesma do cadastro oficial. Um número errado com cara de certo é pior que
um buraco na série: o buraco você vê.

Três formatos de portão, use o que couber:

- **Por nível** — o valor absoluto reproduz o mês conhecido. É o caso feliz.
- **Por cobertura** — tudo ou nada. Se faltar 1 de 28 arquivos, aborte: um agregado
  parcial não é "quase certo", é errado, e contamina tudo que depende dele.
- **Por variação** — quando o nível não é comparável mas a variação é. Veja adiante.

E sempre: grave um `diagnostico_*.json` no repositório, **mesmo quando recusar**.
É ele que transforma a próxima rodada em conserto em vez de adivinhação.

## Arquitetura

### Onde o robô roda

Não é na máquina do usuário. Redes corporativas bloqueiam os servidores de dados
abertos e bloqueiam download de `.py`, `.ps1`, `.bat` — você entrega o script e ele
nunca chega. Também não é no seu sandbox: o proxy costuma barrar ANS, IBGE, CNES.

Use **GitHub Actions**. Repositório do usuário, workflow com `schedule`, que roda o
coletor e faz commit do `dados.json`. O runner tem internet limpa e é grátis.

Detalhes que economizam rodadas:

- Gere o YAML com `yaml.dump(..., default_flow_style=True)` em vez de escrever na
  mão. Expressão `${{ }}` dentro de mapa em flow style quebra o parser, e o sintoma
  é silencioso: o workflow perde o nome e some da interface.
- `actions/cache/restore` + `actions/cache/save` com `if: always()` sobre a pasta de
  insumos. Download de 400 MB que caiu no meio recomeça de onde parou na rodada
  seguinte.
- Uma entrada `workflow_dispatch` do tipo string (`so: nip | cnj | explorar`) para
  rodar um coletor isolado. Iterar num coletor sem esperar os 15 minutos dos outros
  muda o ritmo do trabalho.
- O passo de publicar leva `if: always()` e faz `git pull --rebase` antes de tentar
  de novo — a rodada demora, e um commit seu no meio derrubaria o push.

### Cadeia de dados de três níveis

1. Base publicada (`raw.githubusercontent.com/.../dados.json`)
2. `dados.json` ao lado do HTML
3. Base embutida no próprio HTML

**Desenhe primeiro com a embutida, busque a remota em segundo plano e troque quando
chegar.** Nunca bloqueie a primeira pintura numa requisição de rede: um fetch pendurado
vira tela branca. Use `AbortController` com timeout de ~6 s. Se a rede corporativa
barrar o raw, o dashboard simplesmente continua com a base embutida.

Manter a cópia embutida atualizada é obrigação do robô: um passo `--embutir` reescreve
o bloco `let DATA = {...}` do HTML a partir do `dados.json` a cada rodada. Sem isso,
quem abrir o arquivo offline vê número velho sem perceber.

### Um arquivo HTML só

Chart.js e CSS inline, zero dependência externa, zero build. Abre com duplo clique,
sobrevive a anexo de e-mail, funciona sem internet. É o formato que de fato circula
dentro de uma gestora.

### O selo do topo é derivado, nunca fixo

Calcule o texto do cabeçalho a partir das competências realmente gravadas
(`vintage_beneficiarios`, `vintage_cnes`, ...) toda vez que gravar a base. Frase fixa
escrita no build vira mentira assim que um coletor anda e outro não.

## O laço de descoberta

**Nunca escreva um parser a partir da documentação.** Escreva primeiro uma rodada de
descoberta que baixa o arquivo mais recente de cada fonte, detecta codificação e
separador, e grava cabeçalho, três linhas de amostra e contagens num JSON no repo.
Depois escreva o parser sabendo o nome real das colunas.

Quando um coletor errar, **faça-o se explicar** em vez de você chutar:

- Contagem acima do esperado? Despeje quais razões sociais caíram em cada balde. O
  erro aparece de imediato ("ah, estou somando a Mediservice na Bradesco").
- Não sabe qual filtro a fonte original usou? Calcule **todos os candidatos numa
  passada só** e deixe a reconciliação escolher. Foi assim que saiu o recorte do NIP
  e o casamento de unidades do CNES.
- Não sabe o que tem numa coluna? Despeje os 10 valores mais frequentes dela.

Duas rodadas de auto-diagnóstico valem dez de tentativa e erro.

## Anatomia de um coletor

1. **Descobrir a competência** mais recente no diretório da fonte.
2. **Baixar** com tentativas e espera crescente, conferindo integridade (tamanho
   contra `Content-Length`, ZIP que abre e lê o primeiro CSV). Servidor público cai
   no meio do download com frequência, e ZIP truncado só falha na hora de ler.
3. **Parsear casando coluna por regex no cabeçalho**, nunca por posição. Registre o
   que casou. Se renomearem um campo, o script avisa em vez de somar errado.
4. **Agregar** nos recortes que o dashboard usa.
5. **Conferir** contra a base.
6. **Gravar só o que passou** — operadora a operadora, se for o caso.
7. **Gravar o diagnóstico**, sempre.

Use piso absoluto na tolerância: uma série de 10 unidades não se julga em 2%.
`abs(calc - base) <= max(5, base * 0.02)`.

## Emenda de série: nível ou encadeamento

Quando o escopo do grupo econômico da fonte não bate com o do relatório, o **nível**
não é comparável — mas a **variação** é, desde que medida na mesma metodologia nos
dois meses. Então:

- Escopos batem dentro da tolerância → grave o número absoluto da fonte.
- Escopos divergem → baixe também a competência anterior, calcule a variação na sua
  própria metodologia e aplique sobre o último nível da base. É o mesmo procedimento
  que o IBGE usa ao trocar de amostra.
- Não dá para medir nem a variação → a série não avança. De propósito.

Guarde o agregado de cada mês num arquivo pequeno (`agregado_ans.json`) para servir
de referência no mês seguinte — evita rebaixar centenas de MB só para medir variação.

Aplique o fator de calibragem a **todos** os recortes da operadora (vidas, UF,
contratação, faixa etária), não só ao principal: escalar num lugar e não nos outros
produz um dashboard onde a soma por UF não fecha com o total.

**Histórico revisado:** fontes públicas reprocessam o passado. O padrão é **acrescentar
meses novos e não reescrever o que já foi publicado** — trocar número que o usuário já
citou em relatório é pior que conviver com a revisão. Meça o tamanho da revisão e
mostre no diagnóstico.

## Fontes brasileiras de saúde já mapeadas

**ANS — beneficiários (PDA-024).** `dadosabertos.ans.gov.br/FTP/PDA/informacoes_consolidadas_de_beneficiarios-024/AAAAMM/`,
28 ZIPs (27 UFs + XX), ~390 MB. **UTF-8**, separador `;`. A coluna de faixa etária é
`DE_FAIXA_ETARIA_REAJ`, não `DE_FAIXA_ETARIA`. A Unimed Nacional aparece como
"UNIMED CNU - COOPERATIVA CENTRAL". Clinipam e São Lucas são aquisições do GNDI.
O servidor derruba conexão em horário de pico.

**CNES — leitos.** A página de downloads é AngularJS e vem vazia; o endereço real é
`cnes.datasus.gov.br/services/arquivos-download/base-dados/`, que devolve
`[{sequencial, nomeArquivo}]`. O arquivo é
`cnes.datasus.gov.br/EstatisticasServlet?path=BASE_DE_DADOS_CNES_AAAAMM.ZIP` (~730 MB).
O DATASUS responde **503 para cliente que não parece navegador** — mande User-Agent,
Accept-Language e Referer de browser. Leitos em `rlEstabComplementar` (CO_UNIDADE,
CO_LEITO, QT_EXIST, QT_SUS); nomes em `tbEstabelecimento`; o CO_UNIDADE de 13 dígitos
já carrega município (6 primeiros) e UF (2 primeiros).

**CNJ — judicialização.** A API pública do DataJud **está ~18 meses atrasada** e tem
datas corrompidas (anos 2611, 4507, 9010 com milhões de processos) — não serve. Use o
painel `justica-em-numeros.cnj.jus.br/painel-saude/`, que é um Power BI publicado e
aceita consulta direta ao modelo:

- POST em `<cluster>/public/reports/querydata?synchronous=true`, header
  `X-PowerBI-ResourceKey`. O cluster sai do `r=` da URL do embed, decodificado.
- Modelo: `tbl_fato_materias_R` (ano, mes, sigla_grau, materia, Proc_cn, ramo_justica)
  e a medida `medidas_matéria[dd Novos_temas]`. `materia = ' Saúde Suplementar'` —
  **com espaço à esquerda**. `sigla_grau`: G1 = 1ª instância, JE = juizados.
- Duas armadilhas: reaproveitar o `QueryId` traz resposta em cache com outro formato
  e ignorando o filtro (mande um novo a cada chamada); e sem `DataReduction` a
  resposta é cortada em 100 linhas.
- Para capturar a consulta de um painel qualquer: instale um gancho em `fetch` e
  `XMLHttpRequest.send` na página do embed, provoque um redesenho e leia os corpos.

**IBGE — IPCA.** SIDRA tabela 7060, variável 2265 (acumulado 12 meses). API JSON
aberta, aceita CORS: é a única fonte que o próprio dashboard pode buscar do navegador.

**ANS — NIP e IGR.** `demandas_dos_consumidores_nip/pda-013-...-AAAA.csv`, microdados
com uma linha por demanda. O corte assistencial está em `NATUREZA_DA_NIP`;
`CLASSIFICACAO_DA_NIP` é o *status* da demanda ("inativa", "em andamento").
Descoberta importante: **o relatório conta a operadora principal, não o grupo** — case
por razão social exata ("BRADESCO SAÚDE S.A." sim, "Bradesco Saúde - Operadora de
Planos" e Mediservice não). O **IGR não precisa de arquivo próprio**: é demandas do
mês ÷ beneficiários **do mês anterior** × 100.000, e você já tem os dois.

**Ainda sem coletor, schema conhecido:** DIOPS (`demonstracoes_contabeis/AAAA/`,
balancete com DATA, REG_ANS, CD_CONTA_CONTABIL, VL_SALDO_FINAL — destrava DRE,
sinistralidade, provisões e depósitos judiciais); reajustes (PDA-043 contrato a
contrato com PC_PERCENTUAL e QT_BENEF_COMUNICADO, média ponderada dá a série mensal;
PDA-055 traz o agrupamento/SME); SIP mapa assistencial (QT_EVENTOS,
QT_BENEF_FORA_CARENCIA, VL_DESPESA_ASST_LIQ → volume por beneficiário, ticket, custo).
O arquivo de ressarcimento ao SUS **não tem dimensão de operadora** — só o agregado do
mercado; o histórico de cobrança (`hc_ressarcimento_sus`) tem.

**Sem endpoint:** ANAHP e Sindusfarma publicam PDF e planilha. Automatizar por
extração é possível, mas quebra quando mudam o layout — assuma manual e diga isso.

## Erros que já custaram uma rodada cada

- Duas funções `baixar()` com assinaturas diferentes no mesmo arquivo. A segunda
  vence e a primeira quebra na primeira chamada real.
- Contar linha de dados como cabeçalho de ano. Exija sequência monotônica plausível
  antes de aceitar uma linha de inteiros como cabeçalho.
- Chave de mapa por nome quando há homônimos ("São Lucas" em duas cidades). Use índice.
- Assumir latin-1. Detecte lendo os primeiros 256 KB.
- Deixar o coletor seguir com menos arquivos do que devia.
- Rodar `--auto` sem que a flag exista no parser real do CLI.

## Entrega

Publique o HTML pelo próprio robô e mande o link do repositório. Se a rede do usuário
bloqueia tipos de arquivo, subir pelo navegador na interface do GitHub funciona — a
ferramenta de upload aceita caminho do sandbox direto no input de arquivo.

No fim, diga em números o que foi automatizado e o que não foi, com o motivo. Um mapa
honesto do que falta vale mais que uma promessa de cobertura total.
````
