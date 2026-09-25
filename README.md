# tqs2etabs

Conversor de plantas de formas do TQS (arquivos `.LDF` + `.LST` do Modelador Estrutural / TQS Formas)
para modelos analíticos do ETABS. Arquitetura, engenharia reversa dos formatos e decisões de projeto
em [ARCHITECTURE.md](ARCHITECTURE.md).

## Instalação (desenvolvimento)

```bash
pip install -e ".[dev]"
```

## Uso — Etapa 1 (parser + modelo intermediário)

```bash
tqs2etabs analyze "caminho/25 - Tipo.LDF" -v
```

O `.LST` de mesmo nome é usado automaticamente (ou informe `--lst`). Opções: `-v` lista pilares,
vigas, lajes, validação cruzada e avisos do TQS; `--json saida.json` grava o modelo intermediário;
`--config meu.toml` substitui `config/default.toml`.

## Uso — Etapa 3 (motor geométrico + auditoria)

```bash
tqs2etabs normalize "caminho/25 - Tipo.LDF" --report auditoria.txt --json modelo.json
```

Executa normalização (clustering 5 mm + arredondamento global a 1 cm), alinhamento viga × pilar
(ajuste transversal 10 mm; extensão da extremidade até a linha média da parede, máx. 35 cm), fusão de
nós, grids pelos eixos dos pilares e validação; imprime o relatório de auditoria com cada alteração
(BEFORE / AFTER / REASON), a comparação TQS × normalizado e os avisos do TQS cruzados com as correções.
`-v` inclui os clusters e os 252 registros de arredondamento. Tolerâncias em `config/default.toml`.

## Uso — Etapa 4 (arquivo .e2k para o ETABS)

```bash
tqs2etabs export "caminho/25 - Tipo.LDF" -o "25 - Tipo.e2k" --report relatorio.txt
```

Roda `normalize`, mapeia para o ETABS (paredes = shells por linha média com pier, pilares-frame quando
L/B ≤ 3, vigas por trecho, lajes shell-thin, escada membrane, grids, restrições na base, kN·m), grava o
`.e2k` e **relê o arquivo** conferindo pontos, conectividade, seções e grids contra o modelo normalizado.
No ETABS: *File → Import → ETABS .e2k Text File*. Detalhes do formato e itens a conferir na primeira
importação em [docs/E2K_FORMAT.md](docs/E2K_FORMAT.md). O separador decimal segue o Windows (vírgula em
pt-BR); mude `etabs.decimal_separator` no TOML se necessário.

## Uso — edifício completo (pasta do TQS)

```bash
tqs2etabs building "C:/Modelos TQS/MEU_EDIFICIO" -o MEU_EDIFICIO.e2k --report relatorio.txt
```

Varre a pasta do edifício (uma subpasta por planta), lê os `.LDF`/`.LST` de cada planta (a tabela
"Definição de Pisos" dá a replicação do tipo, cotas e pé-direitos), o fck por piso/elemento em
`ESPACIAL/RESEST2.TXT` e o E do projeto em `CONCRETO.DAT`; roda o motor geométrico por planta (com os
eixos dos pilares alinhados entre pavimentos) e gera um único `.e2k` com todos os stories (`SIMILARTO`
para pisos repetidos, material por story, piers com o nome TQS). Detalhes em [docs/BUILDING_FILES.md](docs/BUILDING_FILES.md).

## Template .e2k do escritório

```bash
tqs2etabs building "C:/Modelos TQS/MEU_EDIFICIO" -o MEU_EDIFICIO.e2k --template MODELO-REFERENCIA.e2k
```

Reaproveita de um modelo ETABS existente (o mesmo `--template` vale para `export`):

- **definições**: materiais (com o E do projeto), seções de barra/laje/parede **com os modificadores de
  rigidez**, diafragmas, funções, conjuntos de carga de shell — o template vence quando o nome coincide
  (exceção: material cujo E o usuário escolheu na aba Materiais);
- **configuração**: opções de análise (P-Delta, malha), mass source e preferências de dimensionamento;
- **casos e combinações**: `LOAD PATTERNS` (merge), `LOAD CASES`, `LOAD COMBINATIONS` e os **nomes** dos
  grupos (sem os membros, que pertencem ao modelo de origem).

As combinações do template **existem mesmo sem a carga correspondente**: um load pattern citado por um caso
mas não definido no template é criado vazio (tipo deduzido do nome: `Wind` para `Z-WT-…`/`W50YRP`, `Seismic`
para `EQ…`, senão `Other`), de modo que os casos e combinações de túnel de vento já ficam prontos para você
lançar as cargas depois. Aceita `.e2k` e `.$et`.

As cargas do TQS entram nos padrões do escritório: **carga permanente → `SDL`** e **carga acidental de uso →
`RLIVE`** (`pattern_dead_extra` / `pattern_live` em `[etabs]`).

A geometria (pontos, barras, áreas, stories, grids, piers, cargas) vem sempre do TQS. Como o template foi
gravado para outro edifício, o que referencia objetos inexistentes é corrigido ou descartado com aviso:
casos de **construção sequencial** têm os estágios refeitos sobre os pavimentos deste modelo (um por
pavimento, de baixo para cima); caso que usa load pattern inexistente sai, e combinação que perde suas
referências sai junto (resolvido por ponto fixo).

Outras duas regras de posicionamento: o modelo é transladado para que **(0,0) seja o canto inferior
esquerdo** do perímetro, e o **primeiro/último eixo de cada direção cobre todo o perímetro** (inclusive
ponta de viga e bordo de laje) — ambas desligáveis em `[etabs]` (`origin_at_min_corner`, `bounding_grids`).

## Interface web (Streamlit)

```bash
pip install -r requirements.txt
streamlit run app/streamlit_app.py
```

Gerenciador de importação: aponte a pasta do edifício TQS (ou envie um `.zip` dela), revise/edite os
pavimentos (cota, pé-direito, planta, fck), adicione pavimentos replicando uma planta, ajuste as regras
de modelagem e gere o `.e2k` + relatório de auditoria, com o desenho de cada planta normalizada.

- **O que importar** (barra lateral): pilares/paredes, vigas, lajes e cargas de uso — o que estiver
  desmarcado não vai para o E2K (`EtabsOptions.include_columns/include_beams/include_slabs/export_loads`).
- **Pavimentos**: coluna *Importar* por piso; um piso desmarcado é pulado e o story acima passa a ter a
  altura entre as cotas restantes.
- **Materiais**: por classe, E *atual* (CONCRETO.DAT do TQS), *Prudêncio* (tabela do modelo MARAMBAIA) e
  *NBR 6118* (Ecs = αi·5600·√fck); escolha a fonte ou um valor *manual* (`EtabsOptions.e_overrides`).

Repositório: https://github.com/gabrieldepaulapereira/TQStoETABS

**Deploy no Streamlit Community Cloud** (igual ao app de pilares): repositório no GitHub (pode ser
privado) → share.streamlit.io → *New app* → arquivo principal `app/streamlit_app.py`, Python 3.12+.
`requirements.txt` da raiz já lista `streamlit`, `pandas` e `plotly`; o pacote `tqs2etabs` é carregado
de `src/` pelo próprio app.

**Origem do edifício** (barra lateral):
- *Escolher pasta no navegador* — seletor de pastas do próprio navegador (`app/components/folder_picker`,
  componente sem build): o JS reproduz a seleção do `scan_building` — `<planta>.LDF/.LST` de cada pasta de
  planta (LDF com o nome da pasta, senão todos; LST correspondente, senão os não-MENAVI), `CONCRETO.DAT` e
  `ESPACIAL/RESEST2.TXT` — compacta e envia só isso, com barra de progresso (leitura → compactação → envio →
  confirmação do servidor) e a lista dos arquivos recebidos. Funciona local e na nuvem.
- As etapas *Varrer edifício* e *Gerar E2K* têm barra de progresso por planta/etapa (callback `progress`
  em `scan_building` e `convert_building_definition`).
- *Pasta local (caminho)* + botão 📂 (diálogo nativo do Windows) — **só quando o app roda na sua máquina**.
  Na nuvem o servidor é Linux e não enxerga o seu disco, por isso a opção some e o app avisa.
- *Enviar .zip da pasta*.

## Testes

```bash
python -m pytest -q
```

## Estado

| Etapa | Situação |
|---|---|
| 0 — Engenharia reversa e arquitetura | concluída |
| 1 — Parser LDF/LST + modelo intermediário + CLI `analyze` | concluída |
| 2 — Importador DXF | fora do escopo (decisão 18.1) |
| 3 — Geometry Engine (normalização, alinhamento, conectividade, grids, validação, auditoria) | concluída |
| 4 — Escritor E2K + validação pós-exportação | concluída (falta conferir a importação no ETABS); COM pendente |
| 5 — Conversão completa (edifício multi-pavimento) | concluída: `tqs2etabs building` (TESTE: 7 pisos, 4 plantas) |
| 6 — Validação TQS × ETABS | releitura do E2K implementada; readback via COM pendente |
