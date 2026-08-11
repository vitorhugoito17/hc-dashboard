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

O QUE ELE FAZ
    1. IPCA/IBGE — API JSON pública, ciclo fechado, segundos.
    2. Beneficiários/ANS (PDA-024) — descobre a competência mais recente,
       baixa os 28 ZIPs (~390 MB), agrega e costura nas séries do dashboard.
       Operadora que não reconcilia com a base anterior dentro de 1,5% fica
       na competência antiga de propósito, em vez de gerar um degrau falso.

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
import csv, io, os, re, sys, unicodedata, zipfile
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
def baixar(ym, destino, ufs=None, requests=None):
    import requests as rq
    requests = requests or rq
    os.makedirs(destino, exist_ok=True)
    alvos = ufs or UFS
    arquivos = []
    for uf in alvos:
        nome = f'pda-024-icb-{uf}-{ym[:4]}_{ym[4:]}.zip'
        caminho = os.path.join(destino, nome)
        if os.path.exists(caminho) and os.path.getsize(caminho) > 0:
            arquivos.append(caminho); continue
        url = BASE + f'{ym}/' + nome
        print(f'      {uf} …', end='', flush=True)
        try:
            with requests.get(url, stream=True, timeout=600,
                              headers={'User-Agent':'Healthcare-Dashboard-Updater/2.0'}) as r:
                if r.status_code == 404:
                    print(' (não existe)'); continue
                r.raise_for_status()
                with open(caminho,'wb') as f:
                    for c in r.iter_content(1<<20): f.write(c)
            print(f' {os.path.getsize(caminho)/1e6:.0f} MB')
            arquivos.append(caminho)
        except Exception as e:
            print(f' ERRO: {e}')
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


def _reconcilia(D, ag, tol):
    """Decide em quais operadoras podemos confiar nesta competência.

    A base histórica vem da consolidação do BBI; o cálculo novo vem do PDA-024
    agrupado por razão social. Onde as duas metodologias batem, o ponto novo
    entra. Onde divergem além da tolerância, é diferença de escopo de grupo —
    e gravar geraria um degrau falso. Nesse caso não gravamos nada.
    """
    lv = D['ben']['lives_m']
    ult = {}
    for op, vals in lv['series'].items():
        for v in reversed(vals):
            if v is not None: ult[op] = v; break
    aceitos, recusados = set(), []
    for op, v in ag['vidas'].items():
        if op.startswith('_'): continue
        if op == 'Market': aceitos.add(op); continue
        ant = ult.get(op)
        novo = v/1000.0
        if ant in (None, 0):
            recusados.append((op, ant, novo, None)); continue
        d = (novo-ant)/ant
        if abs(d) <= tol: aceitos.add(op)
        else: recusados.append((op, ant, novo, d))
    return aceitos, recusados


def merge(D, ag, ym, verboso=True, tolerancia=0.015):
    """Acrescenta a competência ym às séries de beneficiários. Devolve o log."""
    p = rotulo(ym)
    log = []
    aceitos, recusados = _reconcilia(D, ag, tolerancia)
    if verboso:
        print(f'\n   Reconciliação com a base anterior (tolerância {tolerancia:.1%})')
        print('   ' + '-'*68)
        print(f'   {"operadora":30} {"base":>11} {"ANS jun/26":>12} {"dif":>8}')
        lv = D['ben']['lives_m']
        for op in sorted(aceitos):
            if op == 'Market': continue
            ant = next((v for v in reversed(lv['series'].get(op, [])) if v is not None), None)
            novo = ag['vidas'].get(op, 0)/1000
            d = (novo-ant)/ant*100 if ant else 0
            print(f'   OK   {op:25} {ant:11,.1f} {novo:12,.1f} {d:7.1f}%')
        for op, ant, novo, d in sorted(recusados, key=lambda x: -(abs(x[3]) if x[3] else 9)):
            a = f'{ant:11,.1f}' if ant else f'{"—":>11}'
            dd = f'{d*100:7.1f}%' if d is not None else f'{"—":>8}'
            print(f'   fora {op:25} {a} {novo:12,.1f} {dd}')
        print(f'\n   {len(aceitos)-1} operadoras incorporadas, {len(recusados)} mantidas em mai/26.')
    # filtra o agregado para o que foi aceito
    ag = dict(ag)
    ag['vidas'] = {k: v for k, v in ag['vidas'].items() if k in aceitos}
    ag['vidas_odonto'] = {k: v for k, v in ag['vidas_odonto'].items() if k in aceitos or k == 'Market'}
    ag['uf_op'] = {uf: {k: v for k, v in d.items() if k in aceitos} for uf, d in ag['uf_op'].items()}
    ag['contratacao'] = {k: v for k, v in ag['contratacao'].items() if k in aceitos}
    ag['faixa'] = {k: v for k, v in ag['faixa'].items() if k in aceitos}
    D.setdefault('meta', {})['reconciliacao'] = {
        'competencia': p, 'tolerancia': tolerancia,
        'incorporadas': sorted(x for x in aceitos if x != 'Market'),
        'retidas': sorted(x[0] for x in recusados),
    }

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
    D.setdefault('meta', {})['vintage_beneficiarios'] = p
    D['meta']['base'] = (f'beneficiários: {p} · DIOPS 1T26 · CNES jun/26 · NIP jun/26')
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
import argparse, json, os, re, sys, zipfile, io, datetime
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
   'dir': 'https://datasus.saude.gov.br/transferencia-de-arquivos/',
   'tipo': 'manual',
   'alimenta': ['hosp.units','hosp.by_state','hosp.by_city','hosp.beds_share_city'],
   'obs': 'Arquivos DBC mensais. Alternativa: TabNet de leitos ou a API de dados abertos do MS.',
 },
 'cnj': {
   'nome': 'CNJ · Novas ações de saúde suplementar',
   'dir': 'https://www.cnj.jus.br/sistemas/datajud/',
   'tipo': 'manual', 'alimenta': ['legal.lawsuits','legal.topics'],
   'obs': 'DataJud exige chave pública de acesso; os painéis publicam o agregado mensal.',
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
    print('\n  [1/2] IPCA — API do IBGE')
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
    print('\n  [2/2] Beneficiarios — PDA-024 da ANS')
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
            
            destino = os.path.join(CACHE, 'pda024', novo)
            arquivos = baixar(novo, destino)
            if arquivos:
                ag = agregar(arquivos)
                conferir(ag)
                merge(base, ag, novo)
            else:
                print('        nenhum arquivo baixado')
        except Exception as e:
            print(f'        falhou no processamento: {e}')

    gravar_base(base)
    print('\n  Pronto. Abra o dashboard — ele le o dados.json automaticamente.')
    print('='*72 + '\n')


def acao_beneficiarios(ym, ufs, apenas_conferir, cache_dir=None):
    """Baixa, parseia e incorpora uma competência do PDA-024."""
    
    destino = cache_dir or os.path.join(CACHE, 'pda024', ym)
    print(f'\nCompetência {ym} — PDA-024 (informações consolidadas de beneficiários)')
    print('='*72)
    arquivos = [f for f in (os.listdir(destino) if os.path.isdir(destino) else []) if f.endswith('.zip')]
    if arquivos:
        print(f'   {len(arquivos)} ZIPs já em cache ({destino})')
        arquivos = [os.path.join(destino, f) for f in sorted(arquivos)]
    else:
        print('   baixando (são ~360 MB no total; o cache evita rebaixar)')
        arquivos = baixar(ym, destino, ufs)
    if not arquivos:
        raise SystemExit('Nenhum arquivo baixado — verifique a conectividade e a competência.')

    print(f'\n   processando {len(arquivos)} arquivos')
    ag = agregar(arquivos)
    conferir(ag)

    if apenas_conferir:
        print('\n   modo --conferir: nada foi gravado.')
        print('   Revise o agrupamento acima; ajuste GRUPOS em py se algum')
        print('   nome de operadora tiver caído no balde errado, e rode de novo.')
        return

    base = carregar_base()
    merge(base, ag, ym)
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
    a = ap.parse_args()

    if a.ipca:
        base = carregar_base(); ip = puxar()
        antes = (base.get('ipca') or {}).get('competencia')
        base['ipca'] = ip
        print(f"IPCA: {antes or '—'} -> {ip['competencia']}")
        gravar_base(base); return

    ym = a.competencia
    if not ym and not a.cache:
        try:
            ym = competencia_mais_recente(FONTES['ans_beneficiarios'])
        except Exception as e:
            raise SystemExit(f'Não consegui consultar a ANS: {e}')

    if a.so_baixar:
        destino = a.cache or os.path.join(CACHE, 'pda024', ym)
        baixar(ym, destino)
        print(f'\nZIPs em {destino}'); return

    if a.conferir:
        acao_beneficiarios(ym, None, True, a.cache); return

    acao_auto()


if __name__ == '__main__':
    _cli()
