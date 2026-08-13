#!/usr/bin/env python3
"""
atualizador.py — atualiza o Healthcare Database Dashboard (arquivo único)
========================================================================

Este arquivo foi entregue como .txt porque a rede da Apex bloqueia download
de .py. Renomeie para "atualizador.py" e deixe na mesma pasta do
Healthcare_Database_Dashboard.html e do dados.json.

USO
    python atualizador.py                 # faz tudo: IPCA + beneficiários ANS
    python atualizador.py --ipca          # só a inflação (rápido, API do IBGE)
    python atualizador.py --conferir      # processa a ANS e mostra, sem gravar
    python atualizador.py --so-baixar     # só baixa os ZIPs da ANS
    python atualizador.py --cnes          # só os leitos hospitalares (CNES)
    python atualizador.py --cnes-descobrir  # refaz o mapa unidade -> código CNES
    python atualizador.py --cnj           # só a judicialização (painel do CNJ)

O QUE ELE FAZ
    1. IPCA/IBGE — API JSON pública, ciclo fechado, segundos.
    2. Beneficiários/ANS (PDA-024) — descobre a competência mais recente,
       baixa os 28 ZIPs (~390 MB), agrega e costura nas séries do dashboard.
       Operadora cujo escopo de grupo bate com o da base entra pelo número
       absoluto da ANS; onde não bate, entra pela variação mês a mês medida
       na mesma metodologia. Onde não dá para medir nem a variação, a série
       não avança — melhor um mês a menos que um degrau falso.
    3. Leitos/CNES — baixa a base mensal do DATASUS e atualiza os leitos por
       hospital. O casamento entre as unidades do dashboard e os códigos CNES
       é feito uma vez, validado contra os leitos que a base já traz, e
       congelado em cnes_map.json.
    4. Judicialização/CNJ — painel de Direito à Saúde, consultado direto no
       modelo do Power BI. Reproduz a série do relatório mês a mês; acrescenta
       os meses novos e não reescreve o histórico.

REQUISITOS
    Python 3.9+ e a biblioteca requests:   python -m pip install requests
"""
from __future__ import annotations




# ======================================================================
# COLETA E PARSE — PDA-024 da ANS
# ======================================================================

"""
py — coleta e parse da base PDA-024 da ANS
=================================================

"Informações Consolidadas de Beneficiários": um diretório por competência
(AAAAMM), 28 ZIPs (um por UF + XX). Cada ZIP traz um CSV com uma linha por
combinação de operadora × município × cobertura × contratação × faixa etária.

É desta base que saem, no dashboard, todas as séries que hoje param em mai/26:
vidas e market share por operadora, região, UF, tipo de contratação, faixa
etária e modalidade da operadora.

O parser não assume nomes de coluna fixos: lê o cabeçalho e casa cada campo
por padrão, imprimindo o que casou. Se a ANS renomear uma coluna, o script
avisa em vez de devolver número errado.
"""
import csv, io, os, re, sys, time, unicodedata, zipfile
from collections import defaultdict

BASE = 'https://dadosabertos.ans.gov.br/FTP/PDA/informacoes_consolidadas_de_beneficiarios-024/'
UFS = ['AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT','PA','PB','PE','PI',
       'PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO','XX']

REGIAO = {
 'Southeast':['SP','RJ','MG','ES'],
 'Northeast':['BA','PE','CE','MA','PB','RN','AL','SE','PI'],
 'South':['PR','RS','SC'],
 'Midwest':['GO','MT','MS','DF'],
 'North':['AM','PA','RO','AC','AP','RR','TO'],
}
UF2REG = {uf:r for r,ufs in REGIAO.items() for uf in ufs}

# ---------------------------------------------------------------- colunas
# (nome no parser, lista de padrões aceitos, obrigatório?)
CAMPOS = [
 ('operadora',  [r'^nm_?razao_?social$', r'^razao_?social$', r'^nm_?operadora$', r'^operadora$'], True),
 ('registro',   [r'^cd_?operadora$', r'^registro_?ans$', r'^cd_?registro'], False),
 ('modalidade', [r'^nm_?modalidade$', r'^modalidade'], False),
 ('uf',         [r'^sg_?uf$', r'^uf$', r'^sg_?uf_?(residencia|benef)'], False),
 ('cobertura',  [r'cobertura', r'^tp_?cobertura'], False),
 ('contratacao',[r'contrata'], False),
 ('faixa',      [r'^de_?faixa_?etaria_?reaj$', r'faixa_?etaria_?reaj', r'^de_?faixa_?etaria$'], False),
 ('qtd',        [r'^qt_?beneficiario_?ativo$', r'^qt_?benef', r'quantidade.*benefici'], True),
]

def _norm(s):
    s = unicodedata.normalize('NFD', str(s)).encode('ascii','ignore').decode()
    return re.sub(r'[^a-z0-9_]', '_', s.strip().lower()).strip('_')

def mapear_colunas(header, verboso=True):
    norm = [_norm(h) for h in header]
    mapa, usados = {}, set()
    for nome, padroes, obrig in CAMPOS:
        achou = None
        for pad in padroes:
            for i, h in enumerate(norm):
                if i in usados: continue
                if re.search(pad, h): achou = i; break
            if achou is not None: break
        if achou is None:
            if obrig:
                raise SystemExit(f'ERRO: não encontrei a coluna obrigatória "{nome}" no cabeçalho.\n'
                                 f'Colunas disponíveis: {header}\n'
                                 f'Ajuste os padrões em CAMPOS (py).')
            if verboso: print(f'      aviso: coluna "{nome}" não encontrada — série correspondente será pulada')
        else:
            mapa[nome] = achou; usados.add(achou)
    if verboso:
        print('      colunas casadas: ' + ', '.join(f'{k}→{header[v]}' for k,v in mapa.items()))
    return mapa

# --------------------------------------------------- agrupamento econômico
# Ordem importa: o primeiro padrão que casar define o grupo.
# Cada linha do CSV cai num "balde" folha (operadora). Os grupos econômicos que o
# dashboard usa são somas de baldes, definidas em COMPOSTOS. Baldes que começam com
# "_" são internos: existem só para alimentar a soma e não vão para o dashboard.
GRUPOS = [
 # GNDI inclui as adquiridas que o BBI consolida no grupo (Clinipam, São Lucas)
 ('GNDI',                        r'notre ?dame|interm[e_]dica|\bgndi\b|clinipam|sao lucas saude'),
 ('Hapvida',                     r'\bhapvida\b'),
 ('Amil Assistência Médica',     r'amil assist'),
 ('_amil_outras',                r'\bamil\b|medial|next saude|esho'),
 ('Bradesco Saúde S.A.',         r'bradesco saude s\.? ?a\b|bradesco sa[au]de s'),
 ('_bradesco_outras',            r'\bbradesco\b|mediservice'),
 ('Sul América Cia Seguro Saúde',r'sul ?america (cia|companhia)'),
 ('_sulamerica_outras',          r'sul ?america'),
 ('Athena Saúde',                r'\bathena\b'),
 ('Porto Seguro',                r'porto ?seguro|portomed'),
 # a Unimed Nacional aparece no cadastro como "UNIMED CNU - COOPERATIVA CENTRAL"
 ('Unimed Nacional',             r'unimed cnu|central nacional unimed|unimed (do brasil|nacional)'),
 ('Unimed Seguros',              r'unimed seguros'),
 ('Unimed BH',                   r'unimed b\.?h\b|unimed belo horizonte'),
 # federação estadual do RJ; nao confundir com as singulares Rio Branco/Rio Verde
 ('Unimed Ferj',                 r'unimed do est.*\brj\b.*federacao|federacao.*coop.*medicas?.*\brj\b'),
 ('Unimed Rio',                  r'unimed[- ]rio\b(?!\s*(branco|verde|preto|claro|grande|negro|doce|pardo))'),
 ('Odontoprev',                  r'odontoprev|bradesco dental'),
 ('Metlife',                     r'metlife'),
 ('Prevent Senior',              r'prevent senior'),
]

COMPOSTOS = {
 'Hapvida + GNDI': ['Hapvida','GNDI'],
 'Amil':           ['Amil Assistência Médica','_amil_outras'],
 'Bradesco Saúde': ['Bradesco Saúde S.A.','_bradesco_outras'],
 'SulAmérica':     ['Sul América Cia Seguro Saúde','_sulamerica_outras'],
 'Unimed Rio/Ferj':['Unimed Rio','Unimed Ferj'],
}

def classificar(nome):
    n = _norm(nome).replace('_',' ')
    for rot, pad in GRUPOS:
        if re.search(pad, n): return rot
    return 'Others'

FAIXAS = ['0 - 18','19 - 23','24 - 28','29 - 33','34 - 38','39 - 43','44 - 48','49 - 53','54 - 58','59+']
def norm_faixa(v):
    n = _norm(v)
    m = re.search(r'(\d+)\D+(\d+)', n)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        for f in FAIXAS:
            mm = re.match(r'^(\d+) - (\d+)$', f)
            if mm and int(mm.group(1)) == a and int(mm.group(2)) == b: return f
        return f'{a} - {b}'
    if re.search(r'59|mais|acima', n): return '59+'
    return None

CONTRAT = [('Corporate', r'coletivo empresarial'), ('Affinity', r'coletivo por adesao|coletivo adesao'),
           ('Individual Plan', r'individual|familiar'), ('Not indentified', r'.')]
def norm_contrat(v):
    n = _norm(v).replace('_',' ')
    for rot, pad in CONTRAT:
        if re.search(pad, n): return rot
    return 'Not indentified'

MODALIDADE = [('Unimeds', r'cooperativa medica'), ('Medical Groups', r'medicina de grupo'),
              ('Health Insurers', r'seguradora'), ('Self-management', r'autogestao'),
              ('Philanthropics', r'filantropia'),
              ('Odonto — grupo', r'odontologia de grupo'), ('Odonto — cooperativa', r'cooperativa odontologica')]
def norm_modalidade(v):
    n = _norm(v).replace('_',' ')
    for rot, pad in MODALIDADE:
        if re.search(pad, n): return rot
    return 'Others'

def eh_odonto(cobertura):
    return bool(re.search(r'odonto', _norm(cobertura)))

# ---------------------------------------------------------------- download
def _zip_integro(caminho):
    """O arquivo baixado é mesmo um ZIP legível e com CSV dentro?

    O servidor da ANS derruba conexão no meio do download com frequência. Um
    ZIP truncado abre e falha só na hora de ler — e aí o agregado sai menor sem
    ninguém perceber. Melhor descobrir na hora de baixar.
    """
    try:
        if os.path.getsize(caminho) < 1024:
            return False
        with zipfile.ZipFile(caminho) as z:
            nomes = [n for n in z.namelist() if n.lower().endswith(('.csv', '.txt'))]
            if not nomes:
                return False
            with z.open(nomes[0]) as fh:      # força a leitura do início do stream
                fh.read(1 << 16)
        return True
    except Exception:
        return False


def baixar_competencia(ym, destino, ufs=None, requests=None, tentativas=4):
    """Baixa os 28 ZIPs de uma competência do PDA-024 para `destino`.

    Cada UF é tentada até `tentativas` vezes, com espera crescente, e o arquivo
    só entra na lista depois de passar no teste de integridade. Devolver menos
    de 28 arquivos é motivo para abortar, não para seguir com menos — quem
    chama confere.
    """
    import requests as rq
    requests = requests or rq
    os.makedirs(destino, exist_ok=True)
    alvos = ufs or UFS
    arquivos, faltando = [], []
    sessao = requests.Session()
    sessao.headers.update({'User-Agent': 'Healthcare-Dashboard-Updater/2.0',
                           'Accept-Encoding': 'identity', 'Connection': 'close'})
    for uf in alvos:
        nome = f'pda-024-icb-{uf}-{ym[:4]}_{ym[4:]}.zip'
        caminho = os.path.join(destino, nome)
        if os.path.exists(caminho) and _zip_integro(caminho):
            arquivos.append(caminho); continue
        url = BASE + f'{ym}/' + nome
        print(f'      {uf} …', end='', flush=True)
        ok = False
        for k in range(tentativas):
            try:
                with sessao.get(url, stream=True, timeout=(30, 180)) as r:
                    if r.status_code == 404:
                        print(' (não existe)'); ok = True; break
                    r.raise_for_status()
                    esperado = int(r.headers.get('Content-Length') or 0)
                    with open(caminho, 'wb') as f:
                        for c in r.iter_content(1 << 20):
                            f.write(c)
                obtido = os.path.getsize(caminho)
                if esperado and obtido != esperado:
                    raise IOError(f'truncado: {obtido} de {esperado} bytes')
                if not _zip_integro(caminho):
                    raise IOError('ZIP ilegível')
                print(f' {obtido/1e6:.0f} MB' + (f' (tentativa {k+1})' if k else ''))
                arquivos.append(caminho); ok = True; break
            except Exception as e:
                try: os.remove(caminho)
                except OSError: pass
                if k == tentativas - 1:
                    print(f' ERRO após {tentativas} tentativas: {e}')
                else:
                    espera = 5 * (k + 1)
                    print(f' [{type(e).__name__}, nova tentativa em {espera}s]', end='', flush=True)
                    time.sleep(espera)
        if not ok:
            faltando.append(uf)
    if faltando:
        print(f'\n   NÃO baixaram: {", ".join(faltando)}')
    return arquivos

# ------------------------------------------------------------------ parse
def _encoding(caminho):
    """Detecta a codificação pelo primeiro trecho: UTF-8 quando decodifica limpo."""
    with zipfile.ZipFile(caminho) as z:
        for nome in z.namelist():
            if not nome.lower().endswith(('.csv','.txt')): continue
            with z.open(nome) as fh:
                amostra = fh.read(1 << 18)
            try:
                amostra.decode('utf-8'); return 'utf-8'
            except UnicodeDecodeError:
                return 'latin-1'
    return 'utf-8'


def _linhas(caminho, enc=None):
    enc = enc or _encoding(caminho)
    with zipfile.ZipFile(caminho) as z:
        for nome in z.namelist():
            if not nome.lower().endswith(('.csv','.txt')): continue
            with z.open(nome) as fh:
                raw = io.TextIOWrapper(fh, encoding=enc, errors='replace', newline='')
                yield from csv.reader(raw, delimiter=';')

def agregar(arquivos, verboso=True):
    """Percorre os ZIPs somando os beneficiários nos recortes do dashboard."""
    ag = {
      'vidas':        defaultdict(float),                      # grupo -> vidas (médico)
      'vidas_odonto': defaultdict(float),
      'uf':           defaultdict(float),                      # uf -> total
      'uf_op':        defaultdict(lambda: defaultdict(float)), # uf -> grupo -> vidas
      'modalidade':   defaultdict(float),
      'contratacao':  defaultdict(lambda: defaultdict(float)), # grupo -> tipo -> vidas
      'faixa':        defaultdict(lambda: defaultdict(float)), # grupo -> faixa -> vidas
      'nao_classificadas': defaultdict(float),                 # razão social -> vidas (balde Others)
      'total': 0.0, 'total_odonto': 0.0,
    }
    mapa = None
    for k, caminho in enumerate(arquivos, 1):
        if verboso: print(f'   [{k}/{len(arquivos)}] {os.path.basename(caminho)}', flush=True)
        primeiro = True
        for linha in _linhas(caminho):
            if primeiro:
                primeiro = False
                if mapa is None: mapa = mapear_colunas(linha, verboso)
                continue
            if not linha: continue
            try:
                qt = linha[mapa['qtd']].strip().replace('.','').replace(',','.')
                qt = float(qt) if qt else 0.0
            except (IndexError, ValueError):
                continue
            if qt == 0: continue
            try: nome = linha[mapa['operadora']]
            except IndexError: continue
            grupo = classificar(nome)
            if grupo == 'Others':
                # guarda quem caiu no balde residual, para conseguir auditar depois
                # quais operadoras grandes ainda não têm grupo econômico mapeado
                ag['nao_classificadas'][nome.strip()[:70]] += qt
            cob = linha[mapa['cobertura']] if 'cobertura' in mapa and mapa['cobertura'] < len(linha) else ''
            odonto = eh_odonto(cob)
            if odonto:
                ag['vidas_odonto'][grupo] += qt; ag['total_odonto'] += qt
                continue
            ag['vidas'][grupo] += qt; ag['total'] += qt
            if 'uf' in mapa and mapa['uf'] < len(linha):
                uf = linha[mapa['uf']].strip().upper()
                ag['uf'][uf] += qt; ag['uf_op'][uf][grupo] += qt
            if 'modalidade' in mapa and mapa['modalidade'] < len(linha):
                ag['modalidade'][norm_modalidade(linha[mapa['modalidade']])] += qt
            if 'contratacao' in mapa and mapa['contratacao'] < len(linha):
                ag['contratacao'][grupo][norm_contrat(linha[mapa['contratacao']])] += qt
            if 'faixa' in mapa and mapa['faixa'] < len(linha):
                f = norm_faixa(linha[mapa['faixa']])
                if f: ag['faixa'][grupo][f] += qt
    # grupos econômicos = soma dos baldes-filha
    for comp, partes in COMPOSTOS.items():
        ag['vidas'][comp] = sum(ag['vidas'].get(x,0) for x in partes)
        ag['vidas_odonto'][comp] = sum(ag['vidas_odonto'].get(x,0) for x in partes)
        for uf in ag['uf_op']:
            ag['uf_op'][uf][comp] = sum(ag['uf_op'][uf].get(x,0) for x in partes)
        tipos = set().union(*([set(ag['contratacao'].get(x,{})) for x in partes] or [set()]))
        for tipo in tipos:
            ag['contratacao'][comp][tipo] = sum(ag['contratacao'].get(x,{}).get(tipo,0) for x in partes)
        faixas = set().union(*([set(ag['faixa'].get(x,{})) for x in partes] or [set()]))
        for fx in faixas:
            ag['faixa'][comp][fx] = sum(ag['faixa'].get(x,{}).get(fx,0) for x in partes)
    ag['vidas']['Market'] = ag['total']
    ag['vidas_odonto']['Market'] = ag['total_odonto']
    # remove os baldes internos, que já foram somados nos grupos
    for chave in ('vidas','vidas_odonto','modalidade'):
        for k in [x for x in ag[chave] if x.startswith('_')]: del ag[chave][k]
    for uf in ag['uf_op']:
        for k in [x for x in ag['uf_op'][uf] if x.startswith('_')]: del ag['uf_op'][uf][k]
    for chave in ('contratacao','faixa'):
        for k in [x for x in ag[chave] if x.startswith('_')]: del ag[chave][k]
    return ag


def conferir_cobertura(arquivos, ufs=None):
    """Aborta se faltar UF. Um agregado parcial não é 'quase certo': é errado.

    Sem as 28 UFs o total do mercado sai menor, o encadeamento mede variação
    contra um universo diferente e todo o resto herda o erro em silêncio.
    """
    esperadas = set(ufs or UFS)
    obtidas = set()
    for c in arquivos:
        m = re.search(r'pda-024-icb-([A-Z]{2})-', os.path.basename(c))
        if m: obtidas.add(m.group(1))
    faltam = sorted(esperadas - obtidas)
    if faltam:
        raise SystemExit(
            f'Faltaram {len(faltam)} de {len(esperadas)} UFs: {", ".join(faltam)}.\n'
            'Nada foi gravado. O servidor da ANS costuma derrubar conexão em horário '
            'de pico; rodar de novo mais tarde normalmente resolve, e os ZIPs que já '
            'vieram ficam em cache.')
    print(f'   {len(obtidas)} de {len(esperadas)} UFs — cobertura completa')


def conferir(ag):
    """Imprime o resultado do agrupamento para validação antes de gravar."""
    print('\n   Conferência do agrupamento (vidas em mil, planos médicos)')
    print('   ' + '-'*56)
    for g, v in sorted(ag['vidas'].items(), key=lambda x:-x[1]):
        pct = v/ag['total']*100 if ag['total'] else 0
        print(f'   {g:32} {v/1000:12,.1f} {pct:6.2f}%')
    print(f'   {"TOTAL":32} {ag["total"]/1000:12,.1f}')
    print(f'   {"odontológico":32} {ag["total_odonto"]/1000:12,.1f}')
    if ag['modalidade']:
        print('\n   Por modalidade da operadora')
        for m, v in sorted(ag['modalidade'].items(), key=lambda x:-x[1]):
            print(f'   {m:32} {v/1000:12,.1f}')


# ======================================================================
# COSTURA NAS SÉRIES DO DASHBOARD
# ======================================================================

"""
merge_pda.py — costura o agregado do PDA-024 nas séries do dashboard.

Recebe o dicionário produzido por agregar() e acrescenta a competência
às séries de beneficiários, recalculando market share e adições líquidas a
partir do estoque. Não sobrescreve competências já existentes.
"""

MESES = ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez']
def rotulo(ym: str) -> str:
    return f'{MESES[int(ym[4:6])-1]}/{ym[2:4]}'

UF_NOME = {
 'AC':'Acre','AL':'Alagoas','AM':'Amazonas','AP':'Amapá','BA':'Bahia','CE':'Ceará',
 'DF':'Distrito Federal','ES':'Espírito Santo','GO':'Goiás','MA':'Maranhão','MG':'Minas Gerais',
 'MS':'Mato Grosso do Sul','MT':'Mato Grosso','PA':'Pará','PB':'Paraíba','PE':'Pernambuco',
 'PI':'Piauí','PR':'Paraná','RJ':'Rio de Janeiro','RN':'Rio Grande do Norte','RO':'Rondônia',
 'RR':'Roraima','RS':'Rio Grande do Sul','SC':'Santa Catarina','SE':'Sergipe','SP':'São Paulo',
 'TO':'Tocantins','XX':'n.a.',
}
REGIAO_UF = {
 'Southeast':['SP','RJ','MG','ES'],
 'Northeast':['BA','PE','CE','MA','PB','RN','AL','SE','PI'],
 'South':['PR','RS','SC'],
 'Midwest':['GO','MT','MS','DF'],
 'North':['AM','PA','RO','AC','AP','RR','TO'],
}
# rótulos que a aba de contratação usa, diferentes dos rótulos gerais
SEG_ALIAS = {'GNDI':'NDI', 'Unimed Nacional':'Unimed CNU'}
# rótulos usados nas visões de região/UF -> balde do parser
GEO_ALIAS = {
 'GNDI':'GNDI', 'Hapvida':'Hapvida', 'Amil':'Amil', 'Bradesco Saúde':'Bradesco Saúde',
 'Bradesco Saúde (ex-ASO)':'Bradesco Saúde S.A.', 'SulAmérica':'SulAmérica',
 'SulAmérica (ex-ASO)':'Sul América Cia Seguro Saúde', 'Athena Saúde':'Athena Saúde',
 'Unimed BH':'Unimed BH', 'Unimed CNU':'Unimed Nacional', 'Unimed Seguros':'Unimed Seguros',
 'Others':'Others',
}


def _slot(periods, periodo):
    """Índice do período, criando-o no fim se ainda não existir. (idx, criou)"""
    if periodo in periods:
        return periods.index(periodo), False
    periods.append(periodo)
    return len(periods)-1, True


def _upsert(bloco, periodo, valores):
    """Grava a competência. Se ela já existir (ex.: preenchida parcialmente pela
    camada de divulgação da ANS), sobrescreve — o PDA-024 é a fonte autoritativa."""
    i, criou = _slot(bloco['periods'], periodo)
    n = len(bloco['periods'])
    for nome, vals in bloco['series'].items():
        while len(vals) < n: vals.append(None)
        if nome in valores and valores[nome] is not None:
            vals[i] = valores[nome]
    for nome, v in valores.items():
        if nome not in bloco['series']:
            bloco['series'][nome] = [None]*n
            bloco['series'][nome][i] = v
    return True


def _mil(v):
    return round(v/1000.0, 3) if v else (0.0 if v == 0 else None)


# GRUPOS que particionam o mercado: o "Others" da base é exatamente
# Market menos a soma destes dez. A identidade é conferida em conferir_identidade().
GRUPOS_MERCADO = ['Hapvida + GNDI','Amil','Bradesco Saúde','SulAmérica','Athena Saúde',
                  'Porto Seguro','Unimed Nacional','Unimed Seguros','Unimed BH','Unimed Rio/Ferj']


def _copia_ag(ag):
    """Cópia profunda o suficiente para escalar sem mexer no agregado original."""
    novo = {}
    for k, v in ag.items():
        if isinstance(v, dict):
            if v and isinstance(next(iter(v.values())), dict):
                novo[k] = {a: dict(b) for a, b in v.items()}
            else:
                novo[k] = dict(v)
        else:
            novo[k] = v
    return novo


def escalar(ag, fatores):
    """Aplica o fator de calibragem de cada operadora a TODOS os recortes.

    Escalar num lugar só (vidas) e não nos outros produziria um dashboard
    incoerente: a soma por UF não fecharia com o total da operadora. Como o
    fator corrige diferença de escopo de grupo — quais CNPJs entram no grupo —
    ele vale igual para vidas, odonto, UF, contratação e faixa etária.
    """
    ag = _copia_ag(ag)
    for op, f in fatores.items():
        if not f or abs(f - 1.0) < 1e-12:
            continue
        for chave in ('vidas', 'vidas_odonto'):
            if op in ag[chave]:
                ag[chave][op] = ag[chave][op] * f
        for uf in ag['uf_op']:
            if op in ag['uf_op'][uf]:
                ag['uf_op'][uf][op] = ag['uf_op'][uf][op] * f
        for chave in ('contratacao', 'faixa'):
            if op in ag[chave]:
                ag[chave][op] = {k: v * f for k, v in ag[chave][op].items()}
    return ag


def _ultimos(D):
    """Último valor não-nulo de cada operadora na série de vidas da base."""
    ult = {}
    for op, vals in D['ben']['lives_m']['series'].items():
        for v in reversed(vals):
            if v is not None:
                ult[op] = v; break
    return ult


def calibrar(D, ag, ag_ant=None, tol=0.015, limite_var=0.06):
    """Decide, operadora a operadora, COMO o ponto novo entra na série.

    A base histórica vem da consolidação do BBI, que agrupa CNPJs por critério
    societário próprio. O cálculo novo vem do PDA-024 agrupado por razão social.
    Onde os dois escopos coincidem, o número absoluto da ANS entra direto. Onde
    divergem, o nível não é comparável — mas a VARIAÇÃO é, desde que medida na
    mesma metodologia nos dois meses. Daí a competência anterior.

      nível     — escopos batem dentro da tolerância; grava o número da ANS.
      encadeado — escopos divergem em nível; aplica a variação mês a mês medida
                  no PDA-024 sobre o último nível da base. É o mesmo
                  procedimento de emenda que o IBGE usa ao trocar de amostra.
      retido    — não dá para medir a variação (operadora ausente do cadastro
                  ou variação implausível); a série não avança, de propósito.

    Devolve (fatores, diagnostico).
    """
    ult = _ultimos(D)
    ant_ans = (ag_ant or {}).get('vidas') or {}
    fatores, diag = {}, []
    for op, v in ag['vidas'].items():
        if op.startswith('_'):
            continue
        novo = v / 1000.0
        if op == 'Market':
            fatores[op] = 1.0
            diag.append((op, 'nível', novo, novo, 0.0, 1.0)); continue
        base = ult.get(op)
        if base is None:
            diag.append((op, 'fora da base', None, novo, None, None)); continue
        if base == 0:
            # ex.: Unimed Rio, que o BBI consolida dentro da Ferj
            if novo <= 1.0:
                fatores[op] = 1.0
                diag.append((op, 'nível', base, novo, 0.0, 1.0))
            else:
                diag.append((op, 'retido', base, novo, None, None))
            continue
        d = novo / base - 1
        if abs(d) <= tol:
            fatores[op] = 1.0
            diag.append((op, 'nível', base, novo, d, 1.0)); continue
        anterior = ant_ans.get(op)
        if not anterior:
            diag.append((op, 'retido', base, novo, None, None)); continue
        var = v / anterior - 1
        if abs(var) > limite_var:
            # carteira real não anda 6% num mês: isso é erro de mapeamento
            diag.append((op, 'retido', base, novo, var, None)); continue
        fatores[op] = (base * 1000.0) / anterior
        diag.append((op, 'encadeado', base, base * (1 + var), var, fatores[op]))
    # Séries da base que o agrupamento não encontrou em NENHUMA linha do cadastro
    # — a Athena, por exemplo, é um roll-up cujas operadoras têm razão social
    # própria. Sem isso elas sumiriam do relatório em vez de aparecer como
    # retidas, que é o que de fato são.
    vistos = {op for op, *_ in diag}
    for op, base in ult.items():
        if op in vistos or op == 'Market':
            continue
        diag.append((op, 'retido', base, None, None, None))
    return fatores, diag


def _imprime_calibragem(diag, ym, ym_ant):
    print(f'\n   Calibragem contra a base (nível quando os escopos batem,')
    print(f'   variação {rotulo(ym_ant)}→{rotulo(ym)} quando não batem)')
    print('   ' + '-' * 78)
    print(f'   {"":10} {"operadora":30} {"base":>11} {"grava":>11} {"var":>8}')
    ordem = {'nível': 0, 'encadeado': 1, 'retido': 2, 'fora da base': 3}
    for op, st, base, grava, var, f in sorted(diag, key=lambda x: (ordem.get(x[1], 9), x[0])):
        b = f'{base:11,.1f}' if base is not None else f'{"—":>11}'
        g = f'{grava:11,.1f}' if grava is not None else f'{"—":>11}'
        vv = f'{var*100:7.2f}%' if var is not None else f'{"—":>8}'
        print(f'   {st:10} {op:30} {b} {g} {vv}')
    n = {k: sum(1 for d in diag if d[1] == k) for k in ordem}
    print(f'\n   {n["nível"]} por nível · {n["encadeado"]} encadeadas · '
          f'{n["retido"]} retidas · {n["fora da base"]} fora da base')


def conferir_identidade(D, p, limite=0.005):
    """Others é, na base, exatamente Market menos a soma dos dez grupos.

    Depois do encadeamento a identidade deixa de fechar na unha; o quanto ela
    não fecha é a melhor medida de erro acumulado que temos. Acima de 0,5% do
    mercado o encadeamento não é confiável e é melhor saber.
    """
    lv = D['ben']['lives_m']
    if p not in lv['periods']: return None
    i = lv['periods'].index(p)
    def val(op):
        s = lv['series'].get(op)
        return s[i] if s and i < len(s) else None
    mkt = val('Market'); outros = val('Others')
    partes = [val(g) for g in GRUPOS_MERCADO]
    if mkt is None or outros is None or any(x is None for x in partes):
        faltando = [g for g in GRUPOS_MERCADO if val(g) is None]
        print(f'\n   Identidade Others = Market − 10 grupos: não verificável em {p}'
              + (f' (sem {", ".join(faltando)})' if faltando else ''))
        return None
    gap = mkt - outros - sum(partes)
    rel = gap / mkt if mkt else 0
    sinal = 'ok' if abs(rel) <= limite else 'ATENÇÃO'
    print(f'\n   Identidade Others = Market − 10 grupos em {p}: '
          f'diferença de {gap:,.1f} mil vidas ({rel:+.2%}) — {sinal}')
    return rel


def merge(D, ag, ym, ag_ant=None, ym_ant=None, verboso=True, tolerancia=0.015):
    """Acrescenta a competência ym às séries de beneficiários. Devolve o log.

    Com `ag_ant` (o agregado da competência anterior, calculado com ESTA mesma
    metodologia), as operadoras cujo escopo de grupo não bate com o do BBI
    entram por variação mês a mês em vez de ficarem paradas. Sem ele, o
    comportamento é o antigo: só entra quem reconcilia por nível.
    """
    p = rotulo(ym)
    log = []
    fatores, diag = calibrar(D, ag, ag_ant, tolerancia)
    if verboso:
        _imprime_calibragem(diag, ym, ym_ant or ym)
    aceitos = set(fatores)
    ag = escalar(ag, fatores)
    # filtra o agregado para o que foi calibrado
    ag['vidas'] = {k: v for k, v in ag['vidas'].items() if k in aceitos}
    ag['vidas_odonto'] = {k: v for k, v in ag['vidas_odonto'].items() if k in aceitos or k == 'Market'}
    ag['uf_op'] = {uf: {k: v for k, v in d.items() if k in aceitos} for uf, d in ag['uf_op'].items()}
    ag['contratacao'] = {k: v for k, v in ag['contratacao'].items() if k in aceitos}
    ag['faixa'] = {k: v for k, v in ag['faixa'].items() if k in aceitos}
    metodo = {op: st for op, st, *_ in diag if st in ('nível', 'encadeado')}
    incorporadas = sorted(x for x in aceitos if x != 'Market')
    retidas = sorted(op for op, st, *_ in diag if st == 'retido')
    D.setdefault('meta', {})['reconciliacao'] = {
        'competencia': p, 'tolerancia': tolerancia,
        'competencia_anterior': rotulo(ym_ant) if ym_ant else None,
        'metodo': metodo, 'incorporadas': incorporadas, 'retidas': retidas,
    }
    # o dashboard lê a tabela de reconciliação daqui, não do meta
    pda = D.setdefault('ans', {}).setdefault('pda024', {})
    pda.update({'competencia': p, 'competencia_anterior': rotulo(ym_ant) if ym_ant else None,
                'processado_em': datetime.date.today().strftime('%d/%m/%Y'),
                'tolerancia': tolerancia, 'metodo': metodo,
                'incorporadas': incorporadas, 'retidas': retidas,
                'total_medico': round(ag['total'] / 1000, 3),
                'total_odonto': round(ag['total_odonto'] / 1000, 3)})
    D['ans']['periodo'] = p
    D['ans']['medico'] = round(ag['total'] / 1000, 3)
    D['ans']['odonto'] = round(ag['total_odonto'] / 1000, 3)

    # ---------- vidas e market share por operadora (médico) ----------
    vidas = {g: _mil(v) for g, v in ag['vidas'].items()}
    total = ag['total']   # soma de todas as linhas, independe do agrupamento
    if _upsert(D['ben']['lives_m'], p, vidas):
        log.append(f'ben.lives_m ({len(vidas)} operadoras)')
    share = {g: round(v/total, 6) for g, v in ag['vidas'].items()} if total else {}
    if _upsert(D['ben']['share_m'], p, share):
        log.append('ben.share_m')

    # adições líquidas = variação do estoque
    lv = D['ben']['lives_m']
    i_now = lv['periods'].index(p)
    net = {}
    for g, vals in lv['series'].items():
        if i_now >= 1 and vals[i_now] is not None and vals[i_now-1] is not None:
            net[g] = round(vals[i_now] - vals[i_now-1], 3)
    if _upsert(D['ben']['netadds_m'], p, net):
        log.append('ben.netadds_m')

    # ---------- odontológico ----------
    vid_o = {g: _mil(v) for g, v in ag['vidas_odonto'].items()}
    tot_o = ag['total_odonto']
    if _upsert(D['ben']['dental_lives_m'], p, vid_o):
        log.append('ben.dental_lives_m')
    if tot_o and _upsert(D['ben']['dental_share_m'], p,
                         {g: round(v/tot_o, 6) for g, v in ag['vidas_odonto'].items()}):
        log.append('ben.dental_share_m')
    dl = D['ben']['dental_lives_m']; j = dl['periods'].index(p)
    net_o = {g: round(v[j]-v[j-1], 3) for g, v in dl['series'].items()
             if j >= 1 and v[j] is not None and v[j-1] is not None}
    if _upsert(D['ben']['dental_netadds_m'], p, net_o):
        log.append('ben.dental_netadds_m')

    # ---------- totais consolidados ----------
    if _upsert(D['ben']['medical_total_q'], p, {'Beneficiários': _mil(total)}):
        log.append('ben.medical_total_q')
    if _upsert(D['ben']['dental_total_q'], p, {'Beneficiários': _mil(tot_o)}):
        log.append('ben.dental_total_q')

    # market share trimestral usa rótulos curtos
    msq = {'Hapvida': share.get('Hapvida + GNDI'), 'SulAmérica': share.get('Sul América Cia Seguro Saúde'),
           'Bradesco': share.get('Bradesco Saúde S.A.'), 'Amil': share.get('Amil Assistência Médica'),
           'Unimed CNU': share.get('Unimed Nacional')}
    if any(v is not None for v in msq.values()) and _upsert(D['ben']['medical_share_q'], p, msq):
        log.append('ben.medical_share_q')

    # ---------- modalidade ----------
    if ag['modalidade']:
        mod = {m: _mil(v) for m, v in ag['modalidade'].items()}
        mod['Market'] = _mil(total)
        if _upsert(D['ben']['by_modality'], p, mod):
            log.append('ben.by_modality')

    # ---------- UF e região ----------
    if ag['uf']:
        st = D['ben']['state']
        if True:
            i_st, _ = _slot(st['periods'], p); n_st = len(st['periods'])
            def _set(lst, val):
                while len(lst) < n_st: lst.append(None)
                if val is not None: lst[i_st] = val
            for uf, nome in UF_NOME.items():
                g = st['groups'].get(nome)
                if not g: continue
                _set(g['total'], _mil(ag['uf'].get(uf, 0)))
                for op, vals in g['series'].items():
                    _set(vals, _mil(ag['uf_op'].get(uf, {}).get(GEO_ALIAS.get(op, op))))
            if 'Total' in st['groups']:
                _set(st['groups']['Total']['total'], _mil(total))
                for op, vals in st['groups']['Total']['series'].items():
                    _set(vals, vidas.get(op))
            for g in st['groups'].values():
                _set(g['total'], None)
                for vals in g['series'].values(): _set(vals, None)
            log.append('ben.state')

        rg = D['ben']['region']
        if True:
            i_rg, _ = _slot(rg['periods'], p); n_rg = len(rg['periods'])
            def _setr(lst, val):
                while len(lst) < n_rg: lst.append(None)
                if val is not None: lst[i_rg] = val
            for reg, ufs in REGIAO_UF.items():
                g = rg['groups'].get(reg)
                if not g: continue
                _setr(g['total'], _mil(sum(ag['uf'].get(u, 0) for u in ufs)))
                for op, vals in g['series'].items():
                    b = GEO_ALIAS.get(op, op)
                    _setr(vals, _mil(sum(ag['uf_op'].get(u, {}).get(b, 0) for u in ufs)))
            if 'Total' in rg['groups']:
                _setr(rg['groups']['Total']['total'], _mil(total))
            for g in rg['groups'].values():
                _setr(g['total'], None)
                for vals in g['series'].values(): _setr(vals, None)
            log.append('ben.region')

    # ---------- tipo de contratação ----------
    if ag['contratacao']:
        sg = D['ben']['segment']
        if True:
            i_sg, _ = _slot(sg['periods'], p); n_sg = len(sg['periods'])
            def _sets(lst, val):
                while len(lst) < n_sg: lst.append(None)
                if val is not None: lst[i_sg] = val
            for op, tipos in sg['operators'].items():
                fonte = None
                for g, d in ag['contratacao'].items():
                    if SEG_ALIAS.get(g, g) == op: fonte = d; break
                for tipo, vals in tipos.items():
                    _sets(vals, _mil(fonte.get(tipo)) if fonte else None)
            for tipos in sg['operators'].values():
                for vals in tipos.values(): _sets(vals, None)
            log.append('ben.segment')

    # ---------- faixa etária ----------
    if ag['faixa']:
        ad = D['ben']['age_detail']
        if True:
            i_ad, _ = _slot(ad['periods'], p); n_ad = len(ad['periods'])
            def _seta(lst, val):
                while len(lst) < n_ad: lst.append(None)
                if val is not None: lst[i_ad] = val
            for grupo, faixas in ad['groups'].items():
                fonte = ag['faixa'].get(grupo)
                if grupo == 'Market':
                    fonte = {}
                    for d in ag['faixa'].values():
                        for f, v in d.items(): fonte[f] = fonte.get(f, 0) + v
                for f, vals in faixas.items():
                    _seta(vals, _mil(fonte.get(f)) if fonte else None)
            for faixas in ad['groups'].values():
                for vals in faixas.values(): _seta(vals, None)
            log.append('ben.age_detail')

    conferir_emenda(D, p)
    rel = conferir_identidade(D, p)
    D.setdefault('meta', {})['vintage_beneficiarios'] = p
    D['meta']['reconciliacao']['gap_identidade'] = rel
    n_niv = sum(1 for _, st, *_ in diag if st == 'nível') - 1
    n_enc = sum(1 for _, st, *_ in diag if st == 'encadeado')
    n_ret = sum(1 for _, st, *_ in diag if st == 'retido')
    D['meta']['base'] = (f'beneficiários: {p} ({n_niv} operadoras por nível, '
                         f'{n_enc} encadeadas' + (f', {n_ret} retidas' if n_ret else '') + ')')
    if verboso:
        print(f'\n   Competência {p} incorporada em {len(log)} blocos:')
        for l in log: print(f'      · {l}')
    return log


def conferir_emenda(D, p, limite=0.03):
    """Compara o ponto novo com o anterior de cada operadora.

    As séries da base vêm da consolidação do BBI; o ponto novo vem do PDA-024
    cru. Se as duas metodologias divergirem, aparece como um degrau — e é melhor
    ver isso explicitamente do que descobrir num gráfico depois.
    """
    lv = D['ben']['lives_m']
    if p not in lv['periods']: return
    i = lv['periods'].index(p)
    if i == 0: return
    alertas = []
    for op, vals in lv['series'].items():
        atual, ant = vals[i], vals[i-1]
        if atual is None or ant in (None, 0): continue
        d = (atual-ant)/ant
        if abs(d) > limite:
            alertas.append((op, ant, atual, d))
    if not alertas:
        print(f'\n   Emenda com a base anterior: sem degraus acima de {limite:.0%}.')
        return
    print(f'\n   ATENÇÃO — degraus acima de {limite:.0%} entre {lv["periods"][i-1]} e {p}:')
    print(f'   {"operadora":32} {"anterior":>12} {"novo":>12} {"variação":>10}')
    for op, ant, atual, d in sorted(alertas, key=lambda x:-abs(x[3])):
        print(f'   {op:32} {ant:12,.1f} {atual:12,.1f} {d:9.1%}')
    print('   Variação mensal real de carteira raramente passa de 1-2%. Degrau grande aqui')
    print('   normalmente significa diferença de metodologia entre a consolidação do BBI e')
    print('   o agrupamento deste script — revise GRUPOS em py antes de usar a série.')


# ======================================================================
# CAMADA DE INFLAÇÃO — IPCA/IBGE
# ======================================================================

"""
ipca_update.py — atualiza a camada de inflação direto da API do IBGE.

Diferente das bases da ANS, o IPCA vem de API JSON pública, sem download de
arquivo e sem autenticação. É a única fonte do dashboard que atualiza de ponta
a ponta sem nenhum passo manual: rodou, está atualizado.

    python3 ipca_update.py            # atualiza dados.json
    python3 ipca_update.py --mostrar  # só imprime a última leitura
"""
import argparse, json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DADOS = os.path.join(HERE, 'dados.json')
SIDRA = 'https://apisidra.ibge.gov.br/values/t/7060'

# código do subitem no SIDRA -> rótulo usado no dashboard
CODIGOS = {
 7169:'Índice geral (IPCA)', 7660:'Saúde e cuidados pessoais', 7696:'Plano de saúde',
 7684:'Serviços médicos e dentários', 7685:'Médico', 7686:'Dentista',
 7690:'Serviços laboratoriais e hospitalares', 7692:'Hospitalização e cirurgia',
 12416:'Exame de imagem', 7691:'Exame de laboratório', 7662:'Produtos farmacêuticos',
 7766:'Educação',
}
METRO_ITENS = [7692, 12416, 7691, 7696, 7662]
MESES = ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez']

def _get(url):
    req = urllib.request.Request(url, headers={'User-Agent':'Healthcare-Dashboard/2.0'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode('utf-8'))

def _num(v):
    if v in ('...', '-', '..', None, ''): return None
    try: return float(v)
    except ValueError: return None

def _rotulo(p):  # '202606' -> 'jun/26'
    return f'{MESES[int(p[4:6])-1]}/{p[2:4]}'

def puxar():
    cods = ','.join(str(c) for c in CODIGOS)
    linhas = _get(f'{SIDRA}/n1/all/v/2265/p/all/c315/{cods}')[1:]
    bruto, periodos = {}, set()
    for r in linhas:
        c = r.get('D4C');  p = r.get('D3C')
        if not c or not p: continue
        periodos.add(p)
        bruto.setdefault(int(c), {})[p] = _num(r.get('V'))
    per = sorted(periodos)
    # corta o trecho inicial em que o índice geral ainda não tem 12 meses
    ini = next((i for i,p in enumerate(per) if bruto[7169].get(p) is not None), 0)
    per = per[ini:]
    series = {}
    for cod, rot in CODIGOS.items():
        if cod in bruto: series[rot] = [bruto[cod].get(p) for p in per]

    metro = {}
    for niv in ('n7', 'n6'):
        for r in _get(f'{SIDRA}/{niv}/all/v/2265/p/last%201/c315/{",".join(str(c) for c in METRO_ITENS)}')[1:]:
            c = r.get('D4C')
            if not c: continue
            cidade = (r.get('D1N') or '').replace('Região Metropolitana de ', '')
            cidade = cidade.rsplit(' - ', 1)[0] if ' - ' in cidade else cidade
            metro.setdefault(CODIGOS[int(c)], {})[cidade] = _num(r.get('V'))

    return {'fonte': SIDRA, 'portal': 'https://sidra.ibge.gov.br/tabela/7060',
            'competencia': _rotulo(per[-1]),
            'doze_meses': {'periods': [_rotulo(p) for p in per], 'series': series},
            'metropolitano': metro}

def main():
    ap = argparse.ArgumentParser(description='Atualiza a camada de IPCA do dashboard.')
    ap.add_argument('--mostrar', action='store_true', help='só imprime, não grava')
    a = ap.parse_args()

    print('consultando a API do SIDRA (IBGE)…')
    ip = puxar()
    print(f"competência: {ip['competencia']}  ·  {len(ip['doze_meses']['series'])} séries  ·  "
          f"{len(next(iter(ip['metropolitano'].values())))} praças\n")
    for rot, vals in ip['doze_meses']['series'].items():
        ult = next((v for v in reversed(vals) if v is not None), None)
        print(f'   {rot:40} {ult:6.2f}%')

    if a.mostrar: return
    if not os.path.exists(DADOS):
        sys.exit('\nNão encontrei dados.json nesta pasta.')
    D = json.load(open(DADOS, encoding='utf-8'))
    antes = (D.get('ipca') or {}).get('competencia')
    D['ipca'] = ip
    json.dump(D, open(DADOS, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f"\ndados.json atualizado: {antes or '—'} -> {ip['competencia']}")


# ======================================================================
# ORQUESTRAÇÃO E LINHA DE COMANDO
# ======================================================================

#!/usr/bin/env python3
"""
atualizar.py — rotina de atualização do Healthcare Database Dashboard
=====================================================================

Consulta as fontes oficiais brasileiras de saúde suplementar, detecta
competências novas e regrava o `dados.json` lido pelo dashboard.

Uso:
    python3 atualizar.py --verificar            # só lista o que há de novo
    python3 atualizar.py --atualizar            # baixa e regrava dados.json
    python3 atualizar.py --fonte ans_rpc        # trabalha só numa fonte

    # séries por operadora, UF, contratação e faixa etária (o que hoje para em mai/26):
    python3 atualizar.py --beneficiarios 202606 --conferir   # processa e mostra o agrupamento
    python3 atualizar.py --beneficiarios 202606              # incorpora ao dados.json
    python3 atualizar.py --beneficiarios 202606 --uf SP RJ   # só duas UFs, para testar rápido
    python3 atualizar.py --atualizar --desde 202601

O dashboard procura um `dados.json` na mesma pasta do HTML. Se encontrar,
usa ele; se não, usa a base embutida no próprio arquivo. Ou seja: rodar
este script e deixar o `dados.json` ao lado do HTML já atualiza tudo.

Dependências: requests, pandas (opcional para o parse completo dos CSVs).
    pip install requests pandas
"""
import argparse, json, os, re, sys, time, zipfile, io, datetime
from urllib.parse import urljoin

try:
    import requests
except ImportError:
    print('Instale as dependências:  pip install requests pandas', file=sys.stderr)
    raise

HERE = os.path.dirname(os.path.abspath(__file__))
DADOS = os.path.join(HERE, 'dados.json')
CACHE = os.path.join(HERE, '.cache')
UA = {'User-Agent':'Healthcare-Dashboard-Updater/1.0 (+uso interno)'}
TIMEOUT = 120

# ---------------------------------------------------------------- fontes
# Diretórios verificados em 06/ago/2026. Cada entrada diz onde buscar,
# como reconhecer a competência mais recente e o que ela alimenta.
FONTES = {
 'ans_beneficiarios': {
   'nome': 'ANS · Beneficiários consolidados (PDA-024)',
   'dir': 'https://dadosabertos.ans.gov.br/FTP/PDA/informacoes_consolidadas_de_beneficiarios-024/',
   'tipo': 'dir_ym',
   'alimenta': ['ben.lives_m','ben.share_m','ben.netadds_m','ben.region','ben.state',
                'ben.segment','ben.age_detail','ben.by_modality','ben.dental_lives_m'],
 },
 'ans_ben_operadora': {
   'nome': 'ANS · Beneficiários por operadora (SIB)',
   'dir': 'https://dadosabertos.ans.gov.br/FTP/PDA/dados_de_beneficiarios_por_operadora/',
   'tipo': 'dir_files', 'padrao': r'sib_ativo_([A-Z]{2})\.zip',
   'alimenta': ['ben.lives_m','ben.share_m'],
 },
 'ans_ben_regiao': {
   'nome': 'ANS · Beneficiários por região geográfica',
   'dir': 'https://dadosabertos.ans.gov.br/FTP/PDA/dados_de_beneficiarios_por_regiao_geografica/',
   'tipo': 'dir_files', 'alimenta': ['ben.region','ben.state'],
 },
 'ans_vda': {
   'nome': 'ANS · Vínculos por tipo de contratação (VDA)',
   'dir': 'https://dadosabertos.ans.gov.br/FTP/PDA/beneficiarios_vinculos_tipo_contratacao_vda/',
   'tipo': 'dir_files', 'alimenta': ['ben.segment','ben.segment_share'],
 },
 'ans_diops': {
   'nome': 'ANS · Demonstrações contábeis (DIOPS)',
   'dir': 'https://dadosabertos.ans.gov.br/FTP/PDA/demonstracoes_contabeis/',
   'tipo': 'dir_ano',
   'alimenta': ['fin.is','fin.mlr','fin.prov','legal.civil_ratios','legal.deposits'],
 },
 'ans_rpc': {
   'nome': 'ANS · Reajustes de planos coletivos (RPC / PDA-043)',
   'dir': 'https://dadosabertos.ans.gov.br/FTP/PDA/RPC/',
   'tipo': 'dir_csv_ym', 'padrao': r'pda-043-rpc-(\d{6})\.csv',
   'alimenta': ['price.monthly','price.corporate_annual','price.by_segment_annual','price.sme'],
 },
 'ans_reajuste_agrup': {
   'nome': 'ANS · Reajuste por agrupamento (PDA-055)',
   'dir': 'https://dadosabertos.ans.gov.br/FTP/PDA/percentuais_de_reajuste_de_agrupamento-055/',
   'tipo': 'dir_files', 'alimenta': ['price.sme'],
 },
 'ans_nip': {
   'nome': 'ANS · Demandas dos consumidores (NIP)',
   'dir': 'https://dadosabertos.ans.gov.br/FTP/PDA/demandas_dos_consumidores_nip/',
   'tipo': 'dir_files',
   'tipo_coletor': 'nip',
   'alimenta': ['nip.NIPs','nip.IGR','nip.IGR Growth YoY',
                'nip.NIPs (ex. Reimbursement)','nip.IGR (ex. Reimbursement)'],
 },
 'ans_igr': {
   'nome': 'ANS · Índice Geral de Reclamações',
   'dir': 'https://dadosabertos.ans.gov.br/FTP/PDA/IGR/',
   'tipo': 'dir_files', 'alimenta': ['nip.IGR'],
 },
 'ans_resus_cobranca': {
   'nome': 'ANS · Ressarcimento ao SUS — cobrança e arrecadação',
   'dir': 'https://dadosabertos.ans.gov.br/FTP/PDA/ressarcimento_ao_SUS_cobranca_arrecadacao/',
   'tipo': 'dir_files',
   'alimenta': ['resus.RESUS Charged (R$mn)','resus.RESUS Paid (R$mn)','resus.RESUS Installments (R$mn)'],
 },
 'ans_resus_hc': {
   'nome': 'ANS · Ressarcimento ao SUS — histórico de cobrança',
   'dir': 'https://dadosabertos.ans.gov.br/FTP/PDA/hc_ressarcimento_sus/',
   'tipo': 'dir_files', 'alimenta': ['resus.billing_history'],
 },
 'ans_sip': {
   'nome': 'ANS · Mapa assistencial / SIP',
   'dir': 'https://dadosabertos.ans.gov.br/FTP/PDA/SIP/',
   'tipo': 'dir_files',
   'alimenta': ['pharma.medical_cost','pharma.volume_per_ben','pharma.ticket'],
 },
 'ans_irpi': {
   'nome': 'ANS · Reajuste de planos individuais (nota técnica)',
   'dir': 'https://www.gov.br/ans/pt-br/assuntos/consumidor/reajuste-variacao-de-mensalidade/'
          'reajuste-anual-de-planos-individuais-familiares-1/metodologia-de-calculo',
   'tipo': 'manual', 'alimenta': ['price.individual_formula'],
   'obs': 'PDF anual publicado em abril. Índice 2026-2027 = 5,11% (NT nº 2/2026).',
 },
 'cnes': {
   'nome': 'CNES/DATASUS · Leitos hospitalares',
   'dir': 'https://cnes.datasus.gov.br/pages/downloads/arquivosBaseDados.jsp',
   'tipo': 'cnes',
   'alimenta': ['hosp.units','hosp.by_state','hosp.by_city','hosp.beds_share_city'],
   'obs': 'BASE_DE_DADOS_CNES_AAAAMM.ZIP, mensal. Leitos em rlEstabComplementar.',
 },
 'cnj': {
   'nome': 'CNJ · Novas ações de saúde suplementar',
   'dir': 'https://justica-em-numeros.cnj.jus.br/painel-saude/',
   'tipo': 'cnj', 'alimenta': ['legal.lawsuits'],
   'obs': 'Painel "Estatísticas Processuais de Direito à Saúde", atualizado mensalmente. '
          'Consultado direto no modelo do Power BI publicado. Reproduz a série do BBI '
          'mês a mês. A API pública do DataJud, por outro lado, está ~18 meses atrasada.',
 },
 'sindusfarma': {
   'nome': 'Sindusfarma · Vendas do mercado farmacêutico',
   'dir': 'https://www.sindusfarma.org.br/', 'tipo': 'manual',
   'alimenta': ['pharma.sales','pharma.market_total'],
   'obs': 'Publicação mensal em XLSX/PDF, sem endpoint estável.',
 },
 'anahp': {
   'nome': 'ANAHP · Observatório',
   'dir': 'https://www.anahp.com.br/', 'tipo': 'manual',
   'alimenta': ['hosp.anahp','hosp.anahp_ebitda_annual'],
   'obs': 'Indicadores publicados em PDF; requer extração manual.',
 },
}

MESES = ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez']
def rotulo(ym: str) -> str:
    return f'{MESES[int(ym[4:6])-1]}/{ym[2:4]}'

# ------------------------------------------------------------- utilidades
def get(url: str, **kw) -> requests.Response:
    r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r

def listar(url: str) -> list[str]:
    """Lê um índice Apache e devolve os nomes de arquivo/pasta."""
    html = get(url).text
    return re.findall(r'href="([^"?][^"]*)"', html)

def competencia_mais_recente(fonte: dict) -> str | None:
    t = fonte['tipo']
    if t == 'manual':
        return None
    if t == 'cnes':
        return cnes_competencia_mais_recente()
    if t == 'cnj':
        return None
    itens = listar(fonte['dir'])
    if t == 'dir_ym':
        yms = sorted({m for i in itens for m in re.findall(r'^(20\d{2}(?:0[1-9]|1[0-2]))/$', i)})
        return yms[-1] if yms else None
    if t == 'dir_ano':
        anos = sorted({m for i in itens for m in re.findall(r'^(20\d{2})/$', i)})
        return anos[-1] if anos else None
    if t == 'dir_csv_ym':
        pad = fonte.get('padrao', r'(\d{6})\.csv')
        yms = sorted({m for i in itens for m in re.findall(pad, i)})
        return yms[-1] if yms else None
    if t == 'dir_files':
        arqs = [i for i in itens if not i.endswith('/')]
        return f'{len(arqs)} arquivos' if arqs else None
    return None

# --------------------------------------------------------------- coletas
def baixar(url: str, destino: str) -> str:
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    if os.path.exists(destino):
        return destino
    print(f'    baixando {os.path.basename(destino)} …', flush=True)
    with get(url, stream=True) as r, open(destino, 'wb') as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    return destino

def coletar_rpc(ym: str) -> str:
    """Baixa o CSV de reajustes de planos coletivos de uma competência."""
    url = f"https://dadosabertos.ans.gov.br/FTP/PDA/RPC/pda-043-rpc-{ym}.csv"
    return baixar(url, os.path.join(CACHE, 'rpc', f'pda-043-rpc-{ym}.csv'))

def coletar_beneficiarios(ym: str, ufs: list[str] | None = None) -> list[str]:
    """Baixa os ZIPs consolidados de beneficiários de uma competência."""
    base = f"https://dadosabertos.ans.gov.br/FTP/PDA/informacoes_consolidadas_de_beneficiarios-024/{ym}/"
    itens = [i for i in listar(base) if i.endswith('.zip')]
    if ufs:
        itens = [i for i in itens if any(uf in i for uf in ufs)]
    return [baixar(urljoin(base, i), os.path.join(CACHE, 'ben', ym, i)) for i in itens]

def coletar_diops(ano: str) -> list[str]:
    base = f"https://dadosabertos.ans.gov.br/FTP/PDA/demonstracoes_contabeis/{ano}/"
    itens = [i for i in listar(base) if i.lower().endswith(('.zip', '.csv'))]
    return [baixar(urljoin(base, i), os.path.join(CACHE, 'diops', ano, i)) for i in itens]

# ------------------------------------------------------------ integração
def carregar_base() -> dict:
    """Carrega o dados.json corrente, ou extrai a base embutida do HTML."""
    if os.path.exists(DADOS):
        return json.load(open(DADOS, encoding='utf-8'))
    htmls = [f for f in os.listdir(HERE) if f.endswith('.html')]
    for h in htmls:
        txt = open(os.path.join(HERE, h), encoding='utf-8').read()
        m = re.search(r'let DATA = (\{.*?\});\nconst SOURCES', txt, re.S)
        if m:
            print(f'  base extraída de {h}')
            return json.loads(m.group(1))
    raise SystemExit('Não encontrei dados.json nem um HTML com a base embutida.')

AGREGADO = os.path.join(HERE, 'agregado_ans.json')

def _ym_menos(ym: str, n: int = 1) -> str:
    a, m = int(ym[:4]), int(ym[4:6])
    t = a * 12 + (m - 1) - n
    return f'{t//12:04d}{t%12+1:02d}'

def salvar_agregado(ag: dict, ym: str) -> None:
    """Guarda o agregado desta competência para servir de referência no mês que vem.

    É o que permite encadear por variação sem rebaixar 390 MB da competência
    anterior toda vez: o mês passado já foi medido com esta mesma metodologia.
    """
    nc = sorted(ag.get('nao_classificadas', {}).items(), key=lambda x: -x[1])[:80]
    magro = {'competencia': ym,
             'vidas': {k: round(v, 1) for k, v in ag['vidas'].items()},
             'total': ag['total'], 'total_odonto': ag['total_odonto'],
             # as 80 maiores operadoras sem grupo econômico mapeado: é aqui que
             # se descobre, por exemplo, sob que razão social a Athena aparece
             'nao_classificadas': [{'nome': n, 'vidas': round(v)} for n, v in nc]}
    json.dump(magro, open(AGREGADO, 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    print(f'  agregado_ans.json gravado ({ym}) — referência para o encadeamento do mês que vem')

def carregar_agregado(ym: str):
    if not os.path.exists(AGREGADO):
        return None
    try:
        d = json.load(open(AGREGADO, encoding='utf-8'))
    except Exception:
        return None
    return d if d.get('competencia') == ym else None

def obter_agregado(ym: str):
    """Agregado de `ym`: do cache se existir, senão baixa e processa a competência."""
    ag = carregar_agregado(ym)
    if ag:
        print(f'        referência {rotulo(ym)} veio do cache (agregado_ans.json)')
        return ag
    print(f'        referência {rotulo(ym)} não está em cache — baixando para medir a variação')
    arqs = baixar_competencia(ym, os.path.join(CACHE, 'pda024', ym))
    try:
        conferir_cobertura(arqs)
    except SystemExit as e:
        # referência incompleta não aborta a rodada: só desliga o encadeamento,
        # e aí as operadoras de escopo divergente ficam onde estavam
        print(f'        referência {rotulo(ym)} incompleta — sigo sem encadear\n        {e}')
        return None
    return agregar(arqs, verboso=False)

def gravar_base(d: dict) -> None:
    d.setdefault('meta', {})['atualizado_em'] = datetime.date.today().isoformat()
    json.dump(d, open(DADOS, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'\n  dados.json regravado — {os.path.getsize(DADOS)/1024:.0f} KB')
    print('  deixe o arquivo na mesma pasta do HTML; o dashboard passa a ler dele no próximo carregamento.')

# ---------------------------------------------------------------- ações
def acao_verificar(alvos: list[str]) -> dict:
    print('\nVerificando as fontes oficiais\n' + '='*72)
    achados = {}
    for fid in alvos:
        f = FONTES[fid]
        if f['tipo'] == 'manual':
            print(f'  {f["nome"]}\n      atualização manual — {f.get("obs","")}\n      {f["dir"]}')
            continue
        try:
            ult = competencia_mais_recente(f)
            achados[fid] = ult
            rot = rotulo(ult) if (ult and re.fullmatch(r'\d{6}', ult)) else ult
            print(f'  {f["nome"]}\n      mais recente no diretório: {rot or "não identificado"}')
        except Exception as e:
            print(f'  {f["nome"]}\n      ERRO ao consultar: {e}')
    print('='*72)
    return achados


MES_NUM = {m:i+1 for i,m in enumerate(['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'])}

def _ym_da_base(base):
    """Lê a competência de beneficiários gravada no dados.json ('jun/26' -> '202606')."""
    meta = base.get('meta', {})
    rot = meta.get('vintage_beneficiarios') or (meta.get('reconciliacao') or {}).get('competencia')
    if not rot:
        per = ((base.get('ben') or {}).get('lives_m') or {}).get('periods') or []
        rot = per[-1] if per else None
    if not rot: return None
    m = re.match(r'^([a-z]{3})/(\d{2})$', rot.strip())
    if not m: return None
    return f'20{m.group(2)}{MES_NUM[m.group(1)]:02d}'


def acao_auto():
    """Uma passada só: descobre o que há de novo, baixa, processa e grava."""
    print('\n' + '='*72)
    print('  ATUALIZACAO AUTOMATICA — Healthcare Database Dashboard')
    print('='*72)

    base = carregar_base()
    atual = _ym_da_base(base)
    print(f"\n  Base atual: beneficiarios em {atual or '?'}")

    # ---------- 1) IPCA (API do IBGE, sem download) ----------
    print('\n  [1/5] IPCA — API do IBGE')
    try:
        ip = puxar()
        antes = (base.get('ipca') or {}).get('competencia')
        base['ipca'] = ip
        if antes == ip['competencia']:
            print(f"        ja estava em {ip['competencia']} — nada mudou")
        else:
            print(f"        atualizado: {antes or '—'} -> {ip['competencia']}")
    except Exception as e:
        print(f'        falhou: {e}')
        print('        (checar conexao; o resto da rotina continua)')

    # ---------- 2) Beneficiarios ANS ----------
    print('\n  [2/5] Beneficiarios — PDA-024 da ANS')
    novo = None
    try:
        novo = competencia_mais_recente(FONTES['ans_beneficiarios'])
        print(f'        mais recente publicada: {novo}')
    except Exception as e:
        print(f'        nao consegui consultar o diretorio da ANS: {e}')

    if novo and atual and novo <= atual:
        print('        a base ja esta na competencia mais recente')
    elif novo:
        print(f'        baixando e processando {novo} (~390 MB, alguns minutos)')
        try:
            anterior = _ym_menos(novo)
            ag_ant = obter_agregado(anterior)
            destino = os.path.join(CACHE, 'pda024', novo)
            arquivos = baixar_competencia(novo, destino)
            if arquivos:
                conferir_cobertura(arquivos)
                ag = agregar(arquivos)
                conferir(ag)
                merge(base, ag, novo, ag_ant, anterior)
                salvar_agregado(ag, novo)
            else:
                print('        nenhum arquivo baixado')
        except Exception as e:
            print(f'        falhou no processamento: {e}')

    # ---------- 3) Leitos hospitalares (CNES) ----------
    print('\n  [3/5] Leitos hospitalares — CNES/DATASUS')
    try:
        gravar_base(base)          # o coletor do CNES recarrega o dados.json do disco
        acao_cnes()
        base = carregar_base()
    except Exception as e:
        print(f'        falhou: {e}')
        print('        (o resto da base ja foi gravado; o CNES tenta de novo no mes que vem)')

    # ---------- 4) Reclamacoes (NIP e IGR) ----------
    print('\n  [4/5] Reclamacoes — NIP e IGR da ANS')
    try:
        gravar_base(base)
        acao_nip()
        base = carregar_base()
    except Exception as e:
        print(f'        falhou: {e}')
        print('        (a serie de reclamacoes fica onde estava)')

    # ---------- 5) Judicializacao (painel do CNJ) ----------
    print('\n  [5/5] Judicializacao — painel de Direito a Saude do CNJ')
    try:
        gravar_base(base)
        acao_cnj()
        base = carregar_base()
    except Exception as e:
        print(f'        falhou: {e}')
        print('        (a serie de acoes fica onde estava; tenta de novo na proxima rodada)')

    gravar_base(base)
    print('\n  Pronto. Abra o dashboard — ele le o dados.json automaticamente.')
    print('='*72 + '\n')


def acao_beneficiarios(ym, ufs, apenas_conferir, cache_dir=None):
    """Baixa, parseia e incorpora uma competência do PDA-024."""
    
    destino = cache_dir or os.path.join(CACHE, 'pda024', ym)
    print(f'\nCompetência {ym} — PDA-024 (informações consolidadas de beneficiários)')
    print('='*72)
    print('   baixando (são ~360 MB no total; o cache evita rebaixar)')
    arquivos = baixar_competencia(ym, destino, ufs)
    conferir_cobertura(arquivos, ufs)

    print(f'\n   processando {len(arquivos)} arquivos')
    ag = agregar(arquivos)
    conferir(ag)

    if apenas_conferir:
        print('\n   modo --conferir: nada foi gravado.')
        print('   Revise o agrupamento acima; ajuste GRUPOS em py se algum')
        print('   nome de operadora tiver caído no balde errado, e rode de novo.')
        return

    base = carregar_base()
    anterior = _ym_menos(ym)
    merge(base, ag, ym, obter_agregado(anterior), anterior)
    salvar_agregado(ag, ym)
    gravar_base(base)


def acao_atualizar(alvos, desde):
    base = carregar_base()
    print(f"\nBase atual: {base.get('meta',{}).get('vintage','—')}")
    achados = acao_verificar(alvos)
    baixados = []
    for fid, ult in achados.items():
        if not ult: continue
        f = FONTES[fid]
        try:
            if fid == 'ans_rpc' and re.fullmatch(r'\d{6}', ult):
                yms = [ult]
                if desde:
                    ini_ = int(desde)
                    yms = [f'{a}{m:02d}' for a in range(ini_//100, int(ult[:4])+1) for m in range(1,13)]
                    yms = [y for y in yms if ini_ <= int(y) <= int(ult)]
                for ym in yms:
                    try: baixados.append(coletar_rpc(ym))
                    except Exception as e: print(f'      {ym}: {e}')
            elif fid == 'ans_diops' and re.fullmatch(r'\d{4}', ult):
                baixados += coletar_diops(ult)
        except Exception as e:
            print(f'  {f["nome"]}: falha ao coletar — {e}')
    if baixados:
        print(f'\n  {len(baixados)} arquivos brutos em {CACHE}')
    print('\n  Para as séries por operadora, rode:  python3 atualizar.py --beneficiarios AAAAMM')
    gravar_base(base)


# ======================================================================
# LEITOS HOSPITALARES — CNES/DATASUS
# ======================================================================

"""
Coletor do CNES. A base mensal completa (BASE_DE_DADOS_CNES_AAAAMM.ZIP) traz
`rlEstabComplementar` — uma linha por estabelecimento × tipo de leito, com a
quantidade existente e quanta dela é contratada pelo SUS.

Duas coisas saem daqui:

  * leitos por hospital, para as 197 unidades que o dashboard acompanha. Exige
    casar o nome curto da base ("São Luiz Itaim") com o nome de fantasia do
    CNES. O casamento é feito UMA vez, validado contra os leitos que a base já
    tem em jun/26, e congelado em cnes_map.json.
  * leitos privados por UF e por município, que são só agregação — não
    dependem de casar nome nenhum.

Como o formato exato dos CSVs do CNES não está documentado de forma estável,
o parser casa coluna por padrão no cabeçalho e registra o que casou, igual ao
da ANS. Se o DATASUS renomear um campo, o script avisa em vez de somar errado.
"""

# A página de downloads do CNES é AngularJS: o HTML vem vazio e a lista de
# arquivos chega depois, deste serviço. Ler o HTML não devolve nada.
CNES_LISTA = 'https://cnes.datasus.gov.br/services/arquivos-download/base-dados/'
CNES_ARQUIVO = 'https://cnes.datasus.gov.br/EstatisticasServlet?path=BASE_DE_DADOS_CNES_{ym}.ZIP'
MAPA_CNES = os.path.join(HERE, 'cnes_map.json')
DIAG_CNES = os.path.join(HERE, 'diagnostico_cnes.json')

# palavras que não distinguem um hospital de outro e só atrapalham o casamento
RUIDO = {'hospital','hosp','clinica','clínica','sa','s','a','ltda','de','da','do','dos','das',
         'e','o','os','as','em','ltd','me','eireli','instituto','centro','medico','médico',
         'unidade','und','filial','matriz','grupo','rede','h'}


# O DATASUS devolve 503 para cliente que não parece navegador. Não é rejeição
# de conteúdo — é o WAF na frente do serviço. Com estes cabeçalhos ele responde.
CAB_CNES = {
 'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'),
 'Accept': 'application/json, text/plain, */*',
 'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
 'Referer': 'https://cnes.datasus.gov.br/pages/downloads/arquivosBaseDados.jsp',
 'Connection': 'keep-alive',
}


def _get_cnes(url, tentativas=4, **kw):
    """GET no DATASUS com cara de navegador e paciência com o 503."""
    ultima = None
    for k in range(tentativas):
        try:
            r = requests.get(url, headers=CAB_CNES, timeout=(30, 300), **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                ultima = f'{r.status_code} {r.reason}'
                espera = 8 * (k + 1)
                print(f'        DATASUS respondeu {r.status_code}; nova tentativa em {espera}s')
                time.sleep(espera); continue
            r.raise_for_status()
            return r
        except Exception as e:
            ultima = str(e)
            if k == tentativas - 1: break
            time.sleep(8 * (k + 1))
    raise RuntimeError(f'DATASUS indisponível para este cliente após {tentativas} tentativas '
                       f'({ultima}). O serviço costuma barrar requisição de fora do Brasil.')


def cnes_competencia_mais_recente():
    """Competência mais nova publicada pelo CNES.

    O serviço devolve [{sequencial, nomeArquivo}]. Leio o texto cru e caço o
    padrão do nome: se um dia mudarem o formato do JSON, o regex ainda acha.
    """
    txt = _get_cnes(CNES_LISTA).text
    yms = sorted(set(re.findall(r'BASE_DE_DADOS_CNES_(\d{6})\.ZIP', txt, re.I)))
    if not yms:
        print('        o serviço do CNES respondeu, mas sem nenhum nome de arquivo reconhecível')
    return yms[-1] if yms else None


def baixar_cnes(ym):
    destino = os.path.join(CACHE, 'cnes', f'BASE_DE_DADOS_CNES_{ym}.ZIP')
    if os.path.exists(destino) and _zip_integro(destino):
        print(f'        {os.path.basename(destino)} já em cache')
        return destino
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    print(f'        baixando a base do CNES de {rotulo(ym)} (algumas centenas de MB)')
    with _get_cnes(CNES_ARQUIVO.format(ym=ym), stream=True) as r, open(destino, 'wb') as f:
        for pedaco in r.iter_content(1 << 20):
            f.write(pedaco)
    if not _zip_integro(destino):
        os.remove(destino)
        raise RuntimeError('o arquivo do CNES veio truncado ou não é um ZIP legível')
    return destino


def _membro(z, padrao):
    for nome in z.namelist():
        base = os.path.basename(nome)
        if re.search(padrao, base, re.I) and base.lower().endswith(('.csv', '.txt')):
            return nome
    return None


def _csv_cnes(z, nome):
    """Gera (cabeçalho, linhas) de um CSV do CNES, detectando codificação e separador."""
    with z.open(nome) as fh:
        amostra = fh.read(1 << 16)
    try:
        amostra.decode('utf-8'); enc = 'utf-8'
    except UnicodeDecodeError:
        enc = 'latin-1'
    primeira = amostra.decode(enc, 'replace').split('\n', 1)[0]
    sep = ';' if primeira.count(';') >= primeira.count(',') else ','
    with z.open(nome) as fh:
        leitor = csv.reader(io.TextIOWrapper(fh, encoding=enc, errors='replace', newline=''),
                            delimiter=sep)
        cab = next(leitor, [])
        cab = [c.strip().strip('"').upper() for c in cab]
        yield cab
        for linha in leitor:
            yield linha


def _coluna(cab, *padroes):
    for p in padroes:
        for i, c in enumerate(cab):
            if re.fullmatch(p, c): return i
    for p in padroes:
        for i, c in enumerate(cab):
            if re.search(p, c): return i
    return None


def ler_leitos(caminho, verboso=True):
    """Soma os leitos do CNES por estabelecimento e por município.

    Devolve dict com:
      por_unidade  -> cod13 -> {'total':n, 'uti':n, 'nao_sus':n}
      por_municipio-> cod6  -> {'total':n, 'uti':n, 'nao_sus':n}
      por_uf       -> uf2   -> idem
      tipos        -> descrição do leito -> total (diagnóstico)
    """
    with zipfile.ZipFile(caminho) as z:
        alvo = _membro(z, r'rlEstabComplementar')
        if not alvo:
            raise RuntimeError('rlEstabComplementar não encontrado no ZIP do CNES; '
                               f'membros: {z.namelist()[:12]}')
        # tabela de tipos de leito, para separar UTI do resto
        tipos = {}
        tl = _membro(z, r'tbLeito')
        if tl:
            g = _csv_cnes(z, tl); cab = next(g)
            i_cod = _coluna(cab, r'CO_LEITO')
            i_tip = _coluna(cab, r'CO_TIPO_LEITO', r'TP_LEITO')
            i_ds = _coluna(cab, r'DS_LEITO', r'NO_LEITO', r'DS_TIPO_LEITO')
            for l in g:
                if i_cod is None or i_cod >= len(l): continue
                cod = l[i_cod].strip().strip('"')
                tipos[cod] = {
                  'tipo': l[i_tip].strip().strip('"') if i_tip is not None and i_tip < len(l) else '',
                  'ds': l[i_ds].strip().strip('"') if i_ds is not None and i_ds < len(l) else '',
                }
            if verboso:
                print(f'   tbLeito: {len(tipos)} códigos de leito')

        g = _csv_cnes(z, alvo); cab = next(g)
        i_un = _coluna(cab, r'CO_UNIDADE', r'CO_CNES')
        i_le = _coluna(cab, r'CO_LEITO')
        i_ex = _coluna(cab, r'QT_EXIST')
        i_sus = _coluna(cab, r'QT_SUS', r'QT_CONTR')
        if verboso:
            print(f'   {os.path.basename(alvo)} — colunas: unidade={cab[i_un] if i_un is not None else "?"}, '
                  f'leito={cab[i_le] if i_le is not None else "?"}, '
                  f'existente={cab[i_ex] if i_ex is not None else "?"}, '
                  f'sus={cab[i_sus] if i_sus is not None else "?"}')
        if i_un is None or i_ex is None:
            raise RuntimeError(f'não reconheci as colunas de rlEstabComplementar: {cab}')

        def novo(): return {'total': 0, 'uti': 0, 'nao_sus': 0}
        por_un, por_mun, por_uf, por_tipo = {}, {}, {}, {}
        for l in g:
            if i_un >= len(l) or i_ex >= len(l): continue
            un = l[i_un].strip().strip('"')
            if not un: continue
            try: qt = int(float(l[i_ex].strip().strip('"') or 0))
            except ValueError: continue
            if qt <= 0: continue
            sus = 0
            if i_sus is not None and i_sus < len(l):
                try: sus = int(float(l[i_sus].strip().strip('"') or 0))
                except ValueError: sus = 0
            cod = l[i_le].strip().strip('"') if i_le is not None and i_le < len(l) else ''
            info = tipos.get(cod, {})
            ds = info.get('ds', '') or cod
            eh_uti = (str(info.get('tipo', '')).strip() == '3'
                      or bool(re.search(r'\bUTI\b|INTENSIV', ds, re.I)))
            por_tipo[ds] = por_tipo.get(ds, 0) + qt
            mun, uf = un[:6], un[:2]
            for chave, d in ((un, por_un), (mun, por_mun), (uf, por_uf)):
                a = d.setdefault(chave, novo())
                a['total'] += qt
                a['nao_sus'] += max(qt - sus, 0)
                if eh_uti: a['uti'] += qt

        # nome dos municípios, para casar com as cidades da base
        municipios = {}
        tm = _membro(z, r'tbMunicipio')
        if tm:
            g2 = _csv_cnes(z, tm); c2 = next(g2)
            i_c = _coluna(c2, r'CO_MUNICIPIO', r'CO_IBGE')
            i_n = _coluna(c2, r'NO_MUNICIPIO', r'DS_MUNICIPIO', r'NO_.*MUNIC')
            i_u = _coluna(c2, r'CO_SIGLA_ESTADO', r'SG_UF', r'CO_UF')
            for l in g2:
                if i_c is None or i_n is None or i_c >= len(l) or i_n >= len(l): continue
                municipios[l[i_c].strip().strip('"')[:6]] = (
                    l[i_n].strip().strip('"'),
                    l[i_u].strip().strip('"') if i_u is not None and i_u < len(l) else '')
            if verboso: print(f'   tbMunicipio: {len(municipios)} municípios')

        # nome de fantasia dos estabelecimentos, para casar com as unidades da base
        estab = {}
        te = _membro(z, r'tbEstabelecimento')
        if te:
            g3 = _csv_cnes(z, te); c3 = next(g3)
            i_u3 = _coluna(c3, r'CO_UNIDADE', r'CO_CNES')
            i_f3 = _coluna(c3, r'NO_FANTASIA')
            i_r3 = _coluna(c3, r'NO_RAZAO_SOCIAL', r'NO_EMPRESARIAL')
            for l in g3:
                if i_u3 is None or i_u3 >= len(l): continue
                u = l[i_u3].strip().strip('"')
                if u not in por_un: continue      # só interessa quem tem leito
                estab[u] = (l[i_f3].strip().strip('"') if i_f3 is not None and i_f3 < len(l) else '',
                            l[i_r3].strip().strip('"') if i_r3 is not None and i_r3 < len(l) else '')
            if verboso: print(f'   tbEstabelecimento: {len(estab)} estabelecimentos com leito')

    if verboso:
        tot = sum(v['total'] for v in por_un.values())
        print(f'   {len(por_un):,} estabelecimentos com leito · {tot:,} leitos existentes')
    return {'por_unidade': por_un, 'por_municipio': por_mun, 'por_uf': por_uf,
            'tipos': por_tipo, 'municipios': municipios, 'estabelecimentos': estab}


# ------------------------------------------------- casamento nome -> CNES
def _tokens(s):
    s = _norm(s).replace('-', ' ')
    return {t for t in re.split(r'[^a-z0-9]+', s) if t and t not in RUIDO and len(t) > 1}


def _pontua(base_nome, candidato):
    a, b = _tokens(base_nome), _tokens(candidato)
    if not a or not b: return 0.0
    inter = len(a & b)
    return inter / len(a) * (0.6 + 0.4 * inter / len(b))


def casar_unidades(D, leitos, tolerancia=0.10, verboso=True):
    """Casa cada unidade do dashboard com um estabelecimento do CNES.

    Só aceita o casamento quando os leitos calculados batem com os que a base
    já traz na última competência — ou seja, quando conseguimos REPRODUZIR o
    número que o BBI publicou. Casamento que não reproduz é casamento errado.
    """
    linhas = D['hosp']['units']['rows']
    periodos = D['hosp']['units']['periods']
    ref = periodos[-1]
    munic = leitos['municipios']; estab = leitos['estabelecimentos']; por_un = leitos['por_unidade']

    # cidade+UF -> códigos de município
    por_cidade = {}
    for cod, (nome, uf) in munic.items():
        por_cidade.setdefault((_norm(nome), (uf or '').upper()), []).append(cod)

    mapa, diag = {}, []
    for k_linha, r in enumerate(linhas):
        if r.get('is_group'): continue
        alvo = (r.get('vals', {}).get('Total Beds') or {}).get(ref)
        cidade, uf = _norm(r.get('city') or ''), (r.get('state') or '').upper()
        codigos = por_cidade.get((cidade, uf)) or []
        candidatos = []
        for u, (fant, razao) in estab.items():
            if codigos and u[:6] not in codigos: continue
            if not codigos and uf and munic.get(u[:6], ('', ''))[1].upper() != uf: continue
            p = max(_pontua(r['name'], fant), _pontua(r['name'], razao) * 0.9)
            if p > 0: candidatos.append((p, u, fant))
        candidatos.sort(reverse=True)
        melhor = None
        for p, u, fant in candidatos[:8]:
            calc = por_un.get(u, {}).get('total', 0)
            erro = abs(calc / alvo - 1) if alvo else None
            if melhor is None: melhor = (p, u, fant, calc, erro)
            # Dois sinais independentes: o nome parecer e o número de leitos
            # bater. Quanto melhor um, menos exigente dá para ser com o outro.
            # "Hospital São Francisco - Bauru" vira "HOSPITAL BAURU" no CNES:
            # nome fraco, mas 45 leitos contra 45 não é coincidência.
            limiar = 0.25 if (erro is not None and erro > 0.02) else 0.15
            if alvo and erro is not None and erro <= tolerancia and p >= limiar:
                mapa[u] = k_linha       # índice da linha: nomes se repetem, índices não
                diag.append({'unidade': r['name'], 'cidade': r.get('city'), 'uf': uf,
                             'cnes': u, 'fantasia': fant, 'pontuacao': round(p, 3),
                             'base': alvo, 'calculado': calc, 'erro': round(erro, 4),
                             'status': 'casado'})
                melhor = None
                break
        if melhor is not None:
            p, u, fant, calc, erro = melhor
            diag.append({'unidade': r['name'], 'cidade': r.get('city'), 'uf': uf,
                         'cnes': u, 'fantasia': fant, 'pontuacao': round(p, 3),
                         'base': alvo, 'calculado': calc,
                         'erro': round(erro, 4) if erro is not None else None,
                         'status': 'sem casamento confiável',
                         'outros': [{'cnes': c[1], 'fantasia': c[2], 'p': round(c[0], 3),
                                     'leitos': por_un.get(c[1], {}).get('total', 0)}
                                    for c in candidatos[1:4]]})
    n_ok = sum(1 for d in diag if d['status'] == 'casado')
    if verboso:
        print(f'\n   Casamento CNES: {n_ok} de {len(diag)} unidades reproduzem os leitos de {ref} '
              f'dentro de {tolerancia:.0%}')
        ruins = [d for d in diag if d['status'] != 'casado'][:12]
        if ruins:
            print('   não casaram (as 12 primeiras):')
            for d in ruins:
                print(f"      {d['unidade'][:32]:32} {str(d['cidade'])[:14]:14} "
                      f"base={d['base']} melhor={d['fantasia'][:34]!r} calc={d['calculado']}")
    return mapa, diag


def _slot_lista(periods, p):
    if p in periods: return periods.index(p)
    periods.append(p); return len(periods) - 1


def merge_cnes(D, leitos, mapa, ym, limite=0.15, verboso=True):
    """Grava a competência do CNES em hosp.units e recalcula os grupos.

    `mapa` é cnes_map.json: código CNES -> nome da unidade no dashboard.
    Unidade cujo salto de leitos passe de `limite` não é gravada: hospital não
    dobra de tamanho num mês, então isso é troca de código CNES, fusão de
    unidade ou erro de casamento — casos em que o certo é não escrever.
    """
    p = rotulo(ym)
    u = D['hosp']['units']
    periodos = u['periods']
    anterior = periodos[-1] if periodos else None
    i = _slot_lista(periodos, p)
    por_un = leitos['por_unidade']

    # o mapa aponta para o ÍNDICE da linha, não para o nome: há unidades
    # homônimas em cidades diferentes ("São Lucas") e somá-las seria errado
    linhas = {k: r for k, r in enumerate(u['rows']) if not r.get('is_group')}
    por_nome = {}
    for k, r in linhas.items():
        por_nome.setdefault((r['name'], r.get('city'), r.get('state')), []).append(k)
    soma = {}
    for cod, ref_linha in mapa.items():
        d = por_un.get(cod)
        if not d: continue
        if isinstance(ref_linha, int):
            ks = [ref_linha]
        else:
            ks = por_nome.get(tuple(ref_linha) if isinstance(ref_linha, list) else
                              (ref_linha, None, None), [])
            if not ks:
                ks = [k for k, r in linhas.items() if r['name'] == ref_linha]
            ks = ks[:1]     # nome ambíguo: fica com a primeira, e só
        for k in ks:
            a = soma.setdefault(k, {'total': 0, 'uti': 0})
            a['total'] += d['total']; a['uti'] += d['uti']

    gravadas, recusadas, sem_dado = 0, [], 0
    for k, r in linhas.items():
        novo = soma.get(k)
        vals = r.setdefault('vals', {})
        if not novo:
            sem_dado += 1
            continue
        ant = (vals.get('Total Beds') or {}).get(anterior)
        if ant and abs(novo['total'] / ant - 1) > limite:
            recusadas.append((r['name'], ant, novo['total']))
            continue
        vals.setdefault('Total Beds', {})[p] = float(novo['total'])
        vals.setdefault('UTI Beds', {})[p] = float(novo['uti'])
        gravadas += 1

    # grupos = soma das unidades que vêm logo abaixo dele na lista
    # Unidade sem número novo entra com o último conhecido: leito hospitalar é
    # estoque, quase não anda de um mês para o outro, e zerar o grupo inteiro
    # por causa de uma unidade seria pior. O grupo fica marcado como parcial.
    idx = [k for k, r in enumerate(u['rows']) if r.get('is_group')]
    parciais = []
    for k, ini in enumerate(idx):
        fim = idx[k + 1] if k + 1 < len(idx) else len(u['rows'])
        membros = u['rows'][ini + 1:fim]
        g = u['rows'][ini]
        if not membros: continue
        faltando = 0
        for col in ('Total Beds', 'UTI Beds'):
            total, ausentes = 0.0, 0
            for mb in membros:
                serie = mb.get('vals', {}).get(col) or {}
                v = serie.get(p)
                if v is None:
                    v = serie.get(anterior)
                    if v is None: continue
                    ausentes += 1
                total += v
            if col == 'Total Beds': faltando = ausentes
            if ausentes > len(membros) * 0.2:
                continue      # mais de 20% carregado do mês anterior: não publica
            g.setdefault('vals', {}).setdefault(col, {})[p] = float(total)
        if faltando:
            g['parcial'] = {p: faltando}
            parciais.append((g['name'], faltando, len(membros)))

    if verboso:
        print(f'\n   CNES {p}: {gravadas} unidades gravadas · {sem_dado} sem casamento · '
              f'{len(recusadas)} recusadas por salto acima de {limite:.0%}')
        for nome, ant, novo in recusadas[:10]:
            print(f'      recusada {nome[:34]:34} {ant:7.0f} -> {novo:7.0f}')
        for nome, f, n in parciais:
            print(f'      grupo {nome}: {f} de {n} unidades vieram do mês anterior')
    D.setdefault('meta', {})['vintage_cnes'] = p
    return {'periodo': p, 'gravadas': gravadas, 'sem_casamento': sem_dado,
            'grupos_parciais': [{'grupo': n, 'carregadas': f, 'unidades': t}
                                for n, f, t in parciais],
            'recusadas': [{'unidade': n, 'anterior': a, 'novo': v} for n, a, v in recusadas]}


def calibrar_uf(D, leitos):
    """Descobre QUAL agregado do CNES reproduz os totais por UF da base.

    A base traz, por exemplo, 23.251 leitos em São Paulo. Isso pode ser o total
    de leitos, só os não-SUS, ou outro recorte. Em vez de adivinhar, calcula os
    candidatos e mede qual chega mais perto — o resultado vai para o
    diagnóstico e é lido antes de ligar essa parte.
    """
    UF_COD = {'11':'RO','12':'AC','13':'AM','14':'RR','15':'PA','16':'AP','17':'TO','21':'MA',
              '22':'PI','23':'CE','24':'RN','25':'PB','26':'PE','27':'AL','28':'SE','29':'BA',
              '31':'MG','32':'ES','33':'RJ','35':'SP','41':'PR','42':'SC','43':'RS','50':'MS',
              '51':'MT','52':'GO','53':'DF'}
    NOME_UF = {'São Paulo':'SP','Rio de Janeiro':'RJ','Minas Gerais':'MG','Brasília':'DF',
               'Distrito Federal':'DF','Bahia':'BA','Pernambuco':'PE','Ceará':'CE','Paraná':'PR',
               'Goiás':'GO','Amazonas':'AM','Rio Grande do Sul':'RS','Santa Catarina':'SC',
               'Espírito Santo':'ES','Pará':'PA','Maranhão':'MA','Paraíba':'PB',
               'Rio Grande do Norte':'RN','Alagoas':'AL','Sergipe':'SE','Piauí':'PI',
               'Mato Grosso':'MT','Mato Grosso do Sul':'MS','Tocantins':'TO','Rondônia':'RO'}
    por_uf = {}
    for cod, d in leitos['por_uf'].items():
        uf = UF_COD.get(cod)
        if uf: por_uf[uf] = d
    bs = D['hosp']['by_state']
    ref = bs['periods'][-1]
    saida = []
    for r in bs['rows']:
        if r.get('level') != 'state': continue
        uf = NOME_UF.get(r['name'])
        base = (r.get('beds') or {}).get(ref)
        if not uf or not base or uf not in por_uf: continue
        d = por_uf[uf]
        saida.append({'uf': uf, 'base': base, 'total': d['total'], 'nao_sus': d['nao_sus'],
                      'erro_total': round(d['total'] / base - 1, 4),
                      'erro_nao_sus': round(d['nao_sus'] / base - 1, 4)})
    if saida:
        mt = sum(abs(x['erro_total']) for x in saida) / len(saida)
        mn = sum(abs(x['erro_nao_sus']) for x in saida) / len(saida)
        print(f'\n   Calibragem por UF ({len(saida)} estados, referência {ref}):')
        print(f'      leitos totais   — erro médio {mt:.1%}')
        print(f'      leitos não-SUS  — erro médio {mn:.1%}')
        print(f'      melhor candidato: {"não-SUS" if mn < mt else "total"}')
    return saida


def acao_cnes(ym=None, descobrir=False, verboso=True):
    """Atualiza os leitos hospitalares a partir da base mensal do CNES."""
    print('\n  Leitos hospitalares — CNES/DATASUS')
    ym = ym or cnes_competencia_mais_recente()
    if not ym:
        print('        não consegui identificar a competência mais recente'); return None
    print(f'        competência mais recente: {rotulo(ym)}')
    base = carregar_base()
    if (base.get('meta') or {}).get('vintage_cnes') == rotulo(ym) and not descobrir:
        print('        a base já está nessa competência'); return None
    caminho = baixar_cnes(ym)
    print(f'        {os.path.basename(caminho)} — {os.path.getsize(caminho)/1e6:.0f} MB')
    leitos = ler_leitos(caminho, verboso)

    mapa = {}
    if os.path.exists(MAPA_CNES) and not descobrir:
        mapa = {k: v for k, v in json.load(open(MAPA_CNES, encoding='utf-8')).items()
                if not k.startswith('_')}
        print(f'        cnes_map.json: {len(mapa)} unidades já mapeadas')
    else:
        print('        primeira rodada — casando as unidades do dashboard com o CNES')
        mapa, diag_casamento = casar_unidades(base, leitos)
        json.dump(mapa, open(MAPA_CNES, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
        uf = calibrar_uf(base, leitos)
        json.dump({'competencia': ym,
                   'tipos_de_leito': sorted(leitos['tipos'].items(), key=lambda x: -x[1])[:40],
                   'casamento': diag_casamento, 'por_uf': uf},
                  open(DIAG_CNES, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f'        diagnostico_cnes.json gravado — é por ele que se conferem os casamentos')

    if not mapa:
        print('        nenhuma unidade mapeada; nada gravado'); return None
    res = merge_cnes(base, leitos, mapa, ym, verboso=verboso)
    gravar_base(base)
    return res


# ======================================================================
# JUDICIALIZAÇÃO — CNJ / Painel de Estatísticas de Direito à Saúde
# ======================================================================

"""
Coletor do painel "Estatísticas Processuais de Direito à Saúde" do CNJ
(justica-em-numeros.cnj.jus.br/painel-saude/).

Por que este painel e não a API pública do DataJud: a API está cerca de 18
meses atrasada, enquanto o painel é atualizado mensalmente e já traz
"Dados até 30/06/2026". Mais importante: os números batem. A série
`legal.lawsuits` do relatório do BBI, somada de jan a jun/26, dá 181.643 —
exatamente o "Entradas em 2026 · Novos" que o painel mostra para Saúde
Suplementar. Mês a mês também: jan/26 = 23.608 nos dois. O BBI é consumidor
desta mesma fonte.

O painel é um Power BI publicado na web. Publicação desse tipo aceita consulta
direta ao endpoint /public/reports/querydata com a chave do relatório — não é
raspagem de tela, é a mesma consulta semântica que o painel faz, com o mesmo
resultado. O modelo tem:

    tbl_fato_materias_R   ano · mes · sigla_grau · materia · Proc_cn · ramo_justica
    medidas_matéria       "dd Novos_temas"  (casos novos)

e sigla_grau separa exatamente as três séries do dashboard:
    sem filtro -> All instances · G1 -> 1st instance · JE -> Special Civil Courts

Duas armadilhas descobertas na prática, ambas tratadas abaixo:
  * reaproveitar o QueryId da consulta capturada faz o serviço devolver um
    resultado em cache, com outro formato e ignorando o filtro. Cada requisição
    leva um QueryId novo.
  * sem DataReduction, a resposta é truncada em 100 linhas.
"""

CNJ_PAINEL = 'https://justica-em-numeros.cnj.jus.br/painel-saude/'
CNJ_CHAVE = 'cc26e866-9709-48a9-93af-0c4e5d0bc99e'
CNJ_DATASET = '65185df1-393e-4827-9e0e-3ddf0ff2c619'
CNJ_RELATORIO = '6f49513b-2b93-4c9e-8db8-bc2b5ede6d8f'
CNJ_VISUAL = '124a03566950da4bfca9'
CNJ_MODELO = 3223105
# O cluster é regional. O primeiro é o que responde hoje; os outros são rede de
# segurança para o dia em que a Microsoft mudar o relatório de região.
CNJ_HOSTS = [
 'https://wabi-us-east-a-primary-api.analysis.windows.net',
 'https://wabi-brazil-south-b-primary-api.analysis.windows.net',
 'https://wabi-south-central-us-a-primary-api.analysis.windows.net',
 'https://wabi-west-europe-d-primary-api.analysis.windows.net',
]
CNJ_MATERIA = ' Saúde Suplementar'      # o espaço à esquerda é do próprio modelo
CNJ_RAMOS = ['Justiça Estadual', 'Justiça Federal', 'Tribunais Superiores']
CNJ_GRAUS = ['G1', 'G2', 'JE', 'SUP', 'TR', 'TRU', 'TNU']
CNJ_SERIES = {None: 'All instances', 'G1': '1st instance', 'JE': 'Special Civil Courts'}
DIAG_CNJ = os.path.join(HERE, 'diagnostico_cnj.json')

_cnj_host_ok = None


def _lit(v):
    return {'Literal': {'Value': f"'{v}'"}}


def _col(prop, fonte='t'):
    return {'Column': {'Expression': {'SourceRef': {'Source': fonte}}, 'Property': prop}}


def _cnj_corpo(grau=None, indice=0):
    """Monta a consulta semântica: casos novos de Saúde Suplementar por ano e mês."""
    onde = [
      {'Condition': {'In': {'Expressions': [_col('Proc_cn')],
                            'Values': [[_lit('Processo (casos novos)')]]}}},
      {'Condition': {'In': {'Expressions': [_col('materia')],
                            'Values': [[_lit(CNJ_MATERIA)]]}}},
      {'Condition': {'In': {'Expressions': [_col('ramo_justica')],
                            'Values': [[_lit(r)] for r in CNJ_RAMOS]}}},
    ]
    if grau:
        onde.append({'Condition': {'In': {'Expressions': [_col('sigla_grau')],
                                          'Values': [[_lit(grau)]]}}})
    return {
      'version': '1.0.0',
      'queries': [{
        'Query': {'Commands': [{'SemanticQueryDataShapeCommand': {
          'Query': {
            'Version': 2,
            'From': [{'Name': 'm', 'Entity': 'medidas_matéria', 'Type': 0},
                     {'Name': 't', 'Entity': 'tbl_fato_materias_R', 'Type': 0}],
            'Select': [
              dict(_col('ano'), Name='t.ano'),
              dict(_col('mes'), Name='t.mes'),
              {'Measure': {'Expression': {'SourceRef': {'Source': 'm'}},
                           'Property': 'dd Novos_temas'},
               'Name': 'medidas_matéria.dd Novos_temas'},
            ],
            'Where': onde,
          },
          'Binding': {'Primary': {'Groupings': [{'Projections': [0, 1, 2]}]},
                      'DataReduction': {'DataVolume': 4,
                                        'Primary': {'Window': {'Count': 30000}}},
                      'Version': 1},
        }}]},
        # QueryId novo a cada chamada: reaproveitar traz resposta em cache
        'QueryId': f'hc{indice}{int(time.time()*1000) % 1000000}',
        'ApplicationContext': {'DatasetId': CNJ_DATASET,
                               'Sources': [{'ReportId': CNJ_RELATORIO,
                                            'VisualId': CNJ_VISUAL}]},
      }],
      'cancelQueries': [],
      'modelId': CNJ_MODELO,
    }


def _cnj_consulta(corpo, tentativas=3):
    """POST na consulta pública, testando os clusters até um responder."""
    global _cnj_host_ok
    hosts = ([_cnj_host_ok] if _cnj_host_ok else []) + \
            [h for h in CNJ_HOSTS if h != _cnj_host_ok]
    ultimo = None
    for host in hosts:
        for k in range(tentativas):
            try:
                r = requests.post(host + '/public/reports/querydata?synchronous=true',
                                  json=corpo, timeout=(30, 180),
                                  headers={'X-PowerBI-ResourceKey': CNJ_CHAVE,
                                           'Content-Type': 'application/json;charset=UTF-8',
                                           'User-Agent': CAB_CNES['User-Agent']})
                if r.status_code >= 500 or r.status_code == 429:
                    ultimo = f'{r.status_code}'; time.sleep(4 * (k + 1)); continue
                r.raise_for_status()
                _cnj_host_ok = host
                return r.json()
            except Exception as e:
                ultimo = str(e)[:120]
                time.sleep(3 * (k + 1))
    raise RuntimeError(f'nenhum cluster do Power BI respondeu ({ultimo})')


def _dsr_linhas(js, n_col):
    """Reconstrói as linhas do formato compacto do Power BI.

    O serviço omite valor repetido (bitmask R) e valor nulo (bitmask Ø),
    então uma linha só traz o que mudou em relação à anterior.
    """
    res = (((js.get('results') or [{}])[0].get('result') or {}).get('data') or {})
    ds = ((res.get('dsr') or {}).get('DS') or [{}])[0]
    ph = (ds.get('PH') or [{}])[0]
    dm = ph.get('DM0') or []
    linhas, ant = [], [None] * n_col
    for r in dm:
        c = r.get('C') or []
        rep, nul = r.get('R', 0), r.get('Ø', 0)
        v, k = [], 0
        for i in range(n_col):
            if nul >> i & 1:      v.append(None)
            elif rep >> i & 1:    v.append(ant[i])
            else:
                v.append(c[k] if k < len(c) else None); k += 1
        ant = v
        linhas.append(v)
    return linhas


def _cnj_mensal(grau=None, indice=0):
    """{'AAAAMM': casos novos} para um grau (ou todos, se grau=None)."""
    linhas = _dsr_linhas(_cnj_consulta(_cnj_corpo(grau, indice)), 3)
    fora = {}
    for ano, mes, valor in linhas:
        if not (isinstance(ano, (int, float)) and isinstance(mes, (int, float))):
            continue
        if not (1 <= int(mes) <= 12) or not (2000 <= int(ano) <= 2100):
            continue
        fora[f'{int(ano)}{int(mes):02d}'] = float(valor or 0)
    return fora


def cnj_painel(verboso=True):
    """Puxa as três séries mensais de novas ações de saúde suplementar.

    A consulta sem filtro de grau às vezes volta agregada por ano — resposta em
    cache do serviço. Quando isso acontece, o total é reconstruído somando os
    sete graus, que é a mesma coisa por definição.
    """
    if verboso: print('        consultando o painel do CNJ')
    series, diag = {}, {}
    total = _cnj_mensal(None, 0)
    if len(total) < 24:
        if verboso:
            print(f'        a consulta geral voltou com {len(total)} meses; '
                  'somando os graus um a um')
        total, por_grau = {}, {}
        for i, g in enumerate(CNJ_GRAUS, start=1):
            d = _cnj_mensal(g, i)
            por_grau[g] = d
            for ym, v in d.items():
                total[ym] = total.get(ym, 0) + v
        diag['reconstruido_por_grau'] = True
        series['1st instance'] = por_grau.get('G1', {})
        series['Special Civil Courts'] = por_grau.get('JE', {})
    series['All instances'] = total
    for i, (g, nome) in enumerate(((g, n) for g, n in CNJ_SERIES.items() if g), start=8):
        if nome not in series:
            series[nome] = _cnj_mensal(g, i)
    ult = max(total) if total else None
    if verboso:
        print(f'        {len(total)} meses, até {rotulo(ult) if ult else "?"}')
        for n, d in series.items():
            print(f'           {n:24} {len(d):3} meses · último {d.get(ult, 0):,.0f}')
    # conferência de identidade: a soma dos graus tem de fechar com o total
    if 'Special Civil Courts' in series and ult:
        soma = sum(_cnj_mensal(g, 20 + i).get(ult, 0) for i, g in enumerate(CNJ_GRAUS)) \
               if diag.get('conferir_soma') else None
        if soma: diag['soma_graus_ultimo_mes'] = soma
    return series, ult, diag



def _ym_para_rotulo(ym):
    return f'{MESES[int(ym[4:6])-1]}/{ym[2:4]}'


def _rotulo_para_ym(p):
    m = re.match(r'^([a-z]{3})/(\d{2})$', p or '')
    return f'20{m.group(2)}{MES_NUM[m.group(1)]:02d}' if m else None


def conferir_cnj(D, series, tolerancia=0.005, ultimos=6):
    """O painel reproduz os meses que a base já tem?

    O CNJ reprocessa o histórico à medida que os tribunais reenviam dados, então
    meses antigos saem um pouco diferentes do que o BBI publicou na época. O que
    precisa bater é a ponta: se os últimos meses coincidem, é a mesma
    metodologia e o mês novo pode entrar na mesma série.
    """
    lw = D['legal']['lawsuits']
    rel = {}
    for nome, dados in series.items():
        base = lw['series'].get(nome)
        if not base: continue
        pares = []
        for i, p in enumerate(lw['periods']):
            ym = _rotulo_para_ym(p)
            if not ym: continue
            b, c = base[i] if i < len(base) else None, dados.get(ym)
            if b is None or c is None or b == 0: continue
            pares.append((p, b, c, (c - b) / b))
        recentes = pares[-ultimos:]
        ok = sum(1 for _, _, _, d in recentes if abs(d) <= tolerancia)
        pior = max(pares, key=lambda x: abs(x[3])) if pares else None
        rel[nome] = {
          'meses_comparados': len(pares),
          'iguais_na_ponta': ok, 'exigido': max(1, len(recentes) - 1),
          'aprovado': ok >= max(1, len(recentes) - 1),
          'ponta': [{'periodo': p, 'base': b, 'painel': c, 'dif': round(d, 5)}
                    for p, b, c, d in recentes],
          'maior_desvio_historico': ({'periodo': pior[0], 'base': pior[1],
                                      'painel': pior[2], 'dif': round(pior[3], 5)}
                                     if pior else None),
        }
    return rel


def merge_cnj(D, series, ultimo, verboso=True, tolerancia=0.005):
    """Acrescenta à série de novas ações os meses que o painel já tem e a base não.

    Não reescreve o histórico. O que o BBI publicou fica como está — o painel
    revisa meses antigos, e trocar número que você já citou em relatório é pior
    do que conviver com a revisão. O tamanho da revisão vai para o diagnóstico.
    """
    lw = D['legal']['lawsuits']
    rel = conferir_cnj(D, series, tolerancia)
    if verboso:
        print('\n   Conferência contra a base (novas ações de saúde suplementar)')
        print('   ' + '-' * 72)
        for nome, r in rel.items():
            print(f'   {nome:24} {r["iguais_na_ponta"]}/{len(r["ponta"])} meses recentes '
                  f'idênticos — {"OK" if r["aprovado"] else "NÃO CONFERE"}')
            md = r['maior_desvio_historico']
            if md:
                print(f'   {"":24} maior revisão no histórico: {md["periodo"]} '
                      f'{md["base"]:,.0f} -> {md["painel"]:,.0f} ({md["dif"]:+.2%})')
    reprovadas = [n for n, r in rel.items() if not r['aprovado']]
    if reprovadas:
        print(f'\n   NÃO gravei nada: {", ".join(reprovadas)} não reproduz a base na ponta.')
        print('   Isso normalmente significa que o CNJ mudou o recorte do painel.')
        return {'gravado': False, 'relatorio': rel}

    # último mês que a base já tem
    mensais = [p for p in lw['periods'] if _rotulo_para_ym(p)]
    ultimo_base = _rotulo_para_ym(mensais[-1]) if mensais else None
    novos = sorted(ym for ym in series['All instances'] if not ultimo_base or ym > ultimo_base)
    if not novos:
        if verboso:
            print(f'\n   O painel está em {rotulo(ultimo)}, mesma competência da base. '
                  'Nada a acrescentar.')
        return {'gravado': False, 'relatorio': rel, 'em_dia': True}

    def escreve(periodo, valores):
        i = len(lw['periods'])
        lw['periods'].append(periodo)
        for nome, vals in lw['series'].items():
            while len(vals) < i: vals.append(None)
            vals.append(valores.get(nome))

    for ym in novos:
        valores = {}
        for nome, dados in series.items():
            v = dados.get(ym)
            if v is None: continue
            valores[nome] = round(v, 0)
            ant = dados.get(f'{int(ym[:4])-1}{ym[4:]}')
            if ant:
                valores[nome + ' YoY'] = round(v / ant - 1, 6)
        escreve(_ym_para_rotulo(ym), valores)
        if ym[4:6] == '12':      # fecha o ano logo depois de dezembro
            ano = ym[:4]
            anuais = {}
            for nome, dados in series.items():
                meses = [v for k, v in dados.items() if k.startswith(ano)]
                if len(meses) == 12: anuais[nome] = round(sum(meses), 0)
            if anuais: escreve(ano, anuais)

    if verboso:
        print(f'\n   {len(novos)} mês(es) acrescentado(s): '
              f'{", ".join(_ym_para_rotulo(y) for y in novos)}')
        for ym in novos:
            v = series['All instances'].get(ym)
            print(f'      {_ym_para_rotulo(ym)}: {v:,.0f} novas ações')
    D.setdefault('meta', {})['vintage_cnj'] = _ym_para_rotulo(ultimo)
    return {'gravado': True, 'relatorio': rel,
            'meses': [_ym_para_rotulo(y) for y in novos]}


def acao_cnj(verboso=True):
    """Atualiza a judicialização pelo painel do CNJ."""
    print('\n  Judicialização — painel de Direito à Saúde do CNJ')
    base = carregar_base()
    series, ultimo, diag = cnj_painel(verboso)
    if not series.get('All instances'):
        print('        o painel não devolveu dados; nada foi gravado'); return None
    res = merge_cnj(base, series, ultimo, verboso)
    json.dump({'painel': CNJ_PAINEL, 'competencia': _ym_para_rotulo(ultimo),
               'series': {n: {_ym_para_rotulo(k): v for k, v in sorted(d.items())}
                          for n, d in series.items()},
               'conferencia': res.get('relatorio'), 'diagnostico': diag},
              open(DIAG_CNJ, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('        diagnostico_cnj.json gravado com a série completa e a conferência')
    if res.get('gravado'):
        gravar_base(base)
    return res



# ======================================================================
# DESCOBERTA DE SCHEMA — o que existe em cada base da ANS
# ======================================================================

"""
Antes de escrever um parser, é preciso saber o nome real das colunas. A ANS
publica cada base com um layout próprio, muda nome de campo sem avisar e mistura
CSV solto com ZIP. Esta rodada baixa o arquivo mais recente de cada diretório,
detecta codificação e separador, e grava cabeçalho, amostra e contagem em
diagnostico_fontes.json. É barato, roda uma vez, e evita três rodadas de
tentativa e erro por base.
"""

DIAG_FONTES = os.path.join(HERE, 'diagnostico_fontes.json')

# diretórios que ainda não têm coletor, na ordem em que pretendo atacá-los
EXPLORAR = ['ans_nip', 'ans_igr', 'ans_rpc', 'ans_reajuste_agrup',
            'ans_diops', 'ans_resus_cobranca', 'ans_resus_hc', 'ans_sip']

CAB_ANS = {'User-Agent': 'Healthcare-Dashboard-Updater/2.0',
           'Accept-Encoding': 'identity'}


def _listar(url):
    r = requests.get(url, headers=CAB_ANS, timeout=(30, 120))
    r.raise_for_status()
    itens = re.findall(r'href="([^"?][^"]*)"', r.text)
    return [i for i in itens if i not in ('../', '/')]


def _mais_novo(itens, sufixos=('.csv', '.zip', '.CSV', '.ZIP')):
    arqs = [i for i in itens if i.endswith(sufixos)]
    if not arqs: return None
    # ordena por qualquer AAAAMM/AAAA que apareça no nome; senão, alfabético
    def chave(n):
        m = re.findall(r'(20\d{4})', n) or re.findall(r'(20\d{2})', n)
        return (m[-1] if m else '', n)
    return sorted(arqs, key=chave)[-1]


def _amostra_csv(bruto, nome, linhas=3):
    """Cabeçalho, separador, codificação e primeiras linhas de um CSV cru."""
    try:
        bruto.decode('utf-8'); enc = 'utf-8'
    except UnicodeDecodeError:
        enc = 'latin-1'
    txt = bruto.decode(enc, 'replace')
    primeiras = txt.splitlines()[:linhas + 1]
    if not primeiras: return {'arquivo': nome, 'vazio': True}
    cab = primeiras[0]
    sep = max([';', ',', '\t', '|'], key=lambda s: cab.count(s))
    return {'arquivo': nome, 'codificacao': enc, 'separador': sep,
            'colunas': [c.strip().strip('"') for c in cab.split(sep)],
            'amostra': [l.split(sep)[:14] for l in primeiras[1:]]}


def explorar_fonte(fid, limite_mb=60):
    f = FONTES[fid]
    saida = {'id': fid, 'nome': f['nome'], 'dir': f['dir'], 'alimenta': f.get('alimenta')}
    try:
        itens = _listar(f['dir'])
    except Exception as e:
        saida['erro'] = f'não consegui listar o diretório: {e}'; return saida
    saida['itens_no_diretorio'] = len(itens)
    # alguns diretórios têm um nível de pasta (por ano ou competência)
    pastas = [i for i in itens if i.endswith('/')]
    alvo_dir = f['dir']
    if pastas and not _mais_novo(itens):
        pasta = sorted(pastas)[-1]
        alvo_dir = urljoin(f['dir'], pasta)
        saida['subpasta'] = pasta
        try:
            itens = _listar(alvo_dir)
        except Exception as e:
            saida['erro'] = f'não consegui listar {pasta}: {e}'; return saida
    nome = _mais_novo(itens)
    if not nome:
        saida['erro'] = 'nenhum csv/zip no diretório'
        saida['exemplos'] = itens[:10]; return saida
    saida['arquivo_mais_recente'] = nome
    url = urljoin(alvo_dir, nome)
    try:
        r = requests.get(url, headers=CAB_ANS, timeout=(30, 300), stream=True)
        r.raise_for_status()
        pedacos, total = [], 0
        for c in r.iter_content(1 << 20):
            pedacos.append(c); total += len(c)
            if total > limite_mb * (1 << 20): break
        bruto = b''.join(pedacos)
        saida['bytes_lidos'] = total
        saida['completo'] = total < limite_mb * (1 << 20)
    except Exception as e:
        saida['erro'] = f'falha ao baixar: {e}'; return saida
    if nome.lower().endswith('.zip'):
        try:
            with zipfile.ZipFile(io.BytesIO(bruto)) as z:
                membros = z.namelist()
                saida['membros_do_zip'] = membros[:20]
                alvos = [n for n in membros if n.lower().endswith(('.csv', '.txt'))][:3]
                saida['tabelas'] = [_amostra_csv(z.open(n).read(1 << 18), n) for n in alvos]
        except Exception as e:
            saida['erro'] = f'ZIP ilegível (pode ser o corte de {limite_mb} MB): {e}'
    else:
        saida['tabelas'] = [_amostra_csv(bruto[:1 << 18], nome)]
    return saida


def acao_explorar(alvos=None, verboso=True):
    """Mapeia o schema das bases da ANS que ainda não têm coletor."""
    print('\n  Descoberta de schema — bases da ANS sem coletor')
    print('  ' + '=' * 70)
    rel = []
    for fid in (alvos or EXPLORAR):
        if fid not in FONTES:
            print(f'  {fid}: fonte desconhecida'); continue
        print(f'\n  {FONTES[fid]["nome"]}')
        d = explorar_fonte(fid)
        rel.append(d)
        if d.get('erro'):
            print(f'      ERRO: {d["erro"]}')
            continue
        print(f'      arquivo: {d.get("arquivo_mais_recente")} '
              f'({d.get("bytes_lidos",0)/1e6:.1f} MB lidos'
              f'{"" if d.get("completo") else ", truncado"})')
        for t in d.get('tabelas', []):
            cols = t.get('colunas') or []
            print(f'      {t.get("arquivo")}: {len(cols)} colunas '
                  f'[{t.get("codificacao")}, sep "{t.get("separador")}"]')
            print(f'         {", ".join(cols[:16])}')
    json.dump(rel, open(DIAG_FONTES, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'\n  diagnostico_fontes.json gravado com {len(rel)} bases')
    return rel


def calibrar_leitos_cnes(ym=None, verboso=True):
    """Descobre qual recorte do CNES reproduz os leitos por UF da base.

    A base traz 23.251 leitos em São Paulo. Nem o total nem só os não-SUS
    chegam perto. Aqui eu calculo vários recortes candidatos de uma vez e meço
    qual erra menos — em vez de adivinhar um por rodada.
    """
    base = carregar_base()
    ym = ym or cnes_competencia_mais_recente()
    caminho = baixar_cnes(ym)
    UF_COD = {'11':'RO','12':'AC','13':'AM','14':'RR','15':'PA','16':'AP','17':'TO','21':'MA',
              '22':'PI','23':'CE','24':'RN','25':'PB','26':'PE','27':'AL','28':'SE','29':'BA',
              '31':'MG','32':'ES','33':'RJ','35':'SP','41':'PR','42':'SC','43':'RS','50':'MS',
              '51':'MT','52':'GO','53':'DF'}
    NOME_UF = {'São Paulo':'SP','Rio de Janeiro':'RJ','Minas Gerais':'MG','Brasília':'DF',
               'Distrito Federal':'DF','Bahia':'BA','Pernambuco':'PE','Ceará':'CE','Paraná':'PR',
               'Goiás':'GO','Amazonas':'AM','Rio Grande do Sul':'RS','Santa Catarina':'SC',
               'Espírito Santo':'ES','Pará':'PA','Maranhão':'MA','Paraíba':'PB',
               'Rio Grande do Norte':'RN','Alagoas':'AL','Sergipe':'SE','Piauí':'PI',
               'Mato Grosso':'MT','Mato Grosso do Sul':'MS','Tocantins':'TO','Rondônia':'RO'}

    # natureza jurídica: 1xxx e 2xxx = privado (2 = empresas, 3 = sem fins lucrativos)
    with zipfile.ZipFile(caminho) as z:
        te = _membro(z, r'tbEstabelecimento')
        g = _csv_cnes(z, te); cab = next(g)
        i_un = _coluna(cab, r'CO_UNIDADE', r'CO_CNES')
        i_tp = _coluna(cab, r'TP_UNIDADE', r'CO_TIPO_UNIDADE')
        i_nat = _coluna(cab, r'CO_NATUREZA_JUR', r'NATUREZA_JURIDICA', r'CO_NATUREZA_ORGANIZACAO')
        i_esf = _coluna(cab, r'CO_ESFERA_ADMIN', r'TP_GESTAO')
        atrib = {}
        for l in g:
            if i_un is None or i_un >= len(l): continue
            u = l[i_un].strip().strip('"')
            atrib[u] = {
              'tp': (l[i_tp].strip().strip('"') if i_tp is not None and i_tp < len(l) else ''),
              'nat': (l[i_nat].strip().strip('"') if i_nat is not None and i_nat < len(l) else ''),
              'esf': (l[i_esf].strip().strip('"') if i_esf is not None and i_esf < len(l) else ''),
            }
        if verboso:
            print(f'   tbEstabelecimento: {len(atrib)} unidades · colunas usadas: '
                  f'tipo={cab[i_tp] if i_tp is not None else "?"}, '
                  f'natureza={cab[i_nat] if i_nat is not None else "?"}, '
                  f'esfera={cab[i_esf] if i_esf is not None else "?"}')

    leitos = ler_leitos(caminho, verboso=False)
    por_un = leitos['por_unidade']
    # tipos de unidade que são hospital (05 geral, 07 especializado, 62 dia, 36 clínica)
    HOSP = {'05', '07', '62', '36', '5', '7'}
    recortes = {
      'total':            lambda u, d: d['total'],
      'nao_sus':          lambda u, d: d['nao_sus'],
      'hospital_total':   lambda u, d: d['total'] if atrib.get(u, {}).get('tp') in HOSP else 0,
      'hospital_nao_sus': lambda u, d: d['nao_sus'] if atrib.get(u, {}).get('tp') in HOSP else 0,
      'privado_nao_sus':  lambda u, d: d['nao_sus'] if atrib.get(u, {}).get('nat', '').startswith(('2', '3')) else 0,
      'hosp_priv_nao_sus':lambda u, d: d['nao_sus'] if (atrib.get(u, {}).get('tp') in HOSP and
                                                        atrib.get(u, {}).get('nat', '').startswith(('2', '3'))) else 0,
    }
    por_uf = {nome: {uf: 0 for uf in UF_COD.values()} for nome in recortes}
    for u, d in por_un.items():
        uf = UF_COD.get(u[:2])
        if not uf: continue
        for nome, fn in recortes.items():
            por_uf[nome][uf] += fn(u, d)

    bs = base['hosp']['by_state']; ref = bs['periods'][-1]
    alvo = {}
    for r in bs['rows']:
        if r.get('level') != 'state': continue
        uf = NOME_UF.get(r['name']); v = (r.get('beds') or {}).get(ref)
        if uf and v: alvo[uf] = v
    resumo = []
    for nome in recortes:
        erros = [abs(por_uf[nome][uf] / v - 1) for uf, v in alvo.items() if por_uf[nome].get(uf)]
        if not erros: continue
        resumo.append({'recorte': nome, 'erro_medio': round(sum(erros)/len(erros), 4),
                       'sp_calculado': por_uf[nome].get('SP'), 'sp_base': alvo.get('SP')})
    resumo.sort(key=lambda x: x['erro_medio'])
    if verboso:
        print(f'\n   Recortes candidatos contra {len(alvo)} UFs da base ({ref})')
        print('   ' + '-' * 62)
        for r in resumo:
            print(f'   {r["recorte"]:20} erro médio {r["erro_medio"]:7.1%}   '
                  f'SP {r["sp_calculado"]:>8,} vs {r["sp_base"]:,}')
        print('   (procuro o recorte com erro perto de zero; sem isso não ligo a aba)')
    saida = {'competencia': rotulo(ym), 'referencia': ref, 'resumo': resumo,
             'por_uf': {n: por_uf[n] for n in list(recortes)}, 'alvo': alvo}
    json.dump(saida, open(os.path.join(HERE, 'diagnostico_leitos_uf.json'), 'w',
                          encoding='utf-8'), ensure_ascii=False, indent=1)
    print('   diagnostico_leitos_uf.json gravado')
    return saida



# ======================================================================
# NIP e IGR — demandas dos consumidores na ANS
# ======================================================================

"""
Duas bases distintas alimentam a aba de reclamações:

  NIP  — microdados: uma linha por demanda aberta, com ano e mês de referência,
         nome da operadora e assunto. As séries do dashboard são contagens
         dessas linhas. O recorte "ex. Reimbursement" exclui as demandas cujo
         assunto é reembolso.
  IGR  — Índice Geral de Reclamações, já calculado pela ANS: um CSV por ano,
         uma linha por operadora e uma coluna por mês.

O agrupamento aqui é diferente do usado em beneficiários: nesta aba o BBI abre
Clinipam, São Lucas, Medisanitas e CCG como linhas próprias, e "HAPV" é a soma
de todas as empresas do grupo Hapvida. Conferi a definição na própria base:
em jun/26, HAPV = 5.005 = Hapvida 2.153 + NDI 2.358 + Clinipam 159 + São Lucas
41 + Medisanitas 197 + CCG 84 + H.B. Saúde 13 + Bio Saúde 0.
"""

NIP_DIR = 'https://dadosabertos.ans.gov.br/FTP/PDA/demandas_dos_consumidores_nip/'
IGR_DIR = 'https://dadosabertos.ans.gov.br/FTP/PDA/IGR/'

# Ordem importa: o primeiro padrão que casar define a operadora.
GRUPOS_NIP = [
 ('Clinipam',        r'clinipam'),
 ('São Lucas',       r'sao lucas saude|hospital sao lucas'),
 ('Medisanitas',     r'medisanitas|sanitas'),
 ('CCG',             r'\bccg\b|ccg saude'),
 ('Bio Saúde',       r'bio ?saude'),
 ('H.B. Saúde',      r'h\.? ?b\.? saude|hb saude'),
 ('NDI',             r'notre ?dame|interm[e_]dica|\bgndi\b|\bndi\b'),
 ('Hapvida',         r'\bhapvida\b'),
 ('Amil',            r'\bamil\b|medial|next saude|esho'),
 ('Bradesco Saúde',  r'\bbradesco\b|mediservice'),
 ('SulAmérica',      r'sul ?america'),
 ('Athena Saúde',    r'\bathena\b'),
 ('Porto Seguro',    r'porto ?seguro|portomed'),
 ('Unimed Nacional', r'unimed cnu|central nacional unimed|unimed (do brasil|nacional)'),
 ('Unimed Seguros',  r'unimed seguros'),
 ('Unimed BH',       r'unimed b\.?h\b|unimed belo horizonte'),
 ('Unimed Ferj',     r'unimed do est.*\brj\b.*federacao|federacao.*coop.*medicas?.*\brj\b'),
 ('Unimed Rio',      r'unimed[- ]rio\b(?!\s*(branco|verde|preto|claro|grande|negro|doce|pardo))'),
]
# HAPV é a soma de todo o grupo Hapvida, incluindo as adquiridas
HAPV_PARTES = ['Hapvida', 'NDI', 'Clinipam', 'São Lucas', 'Medisanitas',
               'CCG', 'H.B. Saúde', 'Bio Saúde']
NIP_REEMBOLSO = r'reembolso'


def classificar_nip(nome):
    n = _norm(nome).replace('_', ' ')
    for rot, pad in GRUPOS_NIP:
        if re.search(pad, n): return rot
    return 'Others'


def baixar_nip(ano):
    nome = f'pda-013-demandas_dos_consumidores_nip-{ano}.csv'
    return baixar(NIP_DIR + nome, os.path.join(CACHE, 'nip', nome))


def contar_nip(caminho, verboso=True):
    """Conta demandas por mês e operadora, em vários recortes ao mesmo tempo.

    A primeira versão contava tudo e saía 14% acima do relatório em Bradesco e
    SulAmérica — porque a base do NIP mistura planos médicos e odontológicos, e
    a aba do dashboard é médico-hospitalar. Em vez de eu escolher o filtro no
    escuro, aqui saem todos os recortes plausíveis de uma passada só; quem
    decide é a conferência contra a base.
    """
    enc = 'utf-8'
    with open(caminho, 'rb') as fh:
        amostra = fh.read(1 << 16)
    try: amostra.decode('utf-8')
    except UnicodeDecodeError: enc = 'latin-1'

    # A primeira tentativa filtrou por CLASSIFICACAO_DA_NIP, que na verdade é o
    # status da demanda ("inativa", "em andamento"). O corte assistencial está
    # em NATUREZA_DA_NIP — e boa parte das linhas vem com esse campo vazio.
    recortes = ['tudo', 'sem_odonto', 'assistencial', 'assist_ou_vazio',
                'sem_odonto+assist_ou_vazio']
    total = {r: defaultdict(lambda: defaultdict(float)) for r in recortes}
    sem_reemb = {r: defaultdict(lambda: defaultdict(float)) for r in recortes}
    valores = {'cobertura': defaultdict(int), 'classificacao': defaultdict(int),
               'natureza': defaultdict(int)}
    # quem exatamente está sendo somado em cada operadora do dashboard: é aqui
    # que se vê que "Bradesco Saúde" está engolindo a Mediservice e a Dental
    quem = defaultdict(lambda: defaultdict(int))

    with open(caminho, encoding=enc, errors='replace', newline='') as fh:
        leitor = csv.reader(fh, delimiter=';')
        cab = [c.strip().strip('"').upper() for c in next(leitor, [])]
        def col(*p):
            for x in p:
                for i, c in enumerate(cab):
                    if re.fullmatch(x, c): return i
            return None
        i_ano = col(r'ANO_DE_REFERENCIA'); i_mes = col(r'MES_DE_REFERENCIA')
        i_op = col(r'NOME_OPERADORA'); i_as = col(r'ASSUNTO')
        i_cob = col(r'TIPOS_DE_COBER_CONTRATADAS', r'.*COBER.*')
        i_cla = col(r'CLASSIFICACAO_DA_NIP'); i_nat = col(r'NATUREZA_DA_NIP')
        if verboso:
            print(f'   colunas: ano={cab[i_ano] if i_ano is not None else "?"}, '
                  f'mes={cab[i_mes] if i_mes is not None else "?"}, '
                  f'operadora={cab[i_op] if i_op is not None else "?"}, '
                  f'cobertura={cab[i_cob] if i_cob is not None else "?"}, '
                  f'classificacao={cab[i_cla] if i_cla is not None else "?"}')
        if None in (i_ano, i_mes, i_op):
            raise RuntimeError(f'não reconheci as colunas do NIP: {cab[:12]}')
        n = 0
        for l in leitor:
            if max(i_ano, i_mes, i_op) >= len(l): continue
            a = l[i_ano].strip().strip('"'); mm = l[i_mes].strip().strip('"')
            if not a.isdigit() or not mm.isdigit(): continue
            ym = f'{int(a)}{int(mm):02d}'
            g = classificar_nip(l[i_op])
            if g != 'Others':
                quem[g][l[i_op].strip().strip('"')[:60]] += 1
            cob = _norm(l[i_cob]) if i_cob is not None and i_cob < len(l) else ''
            cla = _norm(l[i_cla]) if i_cla is not None and i_cla < len(l) else ''
            nat = _norm(l[i_nat]) if i_nat is not None and i_nat < len(l) else ''
            if n < 400000:
                valores['cobertura'][cob[:40]] += 1
                valores['classificacao'][cla[:40]] += 1
                valores['natureza'][nat[:40]] += 1
            odonto = 'odonto' in cob
            assist = nat.startswith('assist')
            vazio = not nat
            quais = ['tudo']
            if not odonto: quais.append('sem_odonto')
            if assist: quais.append('assistencial')
            if assist or vazio: quais.append('assist_ou_vazio')
            if (assist or vazio) and not odonto: quais.append('sem_odonto+assist_ou_vazio')
            assunto = _norm(l[i_as]) if i_as is not None and i_as < len(l) else ''
            reembolso = bool(re.search(NIP_REEMBOLSO, assunto))
            for r in quais:
                total[r][ym][g] += 1; total[r][ym]['Market'] += 1
                if not reembolso:
                    sem_reemb[r][ym][g] += 1; sem_reemb[r][ym]['Market'] += 1
            n += 1
    if verboso:
        print(f'   {n:,} demandas em {len(total["tudo"])} meses')
        for campo, d in valores.items():
            topo = sorted(d.items(), key=lambda x: -x[1])[:4]
            print(f'   {campo}: ' + ' · '.join(f'{k or "(vazio)"}={v:,}' for k, v in topo))

    def fecha(d):
        for ym, g in d.items():
            g['HAPV'] = sum(g.get(p, 0) for p in HAPV_PARTES)
            nomeados = sum(v for k, v in g.items()
                           if k not in ('Market', 'Others', 'HAPV'))
            g['Others'] = g.get('Market', 0) - nomeados
        return {k: dict(v) for k, v in d.items()}

    diag = {k: dict(sorted(v.items(), key=lambda x: -x[1])[:10]) for k, v in valores.items()}
    diag['razoes_sociais_por_operadora'] = {
        g: dict(sorted(d.items(), key=lambda x: -x[1])[:8]) for g, d in sorted(quem.items())}
    if verboso:
        print('   razões sociais somadas em cada operadora:')
        for g, d in sorted(quem.items()):
            topo = sorted(d.items(), key=lambda x: -x[1])[:4]
            print(f'      {g:18} ' + ' · '.join(f'{k[:40]}={v}' for k, v in topo))
    return ({r: fecha(total[r]) for r in recortes},
            {r: fecha(sem_reemb[r]) for r in recortes}, diag)


def _igr_arquivos():
    """Lista o diretório do IGR e devolve {ano: url}.

    O igr_anual.zip é um arquivo histórico e para em 2022; os anos recentes
    vêm em arquivos próprios. Varro o diretório inteiro em vez de assumir.
    """
    r = requests.get(IGR_DIR, headers=CAB_ANS, timeout=(30, 120))
    r.raise_for_status()
    itens = re.findall(r'href="([^"?][^"]*)"', r.text)
    fora = {}
    for i in itens:
        if i.endswith('/'): continue
        m = re.search(r'(20\d{2})', i)
        if m and i.lower().endswith(('.csv', '.zip', '.xlsx')):
            fora.setdefault(int(m.group(1)), urljoin(IGR_DIR, i))
    return fora, itens


def baixar_igr(ano=None):
    arqs, itens = _igr_arquivos()
    if ano and ano in arqs:
        url = arqs[ano]
    else:
        url = urljoin(IGR_DIR, 'igr_anual.zip')
    nome = url.rsplit('/', 1)[-1]
    return baixar(url, os.path.join(CACHE, 'igr', nome)), arqs, itens


def _igr_de_tabela(bruto, verboso=True, rotulo_arquivo=''):
    """Lê uma tabela do IGR: uma linha por operadora, uma coluna por mês."""
    try:
        bruto.decode('utf-8'); enc = 'utf-8'
    except UnicodeDecodeError:
        enc = 'latin-1'
    leitor = csv.reader(io.StringIO(bruto.decode(enc, 'replace')), delimiter=';')
    cab = [c.strip().strip('"') for c in next(leitor, [])]
    i_op = next((i for i, c in enumerate(cab) if re.search(r'raz[aã]o social|operadora', c, re.I)), 0)
    MES3 = {'jan':1,'fev':2,'mar':3,'abr':4,'mai':5,'jun':6,
            'jul':7,'ago':8,'set':9,'out':10,'nov':11,'dez':12}
    meses = {}
    for i, c in enumerate(cab):
        mm = re.fullmatch(r'([A-Za-z]{3})[/\-](\d{2,4})', c.strip())
        if mm and mm.group(1).lower() in MES3:
            ano = mm.group(2)
            ano = ano if len(ano) == 4 else '20' + ano
            meses[i] = f'{ano}{MES3[mm.group(1).lower()]:02d}'
    if verboso:
        print(f'   {rotulo_arquivo}: {len(meses)} colunas de mês, coluna de operadora "{cab[i_op][:34]}"')
    acum = defaultdict(lambda: defaultdict(list))
    for l in leitor:
        if i_op >= len(l): continue
        g = classificar_nip(l[i_op])
        if g == 'Others': continue
        for i, ym in meses.items():
            if i >= len(l): continue
            v = l[i].strip().strip('"').replace('.', '').replace(',', '.')
            try: x = float(v)
            except ValueError: continue
            if x > 0: acum[ym][g].append(x)
    fora = {}
    for ym, d in acum.items():
        fora[ym] = {g: round(sum(vs) / len(vs), 6) for g, vs in d.items()}
    return fora


def ler_igr(caminho, verboso=True):
    """IGR por mês e operadora, seja o arquivo um CSV solto ou um ZIP anual."""
    fora = {}
    if caminho.lower().endswith('.zip'):
        with zipfile.ZipFile(caminho) as z:
            for nome in z.namelist():
                if not nome.lower().endswith('.csv'): continue
                try:
                    fora.update(_igr_de_tabela(z.read(nome), verboso, os.path.basename(nome)))
                except Exception as e:
                    print(f'   {nome}: {e}')
    else:
        with open(caminho, 'rb') as fh:
            fora.update(_igr_de_tabela(fh.read(), verboso, os.path.basename(caminho)))
    return fora


def conferir_nip(D, total, sem_reemb, igr, ultimo, tolerancia=0.02):
    """O que eu contei reproduz o mês que a base já tem?"""
    rel = {}
    pares = (('NIPs', total), ('NIPs (ex. Reimbursement)', sem_reemb), ('IGR', igr))
    for nome, dados in pares:
        bloco = D['nip'].get(nome)
        if not bloco: continue
        comuns = [p for p in bloco['periods'] if _rotulo_para_ym(p) in dados]
        if not comuns:
            rel[nome] = {'aprovado': False, 'motivo': 'nenhum mês em comum'}; continue
        p = comuns[-1]; ym = _rotulo_para_ym(p); i = bloco['periods'].index(p)
        linhas, ok = [], 0
        for op, vals in bloco['series'].items():
            b = vals[i] if i < len(vals) else None
            c = dados[ym].get(op)
            if b is None or c is None: continue
            d = (c - b) / b if b else (0 if not c else 1)
            if abs(d) <= tolerancia: ok += 1
            linhas.append({'operadora': op, 'base': b, 'calculado': round(c, 4),
                           'dif': round(d, 4)})
        rel[nome] = {'periodo': p, 'operadoras': len(linhas), 'dentro_da_tolerancia': ok,
                     'aprovado': bool(linhas) and ok >= max(1, int(len(linhas) * 0.75)),
                     'detalhe': sorted(linhas, key=lambda x: -abs(x['dif']))[:8]}
    return rel


def merge_nip(D, total, sem_reemb, igr, verboso=True, tolerancia=0.02):
    """Acrescenta os meses novos de NIP e IGR. Não reescreve o que já existe."""
    ultimo = max(total) if total else None
    rel = conferir_nip(D, total, sem_reemb, igr, ultimo, tolerancia)
    if verboso:
        print('\n   Conferência contra a base')
        print('   ' + '-' * 66)
        for nome, r in rel.items():
            if 'motivo' in r:
                print(f'   {nome:26} {r["motivo"]}'); continue
            print(f'   {nome:26} {r["dentro_da_tolerancia"]}/{r["operadoras"]} operadoras '
                  f'em {r["periodo"]} — {"OK" if r["aprovado"] else "NÃO CONFERE"}')
            for l in r['detalhe'][:3]:
                if abs(l['dif']) > tolerancia:
                    print(f'   {"":26}   {l["operadora"]:18} base {l["base"]:>9,.1f} '
                          f'calc {l["calculado"]:>9,.1f} ({l["dif"]:+.1%})')
    ruins = [n for n, r in rel.items() if not r.get('aprovado')]
    if ruins:
        print(f'\n   NÃO gravei: {", ".join(ruins)} não reproduz a base.')
        return {'gravado': False, 'relatorio': rel}

    blocos = {'NIPs': total, 'NIPs (ex. Reimbursement)': sem_reemb, 'IGR': igr}
    ref = D['nip']['NIPs']
    ja = {_rotulo_para_ym(p) for p in ref['periods'] if _rotulo_para_ym(p)}
    novos = sorted(ym for ym in total if ym not in ja)
    if not novos:
        if verboso: print(f'\n   Nada novo: a base já vai até {ref["periods"][-1]}.')
        return {'gravado': False, 'relatorio': rel, 'em_dia': True}

    def escreve(bloco, periodo, valores):
        i = len(bloco['periods']); bloco['periods'].append(periodo)
        for nome, vals in bloco['series'].items():
            while len(vals) < i: vals.append(None)
            vals.append(valores.get(nome))

    for ym in novos:
        p = _ym_para_rotulo(ym)
        for nome, dados in blocos.items():
            if nome not in D['nip']: continue
            escreve(D['nip'][nome], p, {k: round(v, 6) for k, v in dados.get(ym, {}).items()})
        # IGR ex-reembolso = IGR escalado pela fração de demandas que não são reembolso
        if 'IGR (ex. Reimbursement)' in D['nip']:
            v = {}
            for op, x in igr.get(ym, {}).items():
                t, sr = total.get(ym, {}).get(op), sem_reemb.get(ym, {}).get(op)
                if t: v[op] = round(x * sr / t, 6)
            escreve(D['nip']['IGR (ex. Reimbursement)'], p, v)
        # crescimento do IGR contra o mesmo mês do ano anterior
        if 'IGR Growth YoY' in D['nip']:
            ant = igr.get(f'{int(ym[:4])-1}{ym[4:]}', {})
            v = {op: round(x / ant[op] - 1, 6) for op, x in igr.get(ym, {}).items()
                 if ant.get(op)}
            escreve(D['nip']['IGR Growth YoY'], p, v)

    if verboso:
        print(f'\n   {len(novos)} mês(es) acrescentado(s): '
              f'{", ".join(_ym_para_rotulo(y) for y in novos)}')
        for ym in novos:
            print(f'      {_ym_para_rotulo(ym)}: {total[ym].get("Market", 0):,.0f} demandas no mercado')
    D.setdefault('meta', {})['vintage_nip'] = _ym_para_rotulo(max(novos))
    return {'gravado': True, 'relatorio': rel,
            'meses': [_ym_para_rotulo(y) for y in novos]}


def escolher_recorte(D, totais, verboso=True, tolerancia=0.02):
    """Qual recorte do NIP reproduz melhor o último mês que a base já tem."""
    bloco = D['nip']['NIPs']
    placar = []
    for nome, dados in totais.items():
        comuns = [p for p in bloco['periods'] if _rotulo_para_ym(p) in dados]
        if not comuns:
            placar.append({'recorte': nome, 'sem_mes_comum': True}); continue
        p = comuns[-1]; ym = _rotulo_para_ym(p); i = bloco['periods'].index(p)
        ok = n = 0; pior = None
        for op, vals in bloco['series'].items():
            b = vals[i] if i < len(vals) else None
            c = dados[ym].get(op)
            if b is None or c is None or not b: continue
            d = (c - b) / b; n += 1
            if abs(d) <= tolerancia: ok += 1
            if pior is None or abs(d) > abs(pior[1]): pior = (op, d)
        placar.append({'recorte': nome, 'periodo': p, 'operadoras': n, 'dentro': ok,
                       'acerto': round(ok / n, 3) if n else 0,
                       'pior': {'operadora': pior[0], 'dif': round(pior[1], 4)} if pior else None})
    placar.sort(key=lambda x: -x.get('acerto', 0))
    if verboso:
        print('\n   Recortes testados contra a base')
        print('   ' + '-' * 62)
        for r in placar:
            if r.get('sem_mes_comum'):
                print(f'   {r["recorte"]:20} sem mês em comum'); continue
            pr = r.get('pior') or {}
            print(f'   {r["recorte"]:20} {r["dentro"]:>3}/{r["operadoras"]} operadoras '
                  f'({r["acerto"]:.0%})   pior: {pr.get("operadora","-"):16} {pr.get("dif",0):+.1%}')
    return placar


def acao_nip(ano=None, verboso=True):
    """Atualiza NIP e IGR a partir dos microdados e do índice publicados pela ANS."""
    print('\n  Reclamações — NIP e IGR da ANS')
    base = carregar_base()
    ref = base['nip']['NIPs']['periods'][-1]
    ano = int(ano or (2000 + int(ref[-2:])))
    hoje_ano = datetime.date.today().year
    anos = sorted({ano, hoje_ano})

    totais, sem_reembs, valores = {}, {}, {}
    for a in anos:
        try:
            print(f'        microdados de {a}')
            t, s_, v = contar_nip(baixar_nip(a), verboso)
            for r in t:
                totais.setdefault(r, {}).update(t[r])
                sem_reembs.setdefault(r, {}).update(s_[r])
            valores = v
        except Exception as e:
            print(f'        NIP {a}: {e}')
    if not totais:
        print('        nada foi lido; a série fica onde está'); return None

    placar = escolher_recorte(base, totais, verboso)
    melhor = placar[0] if placar else None
    diag = {'placar': placar, 'valores_das_colunas': valores}
    if not melhor or melhor.get('acerto', 0) < 0.75:
        print('\n   Nenhum recorte reproduz a base o bastante. Não gravei nada.')
        print('   O diagnóstico traz os valores reais das colunas de cobertura e')
        print('   classificação — é por eles que se descobre o filtro que falta.')
        json.dump(diag, open(os.path.join(HERE, 'diagnostico_nip.json'), 'w',
                             encoding='utf-8'), ensure_ascii=False, indent=1)
        return {'gravado': False, 'placar': placar}
    r = melhor['recorte']
    print(f'\n   Recorte escolhido: {r} ({melhor["acerto"]:.0%} de acerto)')
    total, sem_reemb = totais[r], sem_reembs[r]

    igr = {}
    try:
        alvo_ano = int(max(total)[:4])
        for a in sorted({alvo_ano, alvo_ano - 1}):
            try:
                caminho, arqs, itens = baixar_igr(a)
                igr.update(ler_igr(caminho, verboso))
            except Exception as e:
                print(f'        IGR {a}: {e}')
        if igr:
            print(f'        IGR: {len(igr)} meses, até {_ym_para_rotulo(max(igr))}')
    except Exception as e:
        print(f'        IGR: {e}')

    res = merge_nip(base, total, sem_reemb, igr, verboso)
    diag['recorte_usado'] = r
    diag['conferencia'] = res.get('relatorio')
    diag['ultimo_mes_nip'] = _ym_para_rotulo(max(total))
    diag['ultimo_mes_igr'] = _ym_para_rotulo(max(igr)) if igr else None
    json.dump(diag, open(os.path.join(HERE, 'diagnostico_nip.json'), 'w',
                         encoding='utf-8'), ensure_ascii=False, indent=1)
    if res.get('gravado'):
        gravar_base(base)
    return res


def embutir_base(caminho_html=None):
    """Reescreve a base embutida no HTML com o dados.json atual.

    O dashboard busca a base publicada ao abrir, mas guarda uma cópia dentro do
    próprio HTML para funcionar sem rede. Se essa cópia envelhece, quem abrir o
    arquivo offline vê números velhos sem perceber. Aqui ela é reescrita a cada
    rodada, e o arquivo continua sendo um HTML só, sem dependência nenhuma.
    """
    if not os.path.exists(DADOS):
        print('  sem dados.json — nada a embutir'); return None
    htmls = [caminho_html] if caminho_html else [
        os.path.join(HERE, f) for f in sorted(os.listdir(HERE)) if f.endswith('.html')]
    dados = open(DADOS, encoding='utf-8').read()
    tocados = []
    for h in htmls:
        if not h or not os.path.exists(h): continue
        txt = open(h, encoding='utf-8').read()
        novo, n = re.subn(r'(?s)(let DATA = )\{.*?\}(;\nconst SOURCES)',
                          lambda m: m.group(1) + dados + m.group(2), txt, count=1)
        if not n:
            print(f'  {os.path.basename(h)}: não achei a base embutida, deixei como está')
            continue
        if novo != txt:
            open(h, 'w', encoding='utf-8').write(novo)
            tocados.append(os.path.basename(h))
    if tocados:
        print(f'  base embutida atualizada em: {", ".join(tocados)}')
    else:
        print('  base embutida já estava igual ao dados.json')
    return tocados


def main() -> None:
    ap = argparse.ArgumentParser(description='Atualiza a base do Healthcare Database Dashboard.')
    ap.add_argument('--verificar', action='store_true', help='apenas lista o que há de novo')
    ap.add_argument('--atualizar', action='store_true', help='baixa as competências novas e regrava dados.json')
    ap.add_argument('--fonte', help='id de uma fonte específica (ex.: ans_rpc)')
    ap.add_argument('--desde', help='competência inicial AAAAMM para coletas em lote')
    ap.add_argument('--listar-fontes', action='store_true', help='mostra o catálogo de fontes')
    ap.add_argument('--beneficiarios', metavar='AAAAMM',
                    help='baixa e incorpora uma competência do PDA-024 (séries por operadora, UF, contratação e faixa etária)')
    ap.add_argument('--uf', nargs='*', help='limita a coleta a algumas UFs (teste rápido)')
    ap.add_argument('--conferir', action='store_true',
                    help='com --beneficiarios: processa e imprime o agrupamento sem gravar')
    ap.add_argument('--cache', help='pasta com os ZIPs já baixados (pula o download)')
    ap.add_argument('--auto', action='store_true',
                    help='faz tudo sozinho: descobre a competência nova, baixa, processa e atualiza o IPCA')
    ap.add_argument('--cnes', nargs='?', const=True, metavar='AAAAMM',
                    help='atualiza os leitos hospitalares pela base do CNES')
    ap.add_argument('--cnes-descobrir', action='store_true',
                    help='refaz o casamento unidade->CNES e regrava cnes_map.json')
    ap.add_argument('--cnj', action='store_true',
                    help='atualiza a judicialização pelo painel de saúde do CNJ')
    a = ap.parse_args()

    if a.listar_fontes:
        for fid, f in FONTES.items():
            print(f'{fid:24} {f["nome"]}')
            print(f'{"":24} {f["dir"]}')
            print(f'{"":24} alimenta: {", ".join(f["alimenta"])}\n')
        return

    alvos = [a.fonte] if a.fonte else list(FONTES)
    for t in alvos:
        if t not in FONTES:
            raise SystemExit(f'Fonte desconhecida: {t}. Use --listar-fontes.')

    if a.auto:
        acao_auto(); return
    if a.cnes or a.cnes_descobrir:
        acao_cnes(a.cnes if isinstance(a.cnes, str) else None, descobrir=a.cnes_descobrir); return
    if a.cnj:
        acao_cnj(); return
    if a.beneficiarios:
        if not re.fullmatch(r'\d{6}', a.beneficiarios):
            raise SystemExit('Use o formato AAAAMM, por exemplo: --beneficiarios 202606')
        acao_beneficiarios(a.beneficiarios, a.uf, a.conferir, a.cache)
    elif a.atualizar:
        acao_atualizar(alvos, a.desde)
    else:
        acao_verificar(alvos)

# ======================================================================
# PONTO DE ENTRADA
# ======================================================================
def _cli():
    import argparse
    ap = argparse.ArgumentParser(description='Atualiza o Healthcare Database Dashboard.')
    ap.add_argument('--ipca', action='store_true', help='só a camada de inflação (IBGE)')
    ap.add_argument('--conferir', action='store_true', help='processa a ANS e mostra, sem gravar')
    ap.add_argument('--so-baixar', action='store_true', help='só baixa os ZIPs da ANS')
    ap.add_argument('--competencia', help='força uma competência AAAAMM')
    ap.add_argument('--cache', help='pasta com ZIPs já baixados')
    ap.add_argument('--cnes', nargs='?', const=True, metavar='AAAAMM',
                    help='só os leitos hospitalares (CNES/DATASUS)')
    ap.add_argument('--cnes-descobrir', action='store_true',
                    help='refaz o casamento unidade->CNES e regrava cnes_map.json')
    ap.add_argument('--cnj', action='store_true',
                    help='atualiza a judicialização pelo painel de saúde do CNJ')
    ap.add_argument('--auto', action='store_true',
                    help='rodada completa: IPCA, beneficiários da ANS, leitos do CNES e '
                         'judicialização do CNJ (é também o padrão, sem nenhuma opção)')
    ap.add_argument('--embutir', action='store_true',
                    help='reescreve a cópia da base dentro do HTML a partir do dados.json')
    ap.add_argument('--nip', action='store_true',
                    help='atualiza reclamações (NIP) e IGR')
    ap.add_argument('--explorar', action='store_true',
                    help='mapeia o schema das bases da ANS que ainda não têm coletor')
    ap.add_argument('--leitos-uf', action='store_true',
                    help='testa recortes do CNES contra os leitos por UF da base')
    ap.add_argument('--refazer', metavar='AAAAMM',
                    help='reprocessa uma competência da ANS mesmo que a base já esteja nela '
                         '(usado para reescrever por encadeamento o que entrou só por nível)')
    a = ap.parse_args()

    if a.refazer:
        if not re.fullmatch(r'\d{6}', a.refazer):
            raise SystemExit('Use o formato AAAAMM, por exemplo: --refazer 202606')
        acao_beneficiarios(a.refazer, None, False, a.cache); return

    if a.cnes or a.cnes_descobrir:
        acao_cnes(a.cnes if isinstance(a.cnes, str) else None,
                  descobrir=a.cnes_descobrir); return
    if a.cnj:
        acao_cnj(); return

    if a.ipca:
        base = carregar_base(); ip = puxar()
        antes = (base.get('ipca') or {}).get('competencia')
        base['ipca'] = ip
        print(f"IPCA: {antes or '—'} -> {ip['competencia']}")
        gravar_base(base); return

    if a.embutir:
        embutir_base(); return

    if a.nip:
        acao_nip(); return
    if a.explorar:
        acao_explorar(); return
    if a.leitos_uf:
        calibrar_leitos_cnes(); return

    if a.auto:
        acao_auto(); return

    ym = a.competencia
    if not ym and not a.cache:
        try:
            ym = competencia_mais_recente(FONTES['ans_beneficiarios'])
        except Exception as e:
            raise SystemExit(f'Não consegui consultar a ANS: {e}')

    if a.so_baixar:
        destino = a.cache or os.path.join(CACHE, 'pda024', ym)
        baixar_competencia(ym, destino)
        print(f'\nZIPs em {destino}'); return

    if a.conferir:
        acao_beneficiarios(ym, None, True, a.cache); return

    acao_auto()


if __name__ == '__main__':
    _cli()
