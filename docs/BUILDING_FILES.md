# Pasta de edifício do TQS — o que o `tqs2etabs building` lê

Varredura feita sobre `Desktop\TESTE\TESTE` (286 arquivos). Só os arquivos abaixo são usados; os demais foram
inspecionados e descartados (binários ou irrelevantes para o modelo analítico).

## Arquivos usados

| Arquivo | Conteúdo usado | Status |
|---|---|---|
| `<pasta>/<planta>/<planta>.LDF` | geometria da planta (nós, vigas, pilares com status `CON`/`NAS`/`MOR`, lajes). Se há mais de um `.LDF` na pasta (ex.: `Tipo 1/Tipo.LDF`, cópia antiga), usa-se o de nome igual ao da pasta e o outro é registrado como ignorado. | CONFIRMADO |
| `<pasta>/<planta>/<planta>.LST` | tabela **"Definição de Pisos"**: todos os pisos que usam a planta (replicação do tipo), com cota e pé-direito. Ex.: `Tipo 1.LST` lista os pisos 1–5. A coluna de material (`CON`) só aparece em alguns pisos — opcional. | CONFIRMADO |
| `<pasta>/ESPACIAL/RESEST2.TXT` | resumo por piso com **fck** de pilares, vigas e lajes (última coluna das linhas `Pilares`/`Vigas`/`Lajes` sob `Piso k: título`). Ex.: pilares C60 nos pisos 1–3, C50 nos demais; vigas e lajes C50. | CONFIRMADO |
| `<pasta>/CONCRETO.DAT` | catálogo de classes `Cxx` com **E inicial/secante do projeto** (MPa); quando definido, substitui a fórmula da NBR 6118 (ex.: C50 = 40 000 MPa; C60 = 42 000). Flag "só para fundações". | CONFIRMADO |

Planta **sem LST**, sem vigas/lajes e só com pilares `NAS` = planta de fundação: define a base do modelo
(cota da base = cota do piso 1 − pé-direito) e é a **referência dos eixos** dos pilares. Não vira story.

## Arquivos inspecionados e não usados

| Arquivo | Motivo |
|---|---|
| `EDIFICIO.BDE`, `EDIFICIO.DAT`, `*.PAV` | binários (dados do edifício); a informação necessária está nos LSTs |
| `MODELPAVX.DAT` (XML) | lista de plantas do Modelador, incluindo entradas obsoletas (`Tipo`) |
| `PILAR/0001.DAT` | dados do TQS-Pilar (lances, PD, seções): redundante com LDF/LST; útil só como conferência |
| `ESPACIAL/PILAR.LDF` | vazio (`CAD/PILAR … FIM`) |
| `ESPACIAL/PORFOR.TXT`, `RESEST1/3–8.TXT` | esforços/vento/quantitativos do pórtico espacial |
| `PARFOR.DAT`, `CRIT*.DAT`, `COMB*.DAT` | critérios e combinações |
| `*.DWG`, `FOR*.S0x`, `*.LAJ`, `*.GRF` | desenhos e resultados de grelha |

## Regras multi-pavimento

- Story k (k = 1…n) = piso k do LST: nome `"<k>-<título>"`, altura = pé-direito; base `"BASE"` na cota da fundação.
- Pilares/paredes do story k usam a seção `R`/`G` da planta do piso k (lance que chega ao piso).
- **Eixo do pilar alinhado entre pavimentos** (decisão do usuário): as linhas de eixo de cada planta são
  transladadas para coincidir com as da planta mais baixa em que o pilar existe (`multi_story.align_columns_to_reference`);
  registrado como `column-axis-continuity`. Sem excentricidade quando a seção reduz.
- Pisos com a mesma planta: objetos definidos uma vez e atribuídos a cada story (`SIMILARTO` no E2K); o
  material de cada story vem do RESEST2 do piso correspondente (paredes `W30-C60` nos pisos 1–3, `W30-C50` acima).
- Pontos são compartilhados entre pavimentos por coordenada (mesmo nome de ponto em todos os stories).
- Pilares poligonais sem `LAMINAS` (P17/P18 em L) são decompostos em retângulos (`decompose_rectilinear`).
