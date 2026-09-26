# Conversor TQS → ETABS — Engenharia reversa e arquitetura

Documento da Fase 1 (engenharia reversa + proposta de arquitetura). Nenhum código de produção foi escrito.
Todos os números citados foram verificados por script sobre os três arquivos de referência
(`25 - Tipo.LDF`, `25 - Tipo.LST`, `NORTH_TYP.DXF`), pavimento **25 - Tipo** do edifício
**NORTH TOWER - V5 10% molas** (TQS Formas 26, 19/09/2026).

Legenda de confiança usada ao longo do documento:

| Marca | Significado |
|---|---|
| **CONFIRMADO** | Interpretação validada numericamente contra outra parte do arquivo (ou contra o LST/DXF). |
| **NEEDS_REVIEW** | Hipótese plausível, com evidência parcial. Deve ser confirmada pelo usuário ou por mais arquivos TQS. |
| **UNKNOWN** | Sem evidência suficiente. Será lido e armazenado como atributo bruto, sem interpretação. |

---

## 0. Sumário executivo

1. **O LDF é a fonte geométrica completa.** Ele contém: 121 nós (cm), 22 vigas (eixo analítico por lista de nós + seção por trecho + desnível), 8 pilares (seção completa, reconstruível; polígono global para pilares genéricos; decomposição em lâminas para pilares-parede), 14 lajes (contorno por lista de nós com tipo de apoio de cada bordo + espessura + rebaixo), materiais/seções catalogadas e cargas (4 casos).
2. **O LST é necessário** por três motivos: (a) é a única fonte da cota (74,77 m) e do pé-direito (3,24 m) do pavimento — o LDF não os contém; (b) traz os avisos de processamento (os 4 "nó não cai sobre o pilar P3"); (c) traz quantitativos (comprimentos entre faces, áreas, volumes) que servem de validação cruzada do parser.
3. **O DXF não é necessário e, neste caso, é inconsistente com o LDF.** O `NORTH_TYP.DXF` representa **outro estado do modelo** (outro pavimento tipo ou outra revisão): numeração de vigas diferente (V23/V24 existem no DXF, não no LDF; DXF V11 = LDF V5, DXF V19 = LDF V6, DXF V23 = LDF V19, DXF V24 = LDF V11), seções diferentes em 9 vigas (ex.: V16 40/85 no DXF × 60/100 no LDF), pilares 10–20 cm mais finos. Os polígonos de pilar do DXF coincidem exatamente (erro 0,000000 cm) com os polígonos `PSU` do LDF, não com a seção `R`/`G` que o TQS Formas usou para processar este piso. Detalhes na seção 3.
4. **Recomendação: seguir somente com LDF + LST.** Manter na arquitetura um ponto de extensão para um importador DXF opcional (apenas como *overlay* de verificação visual), sem depender dele.
5. **Descoberta que afeta as regras de negócio:** neste pavimento, **nenhuma viga chega ao centroide de nenhum pilar**. Todos os 8 "pilares" são pilares-parede ou cortinas (P3/P6: núcleos em U de 7,71 × 2,79 m; P4/P8: cortinas de 9,21 × 0,50 m; P1/P2: 2,29 × 0,40 m, relação 5,7; P5/P7: 1,76 × 0,40 m) e as vigas apoiam nas faces/bordas, a 0–30 cm da linha média da lâmina e a 22–528 cm do centroide. A regra "alinhar viga ao eixo do pilar dentro da tolerância" (§6/§7 do pedido) precisa ser desdobrada em duas regras distintas (seção 10.3): *ajuste transversal por tolerância* (elimina ruído de 1 mm) e *extensão longitudinal até a linha média da parede* (determinística, 0–30 cm, sempre registrada). E a decisão "pilar = frame ou shell no ETABS" precisa ser tomada antes da Etapa 4 (seção 17).

---

## 1. Arquivo LDF — estrutura e gramática

### 1.1 Forma geral (CONFIRMADO)

- Texto ASCII/Latin-1, orientado a linhas, gerado pelo Modelador Estrutural. Cabeçalho em comentários com pasta, pavimento ("Pavimento 25 - Tipo   Projeto 11"), edifício, título e cliente.
- `$` inicia comentário (linha inteira ou trecho final: `DEFINE NLISTA $ Suprime...`).
- `-` no **fim da linha** = continuação na linha seguinte. Um token pode ser partido pela continuação: `... 8RV9 50 -` / `N 9AV8` produz o nó `50` com qualificador `N` em linhas separadas. O tokenizador deve juntar continuações **antes** de interpretar.
- Blocos delimitados por palavra-chave de abertura e `FIM`:

```
PROJETO 0
TSECOES ... FIM            $ seções catalogadas (perfis metálicos)
TMATERIAIS ... FIM         $ materiais não padrão
CTOR x, y                  $ ponto de referência p/ túnel de vento (solto, fora de bloco)
GEOMETRIA ... FIM          $ nós, eixos de vigas, pilares, contornos de lajes
DIMENSOES ... FIM          $ seções de vigas, seções/polígonos de pilares, espessuras de lajes
CARGAS CASO n ... FIM      $ ×4 (casos 1..4)
CAD/LAJES ... FIM | CAD/VIGAS ... FIM | GRELHA ... FIM   $ vazios neste arquivo
```

- Números: ponto decimal, até 6 casas (`-464.746213`). Pares de coordenadas separados por vírgula (`x, y` ou `x,y`); vértices de polígono separados por `;` e terminados em `;`.
- Strings entre aspas simples (`'ESCADA'`, `'C60'`, `'W 150 x 13.0'`).
- Identificadores de elemento: `V<n>`, `P<n>`, `L<n>` (lajes podem ter numeração não contígua: L1–L8 e L100–L105).

### 1.2 Cabeçalho, catálogos e materiais

| Comando | Conteúdo | Status |
|---|---|---|
| `DEFINE NLISTA` | suprime listagem no processamento | irrelevante |
| `PROJETO 0` | índice de projeto | UNKNOWN (armazenar) |
| `TSECOES 'nome' Ix Iy Iz Ax  tipo d? bf? tw? tf? ...` | catálogo de perfis metálicos (W 150×13, W 250×17,9): 4 propriedades em m⁴/m² (batem com a tabela do LST) + 9 números geométricos | propriedades CONFIRMADO; os 9 números finais UNKNOWN (não usados por nenhum elemento deste piso) |
| `TMATERIAIS 'nome' γ E G ν α fy` | γ tf/m³, E e G tf/m², ν, α 1/°C, último campo 25000 para A36 (=250 MPa → fy em tf/m²) e 0 para concreto | CONFIRMADO pela tabela do LST; último campo NEEDS_REVIEW |
| `CTOR x, y` | ponto de referência para túnel de vento (comentário diz isso) | CONFIRMADO, irrelevante para v1 |

Nenhum elemento do LDF referencia esses materiais/seções (todos os pilares são `CON` + `FCK 'C60'`). **O fck de vigas e lajes não está no LDF nem no LST** (o LST lista apenas "Pilares com fck diferenciado") — ele vem dos dados do edifício. → UNKNOWN; deve ser parâmetro de configuração.

### 1.3 Bloco GEOMETRIA

#### 1.3.1 `DEFINE ESCALA 50.00`
Escala do desenho (1:50). **Não** é fator de unidade. (CONFIRMADO: `$LTSCALE 50` no DXF e "Escala geral de desenho 50" no LST.)

#### 1.3.2 Nós — `<id> <x>, <y>` (CONFIRMADO)
121 nós, ids 1–121 contíguos, coordenadas em **cm** no sistema global da planta (prova: L4 `AREA 1362080.77` cm² = 136,21 m² no LST; P4 920,9 × 50 cm = 4,60 m² no LST).

Papéis dos nós (derivados por referência cruzada, CONFIRMADO):

| Papel | Quantidade | Exemplos |
|---|---|---|
| Nó de eixo de viga (apoio em pilar, apoio em viga, intermediário) | 74 | 37, 42, 38 (V1) |
| Nó de referência de pilar (`P1 36`) | 8 (subconjunto dos anteriores) | 36, 26, 2, 34, 32, 1, 29, 28 |
| Vértice de laje (subconjunto, mais vértices de bordo livre) | — | 47, 62, 61 (L4/L104) |
| **Nó apenas de carga** (só aparece em `DIP`) | 38 (ids 84–121) | 84, 85 … 121 |
| **Nó órfão** (não referenciado por nada) | 9 (55, 57, 76, 77, 78, 80, 81, 82, 83) | cantos de poços/faces auxiliares |

Consequência: o modelo intermediário deve carregar `roles` por nó, e o gerador ETABS **não deve criar joints para nós de carga e órfãos** na v1.

#### 1.3.3 Vigas — `V<n> EIXO <tok> <tok> ...` (CONFIRMADO)
Lista ordenada de nós que define o **eixo analítico** (poligonal). Cada token é `<nó><qualificador>`; o qualificador pode vir colado (`37P1`) ou separado por espaço/continuação (`50 N`).

| Qualificador | Significado | Evidência | Status |
|---|---|---|---|
| `P<k>` | extremidade/apoio sobre o pilar k | nó está sobre a face ou dentro do polígono de P<k> em todos os 32 casos | CONFIRMADO |
| `AV<k>` | esta viga **apoia** na viga k neste nó | V5 `20AV17` ↔ V17 `20RV5` (recíproco em 100% dos casos) | CONFIRMADO |
| `RV<k>` | esta viga **recebe** a viga k neste nó | idem | CONFIRMADO |
| `N` | nó intermediário livre (sem apoio) | nós 42–49, 50: cantos de bordo livre de laje sobre o eixo | CONFIRMADO |
| `ARE` / `ARD` | token isolado logo após o 1º nó (`ARE`) ou logo antes do último (`ARD`). Hipótese: **AR**ticulação (rótula) na extremidade **E**squerda/**D**ireita | V5, V7, V11, V18 têm `ARE`; V6, V7, V19 têm `ARD`; V7 tem ambos; todas essas extremidades chegam em núcleos ou em outras vigas | NEEDS_REVIEW (sem efeito na v1; armazenar como `release_start/release_end`) |

Trechos: 22 vigas, 41 trechos (segmentos entre nós consecutivos). O número de trechos coincide com o número de seções `S1..Sk` em DIMENSOES para **todas** as 22 vigas → `S<k>` é a seção do k-ésimo trecho (CONFIRMADO).

Comprimentos de eixo calculados (cm): V1 780,99; V3 814,63; V7 650,90; V16 149,91; V22 149,91 … O LST "Comprimento linear" é entre **faces de apoio** (V1: 7,30 m = 780,99 − 11 (P1) − 40 (P3) ≈ 730 ✓), o que confirma que o LST usa a seção `R`/`G` como seção do pilar neste piso (ver 1.4.2).

#### 1.3.4 Pilares — `P<n> <nó> <MAT> [CORTINA] [FURADO]`

| Campo | Significado | Status |
|---|---|---|
| `<nó>` | nó de referência do pilar no modelo de formas. **Não é o centroide**: é o ponto onde o eixo da viga principal encontra o pilar (P1: nó 36 na face esquerda, à altura do eixo da V3; P5: nó 32 no eixo longitudinal, 9 cm da face inferior; P3/P6: nó no meio do braço direito do U, sobre a face) | CONFIRMADO |
| `CON` | material concreto (mesmo código da coluna "Seção" da tabela de pisos do LST: `1 CON`) | CONFIRMADO |
| `CORTINA` | pilar-parede/cortina (P4, P8; o LST os marca "(Cortina)" e exclui do "volume de topo") | CONFIRMADO |
| `FURADO` | pilar com furo. P4/P8: o DXF anota "Furo 83x83 H71" e as LAMINAS deixam um vão de 83 cm (y 1894,12–1977,12). **P6 também é FURADO mas as lâminas não mostram vão** | NEEDS_REVIEW |

#### 1.3.5 Lajes — `L<n> ['título'] [GRE] AREA <cm²> <nó [LIV|P<k>]>... ANG <graus>`

| Campo | Significado | Status |
|---|---|---|
| `'título'` | nome exibido (ESCADA, REBAIXO1..6) | CONFIRMADO (textos idênticos no DXF, layer 10) |
| `GRE` | presente em todas exceto L8 'ESCADA'. Hipótese: laje modelada na **gre**lha (TQS Grelha); a escada não é | NEEDS_REVIEW |
| `AREA` | área **líquida** entre faces (cm²); o polígono de eixos dá área maior (L4: 142,87 m² pelo shoelace × 136,21 m² AREA) | CONFIRMADO |
| lista de nós | contorno **anti-horário** (14/14 lajes) sobre eixos de vigas/faces de pilares | CONFIRMADO |
| qualificador após o nó | tipo de apoio do **bordo que começa naquele nó**: `P<k>` = bordo sobre o pilar k; `LIV` = bordo livre; ausente = bordo sobre viga, inferida por os dois nós pertencerem ao eixo da mesma viga (verificado em 100% dos bordos contra a tabela "Influência das lajes" do LST: L3 trechos `3 P6`, `22 V8`, `9 V18`, `50 LIVRE`…) | CONFIRMADO |
| `ANG` | ângulo (0,000 em todas). Hipótese: direção principal de armação/grelha | NEEDS_REVIEW |

Lajes que compartilham bordos `LIV` (ex.: L4 `47 LIV 62 LIV 61 LIV` e L104 `46 LIV 61 LIV 62 LIV`) são regiões **rebaixadas, desconectadas na grelha**, ocupando o entalhe da laje principal.

### 1.4 Bloco DIMENSOES

#### 1.4.1 Vigas — `V<n> S1 b/h [DFS d] S2 b/h [DFS d] ... VOL <cm²> <cm³>` (CONFIRMADO)
- `S<k> b/h`: largura/altura (cm) do trecho k.
- `DFS d`: desnível da face superior (cm) relativo ao nível do piso. Convenção de sinal **positivo = para baixo** — NEEDS_REVIEW, com forte evidência: L100 tem `17.000 DFS 3.000` e chama-se REBAIXO (17 + 3 = 20 → fundo alinhado com as lajes de 20); V7 `S2 14/74 DFS 5.00` recebe no LST "Viga 7 e laje 3 não estão niveladas". As vigas de fachada (18/120 `DFS -40`) teriam a face superior 40 cm **acima** do piso (viga-peitoril). Confirmar com o usuário.
- `VOL a v`: `a` = área em planta (cm²; V1 13136 ≈ 18 × 730), `v` = volume (cm³; 1,576 m³ = LST 1,58).

#### 1.4.2 Pilares retangulares — `P<n> R L/B ANG a BASE u,v [DSC k] FCK 'Cxx'` (CONFIRMADO)
`L` = comprimento (maior lado), `B` = largura, `ANG` = rotação do eixo local *u* (direção de L) em relação ao X global. **`BASE u,v` é a posição do nó de referência em coordenadas locais** (u ao longo de L, v ao longo de B), medidas a partir de um canto-origem. Fórmula decodificada:

```
R(a) = [[cos a, −sin a], [sin a, cos a]]
origem  = nó − R(a)·(u, v)
retângulo = { origem + R(a)·(s, t) : 0 ≤ s ≤ L, 0 ≤ t ≤ B }
```

Validação (6/6 pilares R): os retângulos reconstruídos compartilham exatamente a face externa com o polígono `PSU` correspondente e, em P4/P8, coincidem com a união das `LAMINAS` (x ∈ [−2820,357, −2770,357] e [1129,864, 1179,864]).

| Pilar | nó | L×B | ANG | retângulo x | retângulo y | centroide |
|---|---|---|---|---|---|---|
| P1 | 36 | 229×40 | 90 | −1975,731 … −1935,731 | 2254,449 … 2483,449 | (−1955,731, 2368,949) |
| P2 | 26 | 229×40 | 90 | 295,239 … 335,239 | 2254,449 … 2483,449 | (315,239, 2368,949) |
| P4 | 34 | 920,9×50 | 270 | −2820,357 … −2770,357 | 1509,454 … 2430,354 | (−2795,357, 1969,904) |
| P5 | 32 | 176×40 | 270 | −1975,743 … −1935,743 | 1509,468 … 1685,468 | (−1955,743, 1597,468) |
| P7 | 29 | 176×40 | 270 | 295,251 … 335,251 | 1509,449 … 1685,449 | (315,251, 1597,449) |
| P8 | 28 | 920,9×50 | 270 | 1129,864 … 1179,864 | 1509,454 … 2430,354 | (1154,864, 1969,904) |

#### 1.4.3 Pilares genéricos — `P<n> G x,y; x,y; ...; BASE x,y [DSC k] FCK 'Cxx'` (CONFIRMADO)
Polígono em **coordenadas globais** (8 vértices, sem repetir o primeiro), e aqui `BASE` é **global** e igual ao nó de referência (P3: BASE = nó 2; P6: BASE = nó 1, identidade exata). Atenção: a semântica de `BASE` muda entre `R` (local) e `G` (global).

P3 = U de 771 × 279 cm, braços de 60, alma de 50 (área 6,60 m² = LST ✓). P6 = U espelhado, 771 × 565, braços 60, alma 50 (10,04 m² ✓). O edifício é simétrico em relação a x = −820,246 (centroide dos núcleos).

#### 1.4.4 `PSU <n> <polígono fechado>;` — NEEDS_REVIEW (hipótese forte)
Presente em todos os 8 pilares. Polígono **fechado** (repete o primeiro vértice) em coordenadas globais, **10 a 20 cm mais estreito** que a seção `R`/`G`, mantendo fixa a face externa (P1: 30 em vez de 40, face esquerda fixa; P3: braços de 40 em vez de 60; P4: 40 em vez de 50).

Hipótese: **P**ilar/seção **SU**perior — seção do lance que **nasce** neste piso (vai para o piso 26). Evidências: (i) o TQS Formas usa `R`/`G` para áreas, volumes e vãos deste piso (LST), logo `R`/`G` é o lance que **chega** (abaixo); (ii) o DXF desenha exatamente o `PSU` e os triângulos sólidos (layer 20) marcam precisamente a face/canto mantido fixo na redução; (iii) torre alta com redução de seção para cima é o cenário natural.

Impacto: para um modelo ETABS de um único pavimento, o pilar do story "25" é o que está **abaixo** do nível 74,77 m → usar `R`/`G`. Guardar `PSU` como `section_above` para a evolução multi-pavimento.

#### 1.4.5 `LAMINAS <n> <k>` + k polígonos fechados — CONFIRMADO (uso), NEEDS_REVIEW (nome)
Presente só nos pilares com `DSC 1` (P3: 6, P4: 4, P6: 10, P8: 4). Decomposição do pilar-parede em retângulos ("lâminas") **sem sobreposição** (a alma do U vai de face interna a face interna dos braços). Em P4/P8 as lâminas pulam o furo. É a informação ideal para gerar painéis de parede no ETABS e as linhas médias para os grids. `DSC 1` → hipótese "discretiza em lâminas" (NEEDS_REVIEW).

#### 1.4.6 Lajes — `L<n> h [DFS d] [BALANCO] LARM a b`
`h` espessura (cm) CONFIRMADO (DXF "h=20", "h=17", "h=14"); `DFS` rebaixo (cm) NEEDS_REVIEW (sinal, ver 1.4.1); `BALANCO` = laje em balanço (L100–L105, todas com 3 bordos `LIV` e 1 bordo em pilar ou viga) CONFIRMADO; `LARM a b` UNKNOWN (hipótese: códigos de armação/tipo de análise; valores 1/2).

### 1.5 Blocos CARGAS CASO 1..4 (CONFIRMADO, fora do escopo v1)
Caso 1 = "carregamentos 1 a 4" (permanente + acidental agrupados), 2 = peso próprio (automático, bloco vazio), 3 = permanentes, 4 = acidentais.

| Comando | Significado | Unidade |
|---|---|---|
| `V<n> DIS q` | carga distribuída em toda a viga | tf/m |
| `V<n> DIP n1 n2 q` | carga distribuída entre os nós n1 e n2 (nós de carga, 84–121) | tf/m |
| `L<n> ADI q` | carga de área na laje | tf/m² |
| `L<n> DIP n1 n2 q` | carga linear sobre a laje (parede) entre nós | tf/m |
| `L<n> ARE x,y; ...; VAL q` | carga de área em sub-região poligonal | tf/m² |

O parser deve capturá-los em `LoadCase`/`LoadItem` desde a Etapa 1 (custo baixo), sem que o restante do sistema dependa deles.

---

## 2. Arquivo LST — estrutura e uso

Relatório de processamento do TQS Formas (texto, Latin-1). É **dependente da versão** do TQS e dos critérios; deve ser lido por expressões regulares tolerantes, apenas nas seções úteis:

| Seção | Uso no conversor | Status |
|---|---|---|
| Cabeçalho ("Planta lida do edifício": edifício, planta, projeto, título, cliente) | metadados do projeto | CONFIRMADO |
| **"Definição de Pisos"** — `25  24o Andar  74.77  3.24  1  CON` | **única fonte de cota (74,77 m) e pé-direito (3,24 m)** | CONFIRMADO |
| Tabelas de seções/materiais | redundantes com o LDF (checagem) | CONFIRMADO |
| `***nnn AVISO: ...` | sistema de diagnóstico (seção 12). Famílias vistas: "Nó N na viga V não cai sobre o pilar P k Seção 1"; "Viga V e laje L não estão niveladas"; "Lajes A e B tem engastamento em desnível"; "Laje L bordo livre em concavidade"; "Retirado engastamento da laje"; "largura menor que ... NBR-6118 15.10"; "DISEIX/DISCIN não possível"; "Não há cargas de alvenaria" | CONFIRMADO |
| Quantitativos (por V/P/L: área, formas, volume, comprimento linear, comprimento médio de vão) | **validação cruzada** do parser (área de lajes, volume de vigas, comprimento entre faces) | CONFIRMADO |
| "Cargas nos vãos da VIGA n" (`/L /B /H`, apoios com Largura/Excen/HCS/HCI/DFSE/DFLE/TPS) | b/h por vão (checagem de S1..Sk); demais colunas NEEDS_REVIEW (DFSE ≈ h/2 na maioria, mas V7 dá 0,42 ≠ 0,395) | parcial |
| Cargas, influência de lajes, somatórias, critérios (PARFOR.DAT) | fora do escopo | — |

Os 4 avisos "não cai sobre o pilar P3" foram reproduzidos geometricamente: os nós 41, 18, 4 e 2 estão a **1,00–1,09 mm fora** do polígono `G` de P3 (nós 15 e 40, exatamente sobre a face, não geram aviso). A tolerância interna do TQS está entre 0,1 e 1 mm. Todos os quatro desaparecem com a normalização a 1 cm.

Formato dos avisos: `***<seq> AVISO: <texto>`; a mesma mensagem repete a cada caso de carregamento (013–016 = 029–032 = 038–041 …) → deduplicar por texto.

---

## 3. Arquivo DXF — estrutura e veredito

### 3.1 Formato
- `$ACADVER = AC1002` (AutoCAD R2.6, 1986). Formato muito antigo: sem handles, sem `LWPOLYLINE`, sem blocos; polilinhas como `POLYLINE`/`VERTEX`/`SEQEND`. Bibliotecas modernas (ezdxf) não garantem suporte a versões anteriores a R12 — risco listado na seção 15.
- Unidades: as mesmas do LDF (cm); `$LTSCALE 50`; `$EXTMIN/$EXTMAX` não correspondem ao conteúdo (extensões obsoletas).
- 569 entidades: 84 LINE, 76 POLYLINE (206 VERTEX), 111 TEXT, 12 SOLID, 4 CIRCLE. Sem INSERT/BLOCK.
- 89 layers numéricos (níveis do TQS, 0–255), cor por layer; 5 estilos de linha TQS.

### 3.2 Layers utilizados e interpretação (convenção de níveis do TQS Formas)

| Layer | Entidades | Interpretação | Status |
|---|---|---|---|
| 1 | 66 POLYLINE de 2 vértices | **faces** de vigas (pares paralelos distantes b) | CONFIRMADO |
| 3 | 8 POLYLINE fechadas | contorno de pilares — **idênticos aos `PSU` do LDF** (8/8, erro 0,000000 cm) | CONFIRMADO |
| 8 / 9 / 10 | TEXT h=20 | nomes de vigas / pilares / lajes | CONFIRMADO |
| 11 / 12 / 13 | TEXT h=12 (h=9 para cotas internas de P3/P6) | seção de viga "b/h" / dimensões de pilar / "h=" de laje | CONFIRMADO |
| 20 | 12 SOLID (6 triângulos) | marca na **face/canto fixo** do pilar na variação de seção (coincide 6/6 com a face compartilhada entre `R` e `PSU`) | NEEDS_REVIEW |
| 40 | 2 POLYLINE tracejadas (X em retângulo) | furo nos pilares P4/P8 (83 cm) | CONFIRMADO |
| 41 | TEXT h=10 | "Furo / 83x83 / H71" | CONFIRMADO |
| 216 / 221 / 226 | LINE, TEXT "50", CIRCLE | uma cota (alma de P3 = 50) | CONFIRMADO |
| 237 | 21 LINE | **bordos livres** de laje (contornos de rebaixos/aberturas), desenhados nas faces | CONFIRMADO |
| 241 | 18 LINE (9 pares em X) | marcação de **vazios** (poços de elevador/shaft) — regiões sem laje no LDF | CONFIRMADO |
| 242 | 32 LINE + 8 LINE de comprimento zero | **eixos analíticos de vigas** + **marcadores de centroide dos pilares** (comprimento zero) | CONFIRMADO |

Não há: linhas de grid/eixos arquitetônicos, cotas gerais, hachuras, blocos, nem informação de nível/cota.

### 3.3 Relação DXF × LDF (CONFIRMADO por comparação sistemática)

| Aspecto | Resultado |
|---|---|
| Polígonos de pilar (layer 3) × `PSU` | idênticos 8/8 |
| Polígonos de pilar × seção `R`/`G` | diferentes (DXF 10–20 cm mais fino) |
| Extremidades dos eixos de viga (layer 242) × nós do LDF | 52/64 coincidem (< 0,05 cm); 12 não: eixos prolongados até o **centroide** de P4 (+5 cm), P8 (+15 cm) e até a linha média dos braços **finos** (`PSU`) de P3/P6 (x = −1165,746 e −474,746 em vez de −1175,746 e −464,746) |
| Rótulos de vigas | 18/22 coincidem; 4 renumeradas (DXF V11→LDF V5, V19→V6, V23→V19, V24→V11); DXF tem 24 vigas |
| Seções de vigas | 13/22 iguais (todas 18/120 de fachada, V5/V6 14/50); 9 diferentes (V7, V8, V9, V10, V16, V17, V18, V20, V21, V22, V11/V19 — internas 9–18 cm mais baixas; V16/V22 40/85 × 60/100) |
| Rótulos de lajes e espessuras | iguais (L1–L7, ESCADA h=14, REBAIXO1–6 h=17) |

Conclusão: o DXF é a planta de **outro pavimento** (provavelmente um tipo superior, onde as seções já foram reduzidas — as seções desenhadas são exatamente as `PSU`) **ou de outra revisão**. Em qualquer dos casos não pode ser usado como referência geométrica do `25 - Tipo.LDF`. → NEEDS_REVIEW: o usuário deve confirmar a origem do `NORTH_TYP.DXF`.

### 3.4 O que só o DXF traria
Nada necessário ao modelo analítico: dimensão do furo dos pilares (83×83), marca da face fixa (derivável de `R` × `PSU`), textos (redundantes). Tudo o mais (faces de vigas, bordos livres, vazios, centroides) é **derivável do LDF**.

---

## 4. Sistema de coordenadas e unidades

| Item | Valor |
|---|---|
| Sistema | plano XY global da planta TQS, destro, Y para cima na planta; origem arbitrária do projeto (edifício ocupa x ∈ [−2820, 1180], y ∈ [1499, 2493] cm) |
| Unidade LDF/DXF | **cm**, 6 casas decimais (ruído numérico de 10⁻⁵ a 10⁻¹ cm observado: 2064,458607 × 2064,4492; 2443,448749 × 2443,448753) |
| Cota do piso | 74,77 m (LST), pé-direito 3,24 m → base do lance = 71,53 m |
| Unidade LST | m para geometria de pisos e quantitativos; cm para `/L /B /H` e comprimentos de trechos |
| Unidade interna proposta | **m** (SI), com os valores originais em cm preservados em `provenance` |
| Origem no ETABS | manter a origem TQS (recomendado — garante alinhamento entre pavimentos importados separadamente no futuro); translação opcional por configuração |

---

## 5. Entidades e relações

```
Story ──── 1..n ──── Node(id, x, y, roles)
  │                     ▲  ▲  ▲
  │                     │  │  └── LoadItem.DIP (n1, n2)        [nós 84–121: só carga]
  │                     │  └───── Slab.boundary[i].node        [bordo i: BEAM(inferida) | COLUMN(P k) | FREE(LIV)]
  │                     └──────── Beam.axis[i].node            [qualificador: P k | AV k | RV k | N]
  │                                     │
  ├── Column(P k) ── reference_node ────┘   section_here (R/G), section_above (PSU), laminas[], flags
  ├── Beam(V n) ── segments[k] ← DIMENSOES S<k> (b, h, dfs)
  │        └── supports: COLUMN(P k) | BEAM(AV k ⇄ RV k) | releases (ARE/ARD)
  └── Slab(L n) ── thickness, dfs, cantilever, grid_flag, angle, edges[]
```

Relações verificadas: `AV`/`RV` recíprocos (100%); todo bordo de laje sem qualificador está sobre um eixo de viga com os dois nós consecutivos (100%); todo nó `P k` está sobre a face (≤ 1,1 mm) ou dentro do polígono `R`/`G` de P k (100%).

---

## 6. O que vem de cada arquivo

| Informação | LDF | LST | DXF |
|---|---|---|---|
| Nós, eixos de vigas, seções por trecho, desníveis | ✔ | (b/h por vão, para checagem) | eixos (layer 242) e faces (layer 1) |
| Pilares: seção completa, posição, rotação, lâminas, seção superior, fck | ✔ | áreas/volumes (checagem) | só `PSU` |
| Lajes: contorno, tipo de bordo, espessura, rebaixo, balanço | ✔ | áreas (checagem) | só nomes/"h=" |
| Materiais e seções catalogadas | ✔ | ✔ | — |
| Cargas | ✔ | ✔ (processadas) | — |
| **Cota e pé-direito do pavimento** | ✗ | **✔** | ✗ |
| Nome do pavimento / edifício / cliente | ✔ (cabeçalho) | ✔ | ✗ |
| Avisos de processamento | ✗ | ✔ | ✗ |
| fck de vigas e lajes | ✗ | ✗ | ✗ → configuração |
| Furo em pilar (dimensão) | só flag `FURADO` + vão nas lâminas | ✗ | ✔ (texto) |

---

## 7. Limitações e ambiguidades (lista consolidada)

| # | Item | Status | Tratamento |
|---|---|---|---|
| A1 | `PSU` = seção do lance superior | NEEDS_REVIEW | usar `R`/`G` para o pilar do story; guardar `PSU` |
| A2 | `ARE`/`ARD` = rótulas nas extremidades | NEEDS_REVIEW | armazenar; sem efeito v1 |
| A3 | Sinal de `DFS` (positivo = para baixo) | NEEDS_REVIEW | armazenar com sinal bruto + flag de convenção; sem efeito geométrico v1 (tudo no nível do piso) |
| A4 | `GRE` em lajes; `ANG`; `LARM a b`; `DSC k`; `PROJETO` | NEEDS_REVIEW / UNKNOWN | atributos brutos em `tqs_attrs` |
| A5 | `FURADO` em P6 sem vão nas lâminas | NEEDS_REVIEW | diagnóstico INFO |
| A6 | Posição exata do nó de apoio dentro do pilar (9, 10, 11, 15, 20 cm da face conforme o caso; critério EXTAPO do TQS) | NEEDS_REVIEW | irrelevante: a viga será estendida/ajustada pela regra da seção 10.3 |
| A7 | Origem do `NORTH_TYP.DXF` | NEEDS_REVIEW | perguntar ao usuário; não usar |
| A8 | fck de vigas/lajes; E do concreto (LDF traz 21 GPa genérico) | UNKNOWN | configuração / etapa de materiais |
| A9 | L8 'ESCADA' (h=14, sem `GRE`) — incluir como laje no ETABS? | decisão | configuração (`include_stairs`), default: incluir com aviso |
| A10 | Lajes rebaixadas desconectadas (bordos `LIV` coincidentes) e `DFS` de vigas | decisão | v1: tudo no nível do story, com o desnível registrado; offsets na etapa "níveis" |
| A11 | Pilar-parede: frame × shell; significado de "eixo do pilar" para grids | **decisão de projeto** | seção 10.4 e 17 |
| A12 | 9 nós órfãos e 38 nós só de carga | CONFIRMADO | não geram joints; diagnóstico INFO |
| A13 | Vigas de fachada V12–V13 e V14–V15 **atravessam** P5/P7 (nó 32/29 compartilhado) a 79 cm do centroide | CONFIRMADO | reforça A11 |
| A14 | Variações de LDF de outros modelos (pilares circulares, inclinados, vigas curvas, outros qualificadores) | não observado | parser tolerante: token desconhecido → diagnóstico WARNING + atributo bruto, nunca exceção fatal |

---

## 8. Arquitetura proposta

### 8.1 Tecnologia
- **Python 3.12+** (mesmo ecossistema do WallsDesign; API do ETABS via COM funciona bem em Python).
- `dataclasses` (frozen) + `enum` para o modelo; sem dependência de framework no núcleo.
- `pytest` para testes; `shapely` opcional (offset/união de polígonos de parede — apenas no engine, isolado).
- ETABS: `comtypes` (`ETABSv1.dll`, `cOAPI`/`cSapModel`), ETABS ≥ v18. Alternativa/backup: escrita de **`.e2k`** (texto), que dispensa ETABS aberto e é diffável — ver 11.2.
- DXF (opcional, futuro): parser próprio de pares código/valor (o arquivo só tem 6 tipos de entidade) ou `ezdxf` se suportar AC1002.
- CLI primeiro (`tqs2etabs analyze`, `tqs2etabs export`); UI (Streamlit ou Qt) depois, consumindo apenas `ConversionResult`.

### 8.2 Camadas (dependências só para dentro)

```
ui / cli
   │
application         ConversionPipeline, ConversionResult, ComparisonReport, ReportWriter
   │
┌──┴─────────────┬──────────────────────┬─────────────────────┐
importers/tqs    geometry_engine        exporters/etabs       (adaptadores; cada um só conhece domain)
 ldf/ lst/ dxf/  normalization/         api/ e2k/ mapping/
                 alignment/ connectivity/
                 grids/ validation/
└──────────────────────────┬─────────────────────────────────┘
                         domain          StructuralModel, elementos, geometria 2D básica,
                                         Diagnostic, ChangeRecord, Tolerances (sem I/O)
infrastructure      configuration (TOML), logging, ids
```

Contratos:
- `Importer.parse(files) -> StructuralModel` — responde "o que existe no arquivo". Não corrige nada; registra diagnósticos de leitura.
- `GeometryRule.apply(model, tolerances) -> (model', changes, diagnostics)` — cada regra é uma função pura sobre um snapshot; o pipeline encadeia e acumula `ChangeRecord`s.
- `EtabsWriter.write(model, mapping) -> EtabsExportReport` — responde "como representar no ETABS"; interface única com duas implementações (`ComEtabsWriter`, `E2kEtabsWriter`).

### 8.3 Estrutura de pastas

```
ImportTQSxEtabs/
├── ARCHITECTURE.md
├── pyproject.toml
├── config/
│   └── default.toml                 # tolerâncias, nomenclatura de grids, política pilar/parede, unidades
├── src/tqs2etabs/
│   ├── domain/
│   │   ├── model.py                 # StructuralModel, Story, ProjectInfo
│   │   ├── elements.py              # Node, Column, Beam, Slab, Wall(*), Material, Section
│   │   ├── geometry.py              # Point, Segment, Polygon, transform, área, ponto-em-polígono
│   │   ├── diagnostics.py           # Diagnostic(level, code, refs, action), ChangeRecord
│   │   └── tolerances.py            # Tolerances (frozen dataclass), carregado do TOML
│   ├── importers/tqs/
│   │   ├── common.py                # leitura Latin-1, junção de continuações, tokenizador
│   │   ├── ldf/ (lexer.py, sections.py, geometry_parser.py, dimensions_parser.py, loads_parser.py, builder.py)
│   │   ├── lst/ (stories.py, warnings.py, quantities.py)
│   │   └── dxf/ (reader.py, layers.py, overlay.py)      # opcional; só verificação visual
│   ├── geometry_engine/
│   │   ├── pipeline.py              # ordem das regras, snapshots
│   │   ├── normalization/ (clustering.py, rounding.py)
│   │   ├── alignment/ (beam_column_transverse.py, beam_end_extension.py, column_axes.py)
│   │   ├── connectivity/ (node_merge.py, connectivity_check.py)
│   │   ├── grids/ (generator.py, naming.py)
│   │   └── validation/ (geometry.py, alignment.py, connectivity.py, integrity.py)
│   ├── exporters/etabs/
│   │   ├── writer.py                # interface EtabsWriter
│   │   ├── mapping.py               # políticas: pilar→frame|shell, nomes, seções, unidades
│   │   ├── com_writer.py            # comtypes / ETABSv1
│   │   ├── e2k_writer.py
│   │   └── readback.py              # validação pós-exportação via API
│   ├── application/
│   │   ├── conversion.py            # ConversionPipeline (fluxo da seção 20 do pedido)
│   │   ├── comparison.py            # TQS × normalizado × ETABS
│   │   └── report.py                # log de auditoria (texto/markdown/json)
│   ├── infrastructure/ (config.py, logging.py)
│   └── cli.py
├── tests/
│   ├── fixtures/ (25 - Tipo.LDF, 25 - Tipo.LST, mini_*.ldf sintéticos)
│   ├── unit/ (test_ldf_lexer.py, test_ldf_beams.py, test_ldf_columns.py, test_ldf_slabs.py, test_lst.py,
│   │         test_clustering.py, test_rounding.py, test_node_merge.py, test_alignment.py,
│   │         test_extension.py, test_grids.py, test_connectivity.py, test_validation.py, test_e2k.py)
│   └── integration/ (test_pipeline_25_tipo.py, test_comparison.py)
└── docs/ (LDF_FORMAT.md, DIAGNOSTIC_CODES.md, CHANGELOG.md)
```

---

## 9. Modelo estrutural intermediário

Independente do TQS e do ETABS; coordenadas em m; ids próprios (`str`), com `source_id` do TQS preservado. Esboço (não é código final):

```python
class NodeRole(Enum): BEAM_AXIS, COLUMN_REF, BEAM_SUPPORT, BEAM_INTERSECTION, SLAB_VERTEX, LOAD_ONLY, ORPHAN

@dataclass(frozen=True)
class Node:
    id: str; x: float; y: float; z: float; story_id: str
    roles: frozenset[NodeRole]; provenance: Provenance   # arquivo, id TQS, valores originais (cm)

@dataclass(frozen=True)
class RectSection:    length: float; width: float; angle_deg: float; origin: Point
@dataclass(frozen=True)
class PolygonSection: outline: Polygon; laminas: tuple[Polygon, ...]

@dataclass(frozen=True)
class Column:                       # "pilar" no sentido TQS: pode virar frame ou parede no ETABS
    id: str; name: str; story_id: str
    section: RectSection | PolygonSection     # lance que chega ao piso (R/G)
    section_above: Polygon | None             # PSU
    reference_node_id: str
    material_ref: str | None; fck: str | None
    kind_hint: ColumnKind                     # COLUMN | WALL | CORE (política, revisável)
    flags: frozenset[str]                     # CORTINA, FURADO, DSC...
    axes: tuple[AxisLine, ...]                # linhas de eixo p/ grids e conexão (derivadas)
    tqs_attrs: Mapping[str, str]

@dataclass(frozen=True)
class BeamSegment: start_node_id: str; end_node_id: str; width: float; depth: float; top_offset: float
@dataclass(frozen=True)
class BeamSupport: node_id: str; kind: SupportKind; ref_id: str | None   # COLUMN(P k) | BEAM(AV k) | RECEIVES(RV k) | FREE(N)
@dataclass(frozen=True)
class Beam:
    id: str; name: str; story_id: str
    axis: tuple[str, ...]                      # nós em ordem
    segments: tuple[BeamSegment, ...]          # len == len(axis) - 1
    supports: tuple[BeamSupport, ...]
    release_start: bool; release_end: bool     # ARE / ARD (NEEDS_REVIEW)
    is_inclined: bool = False                  # futuro: z por nó
    tqs_attrs: Mapping[str, str]

@dataclass(frozen=True)
class SlabEdge: start_node_id: str; end_node_id: str; support: EdgeSupport; ref_id: str | None   # BEAM | COLUMN | FREE
@dataclass(frozen=True)
class Slab:
    id: str; name: str; title: str | None; story_id: str
    edges: tuple[SlabEdge, ...]; holes: tuple[Polygon, ...]     # holes: futuro
    thickness: float; top_offset: float; is_cantilever: bool; in_grid_model: bool; angle_deg: float
    tqs_attrs: Mapping[str, str]

@dataclass(frozen=True)
class Story: id: str; name: str; tqs_index: int; elevation: float | None; height: float | None; source: str

@dataclass(frozen=True)
class GridLine: id: str; label: str; direction: Literal["X", "Y"]; coordinate: float; origin_column_ids: tuple[str, ...]

@dataclass(frozen=True)
class StructuralModel:
    project: ProjectInfo; stories; nodes; columns; beams; slabs; walls  # walls: vazio na v1 (pilares-parede ficam em Column + política)
    materials; sections; load_cases; grids; diagnostics: tuple[Diagnostic, ...]; changes: tuple[ChangeRecord, ...]
```

Preparação para o futuro: `z` por nó (rampas, vigas inclinadas), `story_id` em todos os elementos, `Column.section_above` + `Column.base_story_id/top_story_id` (multi-pavimento, pilares inclinados por dois pontos), `Slab.holes`, `Wall` como elemento próprio quando o TQS exportar paredes, `LoadCase`.

---

## 10. Geometry Engine

### 10.1 Pipeline (cada passo gera snapshot + `ChangeRecord`s)

```
1 classify_nodes            papéis; marca LOAD_ONLY/ORPHAN (INFO)
2 derive_column_axes        centroide (R) / linhas médias das lâminas (G/laminas) → AxisLine[]
3 cluster_coordinates       X e Y separadamente, tolerância cluster_tol; prioridade a valores vindos de pilar
4 round_clusters            representante do cluster → 2 casas (m); aplica a TODOS os nós/vértices do cluster
5 snap_beams_to_column_axes ajuste TRANSVERSAL (10.3a), tolerância beam_snap_tol
6 extend_beam_ends          ajuste LONGITUDINAL até a linha de eixo/linha média do pilar (10.3b)
7 merge_nodes               nós coincidentes (node_merge_tol) → um id; reindexa referências
8 generate_grids            a partir de AxisLine[] já normalizadas; nomeação separada
9 validate                  geometria, alinhamento, conectividade, integridade (seção 13)
```

### 10.2 Normalização e clustering (§8/§9 do pedido)

- Clustering 1-D por varredura ordenada (gap ≤ `cluster_tol`), **separado para X e Y**, sobre **todos** os valores de coordenada de nós e vértices de pilar.
- Representante do cluster: se o cluster contém coordenada derivada de pilar (face ou eixo), usa-se a do pilar; senão a média. Depois arredonda-se a 2 casas em m e **todos** os membros recebem o mesmo valor (elimina 21,24/21,240001/21,239999).
- Tolerância padrão proposta: **`cluster_tol = 0,005 m` (5 mm)**, não 1 cm. Justificativa medida neste arquivo: existem alinhamentos legítimos distintos a 0,86–0,90 cm (nós de carga −2703,263 × −2702,365 sobre V3/V12; y 1861,604 dos cantos do REBAIXO6 × 1862,468 do eixo da V10). Com 1 cm eles seriam fundidos indevidamente; com 5 mm todos os 20 clusters ruidosos reais (spreads 0,000001–0,10 cm) continuam unidos.
- Validação obrigatória após o passo 4: comprimento de cada trecho antes × depois (|Δ| ≤ `length_change_tol`, padrão 1 cm), fechamento e área das lajes, dimensões de pilar preservadas (todas as faces do mesmo pilar têm a mesma fração decimal → largura exata preservada: −434,746/−494,746 → −4,35/−4,95).

### 10.3 Duas regras de "alinhamento viga × pilar" (§6/§7)

**(a) Ajuste transversal por tolerância** — desloca a *linha* do eixo da viga para coincidir com uma linha de eixo do pilar quando a distância perpendicular ≤ `beam_snap_tol` (padrão 0,01 m). Pilar nunca se move. Neste arquivo a regra corrige apenas os desvios de 1 mm (V6/V16/V22 já estão nas linhas médias dos braços; nós 41/18/4/2). Registro: `Beam V6 | axis x: −4,94846 → −4,95 | ref P3 | rule column-axis-priority | tol 0,01`.

**(b) Extensão longitudinal da extremidade** — prolonga (ou recua) a extremidade da viga **ao longo do seu próprio eixo** até a linha de eixo do pilar de apoio (centroide de pilar-frame; linha média da lâmina de parede). Não é questão de tolerância: neste arquivo as distâncias são 0, 5, 10, 11, 15, 20, 25 e 30 cm (tabela abaixo). Limite de segurança `max_end_extension` (padrão 0,35 m = meia espessura máxima + folga); acima disso → ERROR, sem correção. O próprio TQS faz isso no desenho (eixos do layer 242 prolongados até x = −2795,357 em P4 e 1154,864 em P8).

| Viga | nó | pilar | dist. ao centroide (cm) | dist. à linha média (cm) |
|---|---|---|---|---|
| V1 | 37 | P1 | 106,1 | 11,0 |
| V1 / V2 | 38 / 25 | P3 | 369,3 | 10,0 |
| V3 / V12 | 35 / 34 | P4 | 451,5 | 5,0 |
| V4 / V15 | 28 / 30 | P8 | 451,7 | 15,0 |
| V3 / V4 | 36 / 27 | P1 / P2 | 56,1 | 20,0 |
| V5, V6, V7, V8–V11 | vários | P3 / P6 | 332–501 | 30,0 |
| V12–V13 / V14–V15 | 32 / 29 | P5 / P7 | 79,0 | 0,0 |
| V16 / V22 | 3,4 / 1,2 | P6 / P3 | 412–528 | 0,0–0,1 |
| V17, V19, V21 | 21, 19, 14 | P3 | 22–236 | 25,0 |

### 10.4 Eixos de pilar e grids (§10/§11)

Definição proposta de `AxisLine` por tipo de pilar:
- `RectSection` com L/B ≤ `wall_aspect_ratio` (padrão 5): 2 linhas pelo centroide (X e Y).
- `RectSection` com L/B > 5, `CORTINA`, `G`/`LAMINAS`: uma linha média por lâmina (lâminas colineares fundidas). Para P3: x = −4,65 (braço direito), x = −11,76 (braço esquerdo), y = 24,68 (alma).

Grids gerados (após normalização) para este pavimento, com a política acima (resultado do `tqs2etabs normalize`):

| Direção | Coordenadas (m) | Origem |
|---|---|---|
| X | −27,95; −19,56; −11,76; −4,65; 3,15; 11,55 | P4; P1+P5 (cluster 0,012 cm); P3+P6 braço esq.; P3+P6 braço dir.; P2+P7; P8 |
| Y | 15,24; 24,68 | alma P6; alma P3 |

Paredes retangulares (P1/P2/P4/P5/P7/P8) só geram o grid da sua linha média; o grid pela metade do comprimento (Y = 15,97; 19,70; 23,69) só aparece se o pilar for modelado como frame (`wall_aspect_ratio` maior). Cantos de núcleo: a alma é prolongada até as linhas médias dos braços e o braço é **dividido** no encontro (fica um toco de meia espessura da alma, 25 cm, representando o canto) — assim os painéis compartilham uma aresta e as vigas de fachada que chegam dentro da espessura da alma (V1/V2 em y = 24,74; V13/V14 em y = 15,18) encontram o eixo do braço.

Nomeação (`X1..Xn`, `Y1..Yn`, crescente) em módulo separado (`grids/naming.py`) com estratégia configurável (numérica, alfabética, prefixo). Grids secundários por eixos de vigas de fachade (y = 24,74; 24,21; 15,18) são opcionais (`secondary_grids_from_beams = false`).

### 10.5 Conectividade
`merge_nodes` funde nós a ≤ `node_merge_tol` (padrão 0,005 m) e reindexa vigas/lajes; depois `connectivity_check` verifica: cada extremidade de viga tem nó compartilhado com pilar (eixo) ou com outra viga (`AV`/`RV`); cada vértice de laje pertence a um eixo de viga ou linha de pilar ou é bordo livre; vigas que atravessam pilares (V12–V13 em P5) têm o nó de passagem sobre a linha de eixo.

---

## 11. Gerador ETABS

### 11.1 Mapeamento (política em `exporters/etabs/mapping.py`, isolada do engine)

| Domínio | ETABS |
|---|---|
| Story "25 - Tipo" (74,77 m; PD 3,24) | `Story.SetStories_2`: Base em 71,53 m, "25 - Tipo" em 74,77 m |
| Node | `PointObj.AddCartesian` (só nós estruturais) |
| Column `kind=COLUMN` | `FrameObj.AddByCoord` vertical no centroide, seção `PropFrame.SetRectangle(L, B)` + rotação `ANG` |
| Column `kind=WALL/CORE` | `AreaObj.AddByCoord` vertical por lâmina fundida (linha média × altura do lance), `PropArea.SetWall` espessura = B; pier label por pilar |
| Beam | um frame por **trecho** (41 frames) em z = 74,77, seção b×h por trecho; releases (ARE/ARD) só quando confirmados |
| Slab | `AreaObj.AddByCoord` horizontal no contorno normalizado, `PropArea.SetSlab` espessura h; rebaixos como shells separados (mesmo z na v1) |
| Grids | ver 11.3 |
| Unidades | `SetPresentUnits` kN·m (ou tf·m — configurável); coordenadas com 2 casas |
| Nomes | `P1`, `V1-1`, `V7-3`, `L4`, `L104` (rastreáveis ao TQS) |

### 11.2 Dois escritores, uma interface
- `ComEtabsWriter`: automação via `comtypes` (`ETABSv1.Helper.GetObject("CSI.ETABS.API.ETABSObject")` ou `CreateObject`), com verificação de versão. Permite leitura de volta (`readback.py`) para a validação pós-exportação.
- `E2kEtabsWriter`: gera o arquivo texto `.e2k` (separador decimal = o do Windows, vírgula em pt-BR — observado no VITREO-V05.e2k; configurável) (seções `$ STORIES`, `$ GRIDS`, `$ POINT COORDINATES`, `$ FRAME/AREA CONNECTIVITIES`, `$ FRAME/SHELL SECTIONS`...). Vantagens: testável sem ETABS instalado (golden files), diffável (auditoria), resolve o problema dos grids. Recomenda-se implementá-lo **primeiro** na Etapa 4 e o COM em seguida, mantendo os dois.

### 11.3 Grids — risco conhecido da API
A API do ETABS não expõe criação direta de linhas de grid individuais (`GridSys` só cria/posiciona o sistema). Caminhos: (1) `DatabaseTables.SetTableForEditingArray("Grid Definitions - Grid Lines")` + `ApplyEditedTables` (funciona em ETABS ≥ 17, frágil entre versões); (2) `.e2k`. A arquitetura isola isso em `com_writer.grids` com fallback para o E2K. Elevações a partir dos grids (§11) ficam preparadas: cada `GridLine` guarda `origin_column_ids`; a geração de vistas de elevação usará os labels — implementação futura.

---

## 12. Diagnóstico, log e auditoria

- `Diagnostic(level ∈ {INFO, WARNING, ERROR}, code, message, element_refs, source ∈ {TQS_LST, PARSER, ENGINE, EXPORTER}, action)`. Códigos catalogados em `docs/DIAGNOSTIC_CODES.md`; ex.: `TQS-W-NODE-OFF-COLUMN` (mapeado do aviso do LST "não cai sobre o pilar", com a distância medida e a ação tomada), `ENG-I-COORD-NORMALIZED`, `ENG-W-BEAM-EXTENDED`, `ENG-E-EXTENSION-EXCEEDS-MAX`, `PARSE-W-UNKNOWN-TOKEN`.
- `ChangeRecord(element_id, attribute, before, after, reason, rule, tolerance, step)` — um por alteração; permite o "BEFORE/AFTER/REASON" do pedido e a comparação da seção 14.
- Saídas: log texto no formato do pedido (blocos "TQS Import", "Geometry normalization", "Beam alignment", "Grid generation", "ETABS generation"), mais `conversion_report.json` (máquina) e `.md` (humano).
- Os avisos do LST entram como diagnósticos `source=TQS_LST`, deduplicados, e são **cruzados** com as ações do engine ("aviso 013 → nó 41 ajustado 1,0 mm para a face de P3 pela regra transversal").

## 13. Validação (antes da exportação e depois dela)

Geometria: nós duplicados; trechos de comprimento zero; Δcomprimento pós-normalização > tol; lajes não fechadas/auto-interseção/orientação; áreas × `AREA`/LST (tolerância relativa, lembrando que AREA é líquida). Alinhamento: extremidade de viga fora de linha de eixo do pilar após o engine; pilar sem grid; grids duplicados (< `cluster_tol`). Conectividade: extremidade sem nó compartilhado; `AV`/`RV` não recíprocos; viga que atravessa pilar sem nó; pilar sem viga/laje. Integridade: ids duplicados; referência a nó inexistente; `len(segments) != len(axis) − 1`; elemento sem geometria. Pós-exportação (COM): contagem de joints/frames/areas × esperado; coordenadas lidas de volta × modelo (≤ 1 mm); comprimentos.

## 14. Comparação TQS × normalizado × ETABS
`ComparisonReport` construído a partir dos snapshots do pipeline: por elemento, valores originais (cm, do `provenance`), normalizados (m) e, quando disponível, lidos do ETABS; contagens (existem/criados/modificados/ignorados), lista de modificados com `ChangeRecord`s. Ex.: `P1: (−1955,731; 2368,949) cm → (−19,56; 23,69) m — Coordinate normalization`.

## 15. Riscos técnicos

| Risco | Mitigação |
|---|---|
| Variações do LDF (versões do TQS, pilares circulares/inclinados, vigas curvas, novos qualificadores) | gramática por seção com tokens desconhecidos → WARNING + `tqs_attrs`; fixtures sintéticas; nunca falha silenciosa |
| Interpretações NEEDS_REVIEW (PSU, DFS, ARE/ARD) | armazenar bruto; decisões explícitas em `mapping`/config; confirmar com o usuário/TQS |
| Pilar-parede: modelagem frame × shell; conexão viga–parede | política configurável; v1 conservadora (shell); diagnóstico das excentricidades |
| API COM do ETABS (versão, ETABS aberto, 32/64 bits, exceções silenciosas) | interface com escritor E2K como alternativa e como golden test |
| Criação de grids via API | DatabaseTables com fallback E2K |
| Arredondamento alterando comprimentos/áreas | validação Δ obrigatória; abort/alerta configurável |
| Clustering fundindo alinhamentos distintos | `cluster_tol` 5 mm (evidência medida), clusters auditados no log |
| DXF AC1002 | não usado; se reativado, parser próprio |
| Encoding Latin-1 (acentos no LST) | leitura explícita `latin-1` |
| Nós de carga/órfãos poluindo o modelo | classificação de papéis; excluídos da exportação |

## 16. Testes propostos

Unitários (`tests/unit`):
- **Lexer LDF**: continuação `-`, comentários `$`, `50 -\nN`, strings com aspas, polígonos com `;`.
- **Parser vigas**: `V7` → 5 nós, 4 trechos, `ARE`/`ARD`, `RV17/RV19/RV21`; `V18` com `50 N` partido.
- **Parser pilares**: reconstrução de P1/P2/P4/P5/P7/P8 (tabela 1.4.2, tolerância 1e-3 cm); `G` com BASE global = nó; `PSU` e `LAMINAS` (contagens 6/4/10/4).
- **Parser lajes**: L3 → 13 bordos com tipos (P6, V8, V18, LIVRE, P6, V22, P3, V7×4, P3, V16) iguais ao LST.
- **Parser LST**: pisos (25, 74,77, 3,24), 59 avisos → famílias, quantitativos V1 (7,30 m; 1,58 m³).
- **Clustering**: `[21,244; 21,244; 21,246] → 21,24` (todos); `[−27,03263; −27,02365]` permanecem separados a 5 mm; prioridade a pilar (`P1 10,00; viga 10,007 → viga 10,00, P1 intacto`).
- **Arredondamento global**: `(21,244; 5,00)–(21,244; 10,00)` mantém 5,00 m.
- **Alinhamento transversal**: nó 41 (−4,94846) → −4,95 com ref P3; caso fora da tolerância → WARNING sem alteração.
- **Extensão longitudinal**: V4 nó 28 x 11,39864 → 11,55 (P8); extensão > max → ERROR.
- **Node merge**: dois nós a 2 mm → um id; referências reindexadas.
- **Grids**: pavimento inteiro → 6 X + 5 Y (10.4); sem duplicatas; nomeação estável.
- **Conectividade**: `AV`/`RV` recíproco; extremidade órfã detectada.
- **E2K**: golden file de um mini-modelo (1 pilar, 1 viga, 1 laje).
Integração: pipeline completo sobre `25 - Tipo` → 121/8/22/14 lidos, 74 nós estruturais, 41 trechos, 4 avisos do LST mapeados a 4 correções de 1 mm, 0 ERROR; comparação TQS × normalizado com Δcomprimento máx. ≤ 1 cm.

## 17. Plano incremental

| Etapa | Entrega | Critério de aceite |
|---|---|---|
| 0 | ARCHITECTURE.md; decisões da seção 18 | **concluída** (2026-09-19) |
| 1 | `domain` + parser LDF + CLI `analyze` (resumo: 121 nós, 8 pilares, 22 vigas, 14 lajes; geometrias reconstruídas; papéis de nós) | **concluída**: 44 testes; `tqs2etabs analyze` |
| 1b | parser LST (pisos, avisos, quantitativos) + validação cruzada LDF×LST | **concluída**: 44/44 grandezas (áreas de pilares e lajes, volumes de vigas) conferem |
| 2 | importador DXF | **descartada** (decisão 18.1) |
| 3 | Geometry Engine completo (10.1) + log/auditoria + comparação | **concluída**: `tqs2etabs normalize`; 4 avisos do TQS resolvidos; 24 extensões (0,05–0,30 m) registradas; 0 erros/0 avisos de validação; 6 X + 2 Y grids; 66 testes |
| 4 | Escritor E2K + validação pós-exportação | **concluída** (`tqs2etabs export`): 94 pontos, 41 vigas, 42 painéis (8 piers), 14 lajes, 8 grids; releitura do .e2k sem erros; 78 testes. Formato em docs/E2K_FORMAT.md. **Pendente: abrir no ETABS** (itens NEEDS_REVIEW do doc) e o escritor COM |
| 5 | Conversão completa — edifício multi-pavimento | **concluída**: `tqs2etabs building` (TESTE: 8 stories, 99 barras, 89 áreas, 16 piers; 90 testes); aceite na importação |
| 6 | Validação pós-exportação (readback) + relatório TQS × ETABS | E2K: releitura e comparação implementadas (`verify_export`); COM readback pendente |

## 17b. Regras adicionadas após a primeira importação no ETABS (2026-09-19)

Feedback do usuário sobre o modelo importado (itens A–D) virou regras explícitas do motor, todas configuráveis e auditadas:

| Regra | Passo | O que faz | Tolerância/config |
|---|---|---|---|
| **C — ponta do pilar** | `snap_beams_to_wall_ends` | Viga cuja extremidade encontra o eixo de uma parede a menos de `wall_end_snap` da **ponta real** dessa parede (junções de núcleo não contam) é deslocada transversalmente — a linha inteira — até passar pela ponta ("nó do pilar"), eliminando o dente. Vários apoios na mesma viga: escolhe-se o deslocamento que minimiza o maior resíduo. No 25 - Tipo: 10 vigas (9 cm nas fachadas, 7 cm na V7). | `wall_end_snap = 0,15 m` |
| **A — laje encosta na parede** | `snap_slab_vertices_to_column_axes` | Vértice de laje com bordo `P k` vai para a linha média da lâmina (interseção de duas linhas quando o vértice é um canto do núcleo). 12 vértices no 25 - Tipo. | meia espessura + `beam_column_snap` |
| **B/D — sem rebaixos** | `absorb_offset_slabs` | Laje com `DFS`/`BALANCO` que compartilha bordos livres com uma laje-mãe é incorporada (a reentrância some; espessura da mãe; registrado). 6 lajes REBAIXO no 25 - Tipo. | `absorb_offset_slabs` |
| **B/D — contorno reto + aberturas** | `simplify_slab_outlines` | Cadeia de bordos livres côncava → contorno reto pelo cruzamento das linhas de apoio vizinhas (viga × eixo do pilar), com o vazio virando **abertura** (`Slab.holes` → `AREA … OPENING "Yes"`); degraus de bordo livre ≤ `slab_dent_flatten_max` achatados; espigões e vértices colineares só de laje removidos. Resultado: L1/L2/L3/L7 retângulos, L4/L5 com 1 abertura cada. | `min_opening_area = 0,05 m²`, `slab_dent_flatten_max = 0,20 m`, `openings = "opening"` |
| Toco de parede | `mapping._trim_stubs` | Painel terminal < `trim_wall_stub_max` sem nó na ponta é eliminado (sobra do canto do núcleo além da última viga). | `trim_wall_stub_max = 0,15 m` |
| Grids | `grids.name_grids` | Letras em X (A, B, …) e números em Y (1, 2, …), como no modelo de referência. | `x_style`, `y_style` |
| Divisão das walls | `mapping._split_at_nodes` | Todo nó de viga/laje sobre o eixo divide o painel (junta explícita). | `split_walls_at_nodes` |

Ordem do pipeline: eixos → normalização → snap transversal → extensão → **ponta da parede** → **vértices de laje** → **absorção** → **simplificação** → merge → grids → validação.

## 17c. Multi-pavimento (Etapa 5, 2026-09-19)

Implementado sobre a pasta `TESTE` (fundação + Tipo 1 ×5 + Tipo 2 + Cobertura). Fontes e regras em
[docs/BUILDING_FILES.md](docs/BUILDING_FILES.md). Pontos de arquitetura:

- `domain/building.py` (PlanDefinition, PisoDefinition, ConcreteClass, BuildingDefinition) e
  `importers/tqs/building.py` (varredura: LDF/LST por planta, RESEST2, CONCRETO.DAT; planta de fundação detectada
  por pilares `NAS` sem vigas).
- Variantes do LDF encontradas e tratadas: 3º token do pilar é o **status** (`CON`/`NAS`/`MOR`), não material;
  `R L/B` sem `ANG` (= 0) e sem `FCK`; polígono `G` sem `LAMINAS` (decomposição retilínea própria); tabela de
  pisos do LST sem a coluna de material.
- `geometry_engine/multi_story.py`: eixos dos pilares transladados para a planta de referência (decisão:
  sem excentricidade entre lances). No TESTE os centroides já coincidem (0 translações).
- `EtabsMapper` (mapping.py): objetos por planta com prefixo `<TAG>.`, atribuídos a cada story com o material
  do piso; pontos compartilhados por coordenada; `SIMILARTO` para pisos repetidos; restrições na base.
- Regras de alinhamento refinadas com este modelo: deslocamento para a ponta da parede é rejeitado se tirar a
  viga do eixo de outro apoio (V1 colinear com P4/P7/P9/P13); deslocamentos ortogonais no mesmo nó são
  compostos; apoio interior de viga (viga passando sobre a parede) também é levado ao eixo; vértice de laje que
  é nó de viga pode deslizar ao longo da viga até o eixo da parede.

## 18. Decisões tomadas (2026-09-19)

| # | Decisão | Consequência na implementação |
|---|---|---|
| 1 | Entrada = **LDF + LST somente**. DXF fora do escopo (importador opcional apenas como overlay futuro). | `importers/tqs/dxf` não é implementado na v1. |
| 2 | Seção do pilar = **`R`/`G`** (a que o TQS usou para processar o piso). `PSU` armazenado como `section_above`. | builder reconstrói `R` pela fórmula 1.4.2; `G` direto. |
| 3 | **Shell** quando L/B > **3** ou pilar poligonal; frame caso contrário. "Eixo do pilar" = linha média da lâmina (walls) / centroide (frames). Neste piso: 8/8 pilares em shell (P5/P7 têm 4,4). | `ModelingPolicy.wall_aspect_ratio = 3.0`, `polygon_always_wall = true`. |
| 4 | Tolerâncias: `cluster_tol` 5 mm, `beam_snap_tol` 10 mm, `max_end_extension` 35 cm, `length_change_tol` 10 mm. | `config/default.toml`. |
| 5 | **Ignorar `DFS`** (vigas e lajes): tudo lançado no nível do piso, sem rebaixo, offset ou insertion point. `ARE/ARD` armazenados, sem efeito. | `ModelingPolicy.ignore_vertical_offsets = true`; valores continuam em `tqs_attrs`. |
| 6 | **Escada (L8) como elemento membrane**, no nível do piso; lajes rebaixadas como shells no nível do piso. | `ModelingPolicy.stair_area_type = "membrane"`. |
| 7 | **Escritor E2K primeiro**; COM em seguida. | Etapa 4 começa por `e2k_writer.py`. |
| 8 | Unidades finais **kN, m**. Origem: a partir de 2026-09-25 o modelo é transladado para que **(0,0) seja o canto inferior esquerdo** do perímetro. | `EtabsOptions.origin_at_min_corner`; `EtabsDescription.origin_shift` (a validação pós-exportação compara com o deslocamento). |
| 9 | **Eixos nos extremos do perímetro**: o primeiro e o último grid de cada direção cobrem todo o modelo (inclusive ponta de viga e bordo de laje), salvo quando o eixo existente está a menos de `boundary_tolerance` (0,25 m — meia espessura típica de parede/viga). | `EtabsOptions.bounding_grids`, `GridNaming.boundary_tolerance`, `EtabsMapper._renamed_grids`. |
| 10 | **Template .e2k/.$et do escritório**: definições (materiais, seções com modificadores, diafragmas, funções), configuração (P-Delta, malha, mass source, preferências de dimensionamento) e casos/combinações vêm de um modelo de referência; a geometria vem sempre do TQS. O template vence no mesmo nome, exceto material com E escolhido pelo usuário. Casos de construção sequencial têm os estágios refeitos sobre os pavimentos deste modelo. Load pattern citado por um caso e não definido é **criado vazio** (tipo deduzido do nome) para que casos e combinações do template existam mesmo sem a carga — é o que permite lançar depois o vento de túnel. Grupos entram só como nomes. Cargas do TQS: permanente → `SDL`, acidental de uso → `RLIVE`. | `exporters/etabs/template.py`, `EtabsOptions.template_path/template_definitions/template_analysis/template_combos`. |
| 11 | **Cantos de pilares em L/U no eixo** (2026-09-26): as lâminas terminam exatamente no cruzamento dos eixos; o trecho além do encontro (só a espessura do canto) é removido, sem toco de parede. O canto passa a contar como ponta da parede na regra da viga. **Viga nunca é inclinada**: o deslocamento até a ponta é rígido e decidido por alinhamento (vigas colineares ligadas andam juntas), escolhendo o que mantém mais apoios conectados, depois o que alcança mais pontas, depois o menor; ponta não alcançada = a viga termina no alinhamento do pilar (INFO ALIGN-I-WALL-END-MISSED / -KEPT). Junções em T continuam dividindo a lâmina atravessada. | `column_axes.join_corners`, `alignment._wall_true_ends`, `alignment.snap_beams_to_wall_ends`. |
