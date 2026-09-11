# Manual de recuperação — Healthcare Database Dashboard

Escrito em setembro de 2026 para responder a uma pergunta específica: *e se eu perder
o acesso a esta conta do Claude?*

---

## 1. Leia isto primeiro

**Perder a conta do Claude não para nada.** O dashboard não é atualizado pelo Claude.
Ele é atualizado por uma rotina que roda no GitHub Actions, dentro do seu repositório,
todo dia às 12h UTC (9h de Brasília), sem ninguém logado em lugar nenhum. O Claude
escreveu o robô; quem executa o robô é o GitHub.

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

## 1.5. Onde abrir o dashboard

O endereço fixo, sempre com a versão do dia:

**https://vitorhugoito17.github.io/hc-dashboard/**

É GitHub Pages servindo o próprio repositório. O robô reescreve o
`Healthcare_Database_Dashboard.html` na rodada diária e o Pages republica em um ou dois
minutos; o link nunca muda. Dá para favoritar, mandar por e-mail e abrir do celular.

A contrapartida, que você aceitou conscientemente: **Pages só funciona em repositório
público** — site privado é recurso de conta Enterprise. Ou seja, qualquer pessoa com o
endereço vê o dashboard e o código. O que está lá dentro é dado público reconciliado
(ANS, CNES, CNJ, IBGE) mais os números da base setorial de origem; não há credencial,
posição de carteira nem nota interna no repositório, e não deve haver. Antes de
acrescentar qualquer bloco novo, pergunte se ele pode ser lido por um estranho.

Três alternativas, se um dia a exposição incomodar:

- **Baixar o HTML e abrir local.** Arquivo único, funciona offline com a base embutida.
- **Voltar o repositório a privado.** O robô continua rodando normalmente; só o Pages
  para de servir, e você volta a abrir o arquivo baixado.
- **Mover para uma organização corporativa** com plano Enterprise, onde o Pages pode ser
  publicado de forma restrita aos membros.

---

## 2. O que é realmente crítico

Só uma coisa: **a conta do GitHub `vitorhugoito17`**. Ela é o ponto único de falha de
verdade. Se você perder essa conta, perde o repositório, o robô e o agendamento.

Vale gastar dez minutos agora com três coisas:

1. **E-mail de recuperação e 2FA** configurados na conta do GitHub, com os códigos de
   backup guardados fora do computador.
2. **Um segundo dono.** Em `Settings → Collaborators` você adiciona um colega com
   acesso de escrita, ou — melhor — transfere o repositório para uma organização da
   sua empresa, onde mais de uma pessoa administra.
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
| `diagnostico_releases.json` | O que a CVM publica das listadas, o que foi aberto e o que a extração aceitou ou recusou. |
| `index.html` | Redirecionamento, para o endereço curto do Pages abrir o dashboard. |
| `.nojekyll` | Diz ao Pages para servir os arquivos como estão, sem processar. |

O dashboard busca a base nesta ordem: base publicada no GitHub → `dados.json` ao lado
do arquivo → cópia embutida no próprio HTML. Ele desenha na hora com a embutida e
troca em segundo plano quando a publicada chega, então nunca fica tela branca — e se a
rede corporativa bloquear o GitHub, ele simplesmente continua com a embutida.

---

## 4. Como operar sem Claude nenhum

**Forçar uma rodada.** No repositório, aba **Actions** → *Atualizar base do dashboard*
→ **Run workflow**. O campo `so` aceita:

- vazio → rodada completa (IPCA, beneficiários da ANS, leitos do CNES, NIP, CNJ);
- `nip` → só reclamações;
- `cnj` → só judicialização;
- `explorar` → só mapeia o schema das fontes que ainda não têm coletor;
- `releases` → só a camada de RI: acha os releases das listadas na CVM, abre os
  PDFs, tenta extrair os números e regrava a aba *Listadas × ANS*.

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

## 4.5. A camada de RI — Listadas × ANS

A aba **Listadas × ANS** compara o número que a companhia aberta publica no release
com a linha correspondente do cadastro da ANS, na mesma competência. Médico contra
médico, odonto contra odonto — nunca consolidado contra recorte, que é o erro fácil
aqui e já custou uma rodada.

**Como o release é achado.** Não por site de RI. Toda companhia aberta protocola na
CVM, e a CVM publica o índice desses protocolos em dados abertos (IPE). O robô lê esse
índice, filtra pelas registrantes que interessam e pega o documento de tipo
`Press-release` mais recente. É um índice só, estável, para todas as empresas —
melhor que raspar cinco sites feitos por fornecedores diferentes.

**Quem é comparada com quem.** A decisão de cobertura está em `LISTADAS`, no
`atualizador.py`:

| Registrante na CVM | Linha da ANS |
|---|---|
| REDE D'OR SÃO LUIZ S.A. | SulAmérica (médico e odonto) |
| BRADSAÚDE S.A. | Bradesco Saúde (médico) · Odontoprev (odonto) |
| PORTO SAÚDE PARTICIPAÇÕES / PORTO SEGURO | Porto Seguro |
| HAPVIDA PARTICIPAÇÕES | Hapvida + GNDI |
| QUALICORP | nenhuma — é corretora, não operadora |

Duas coisas que a descoberta ensinou e que não estavam em lugar nenhum: a **Porto tem
uma registrante própria de saúde**, mas o release de resultado sai pela holding; e a
**Odontoprev virou BRADSAÚDE S.A.**, o veículo em que o Bradesco consolidou saúde.
Quem for mexer nisso confira antes se ainda é assim.

**A referência e o portão.** `REL_REFERENCIA`, no `atualizador.py`, guarda os números
do 2T26 conferidos documento a documento. Eles são o alvo: o extrator automático só
pode gravar um trimestre novo depois de, rodando sobre o 2T26, reproduzir aquela
tabela. Mesma regra do resto da base — o que não se reproduz não entra. Hoje o extrator
acerta sozinho a Porto; Rede D'Or e Hapvida ele ainda recusa, porque reportam em
tabela e não em prosa.

**O que esperar do número.** No 2T26 os sete pares ficaram entre −1,8% e +3,2%. A
dispersão é de perímetro: a companhia consolida o que controla, a ANS conta por
registro de operadora. O sinal útil não é o nível da diferença, é ela mudar de
tamanho de um trimestre para o outro — quando muda, alguma coisa mudou de perímetro
e a série da ANS parou de servir de proxy antecipado.

---

## 5. Como retomar num Claude novo

Antes de tudo, o mal-entendido mais comum: **usar o dashboard não precisa de Claude
nenhum.** Você abre o HTML e ele funciona; o robô atualiza sozinho no GitHub. Claude só
entra quando você quer *mudar* alguma coisa — corrigir um coletor, escrever um novo,
mexer no visual. O passo a passo abaixo é para isso.

### Passo 1 — ligar o navegador à conta nova

O Claude publica no GitHub controlando o seu Chrome. Para isso a **extensão do Claude no
Chrome precisa estar logada na mesma conta** que você está usando na conversa. Se você
trocou de conta, entre de novo na extensão. Sem esse passo o Claude consegue escrever o
código mas não consegue publicar, e você só descobre no fim.

Confira também que você está logado no GitHub como `vitorhugoito17` no mesmo Chrome.

### Passo 2 — colar o prompt de retomada

> Tenho um dashboard setorial de saúde suplementar que se atualiza sozinho por um robô
> em `github.com/vitorhugoito17/hc-dashboard`. Leia o `MANUAL.md` e o `atualizador.py`
> desse repositório antes de mexer em qualquer coisa — dá para baixar os dois com curl
> em `raw.githubusercontent.com`.
>
> A regra da casa: nunca grave um número que você não consiga reproduzir contra um
> período que a base já tem. Se não bater, não grave e me diga por quê. Antes de
> escrever qualquer parser novo, rode uma passada de descoberta que baixe o arquivo
> mais recente da fonte e me mostre cabeçalho e amostra. Quando um coletor errar, faça
> ele se explicar — quais entidades caíram em cada balde, quais recortes candidatos e o
> erro de cada um — em vez de chutar.
>
> Como trabalhamos: seu ambiente alcança o `raw.githubusercontent.com` mas **não**
> alcança ANS, CNES, DATASUS nem IBGE — todo teste contra dado real roda no GitHub
> Actions. Você edita o `atualizador.py` aí, sobe pela página de upload do GitHub
> usando meu Chrome, dispara o workflow em Actions e lê o resultado nos
> `diagnostico_*.json` publicados. Minha rede corporativa bloqueia os servidores de
> dados abertos e bloqueia download de `.py`, então não adianta me mandar script.

### Passo 3 — conferir que ele entendeu antes de confiar

Peça: *"me diga em que competência está cada bloco da base hoje"*. Ele deve responder
lendo o `dados.json` publicado. Se conseguir, o essencial está de pé.

### Passo 4 — o ciclo de trabalho, uma volta completa

1. Claude edita o `atualizador.py` no ambiente dele e testa a lógica offline com dados
   sintéticos (o que der para testar sem rede).
2. Sobe o arquivo em `github.com/vitorhugoito17/hc-dashboard/upload/main`, pelo seu Chrome.
3. Dispara em **Actions → Run workflow**, usando o campo `so` (`nip`, `cnj`, `explorar`)
   para rodar só o coletor em questão — a rodada completa leva ~15 minutos quando há
   competência nova.
4. Lê o resultado em `raw.githubusercontent.com/.../diagnostico_*.json` e itera.

### O que avisar para poupar meia hora

- A página de upload do GitHub às vezes carrega em cache **como se você estivesse
  deslogado** ("Uploads are disabled"). Recarregue; não é falta de permissão.
- Cada rodada de teste custa minutos. Faça o coletor despejar tudo que puder de uma vez
  (candidatos, valores de coluna, quem caiu em cada balde) em vez de uma pergunta por
  rodada.
- Rodada que termina verde sem mudar o `dados.json` normalmente é o portão de
  reconciliação recusando, não erro. Leia o diagnóstico antes de mexer.

### Recuperar a skill

Peça: *"salve como skill o texto que está no anexo do MANUAL.md do repositório"*.

---

## 6. Onde as coisas pararam (setembro de 2026)

**Automatizado e rodando:** IPCA (IBGE/SIDRA), beneficiários por operadora, região, UF,
contratação e faixa etária (ANS PDA-024), leitos por hospital (CNES), judicialização
(painel do CNJ), reclamações NIP e IGR (ANS), e a captura dos releases das listadas
na CVM.

**A lição mais cara desta safra, e ela não é de código.** A ANS **revisa competência
já publicada**. Junho/26 saiu em 05/ago com 53.145.666 — número que na época conferiu
dígito a dígito com o release — e depois foi revisado para 53.080.809. O robô guardava
o agregado do mês anterior e comparava o mês novo contra ele; com isso, a adição
líquida de julho saiu +10,9 mil quando a real era +75,7 mil. **Os dois níveis estavam
certos; o que não existia era a diferença entre eles.** Erro assim é pior que um
buraco na série, porque tem cara de fluxo.

Três defesas ficaram no robô por causa disso, e vale não desmontá-las:

1. A competência de referência é **remedida do arquivo bruto** a cada rodada, em vez
   de reaproveitar o `agregado_ans.json` da rodada anterior.
2. Quando a remedição mostra que a ANS mexeu no mês anterior, ele **recarimba** aquele
   mês e registra a revisão em `pda024.revisoes` — a revisão aparece como revisão, não
   como carteira ganha.
3. O fluxo (adições líquidas) é medido **ANS contra ANS**, os dois meses na mesma
   safra, e não pela diferença entre níveis gravados. Só assim ele não muda quando a
   fonte revisa o passado.

Há ainda `meta.competencias_ans`, a lista das competências que a própria ANS escreveu.
O recarimbo só vale para elas: os meses anteriores vêm da consolidação de origem, com
escopo de grupo próprio, e reescrevê-los seria trocar histórico de uma metodologia por
número de outra.

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
que a base de origem usa é bem mais estreito do que qualquer filtro simples do cadastro. O
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
- Comparar dois meses de **safras diferentes** e chamar a diferença de fluxo. Se a
  fonte revisa o passado, meça o fluxo dentro de uma safra só.
- Normalizar o texto para casar palavra-chave e recortar o trecho **desse mesmo
  texto**: a função que troca pontuação por espaço transforma "2.498" em "2 498" e
  "78,1%" em "78 1". Localizar e ler precisam de textos diferentes, alinhados no
  mesmo índice.
- Comparar o número consolidado da companhia (saúde + odonto) com o recorte médico da
  ANS. Deu 128% de "dispersão" que era só soma. Classifique o segmento antes de
  comparar, e recuse o que vier sem segmento explícito.
- Adivinhar URL de portal de dados abertos. O IPE da CVM não está em
  `.../DADOS/ipe_cia_aberta_AAAA.csv`; os arquivos são `.zip`. Liste o diretório.
- Casar razão social com padrão contendo espaço depois de passar o nome por uma
  normalização que troca espaço por `_`. Hapvida e Qualicorp casaram porque são uma
  palavra só; Bradesco, Porto e Rede D'Or não casaram e o diagnóstico veio vazio.
- Reprocessar um mês do meio da série sem travar o selo do topo: ele andava para trás
  e o cabeçalho passava a anunciar competência velha.

## Entrega

Publique o HTML pelo próprio robô e mande o link do repositório. Se a rede do usuário
bloqueia tipos de arquivo, subir pelo navegador na interface do GitHub funciona — a
ferramenta de upload aceita caminho do sandbox direto no input de arquivo.

No fim, diga em números o que foi automatizado e o que não foi, com o motivo. Um mapa
honesto do que falta vale mais que uma promessa de cobertura total.
````
