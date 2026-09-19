# Formato .e2k (ETABS 23.2) — o que foi observado e o que o tqs2etabs escreve

Referência: `VITREO-V05.e2k` (gravado pelo ETABS 23.2.0 em máquina pt-BR, 20 368 linhas).

## Regras gerais observadas

| Item | Observação |
|---|---|
| Seções | linha `$ TITULO`; registros com dois espaços de recuo; linha em branco entre seções |
| **Separador decimal** | **vírgula** (`HEIGHT 3,53`, `MERGETOL 0,00254`): o ETABS usa o separador do Windows. `etabs.decimal_separator` (padrão `,`) |
| Unidades | `UNITS "KN" "M" "C"` em `$ CONTROLS` |
| Stories | de cima para baixo: `STORY "nome" HEIGHT h` …, base `STORY "BASE" ELEV z` |
| Grids | `GRIDSYSTEM "G1" TYPE "CARTESIAN" BUBBLESIZE 1,25` + `GRID "G1" LABEL "A" DIR "X" COORD 0 VISIBLE "Yes" BUBBLELOC "End"` (Y: `"Start"`) |
| Materiais | `MATERIAL "C60" TYPE "Concrete" WEIGHTPERVOLUME …` / `SYMTYPE "Isotropic" E … U … A …` / `FC …` (kN/m²) |
| Seções de barra | `FRAMESECTION "B19X60-C50" MATERIAL "C50" SHAPE "Concrete Rectangular" D 0,6 B 0,19` + `CONCRETESECTION … TYPE "Beam"/"Column"` |
| Propriedades de casca | `SHELLPROP "S20-C50-SH" PROPTYPE "Slab" MATERIAL "C50" MODELINGTYPE "ShellThin"/"Membrane" SLABTYPE "Slab" SLABTHICKNESS 0,2`; paredes `PROPTYPE "Wall" … WALLTHICKNESS 0,4` |
| Piers | `$ PIER/SPANDREL NAMES` → `PIERNAME "P1"` |
| Pontos | **2D**: `POINT "25" 0,89 15,48`; o pavimento é atribuído no objeto |
| Barras | `LINE "C1" COLUMN "51" "51" 1` (flag 1 = ponto i no pavimento de baixo); `LINE "B1" BEAM "28" "8" 0` |
| Áreas | parede: `AREA "W17" PANEL 4 "2" "25" "25" "2" 1 1 0 0` (dois primeiros pontos no pavimento de baixo); laje: `AREA "F12" FLOOR 4 "11" "79" "56" "51" 0 0 0 0` |
| Atribuições | `POINTASSIGN "2" "11-TF" USERJOINT "Yes"`; `POINTASSIGN "51" "BASE" RESTRAINT "UX UY UZ"`; `LINEASSIGN "B1" "11-TF" SECTION "…" [RELEASE "M2J M3J"] CARDINALPT 8 MAXSTASPC 0,5 AUTOMESH "YES" MESHATINTERSECTIONS "YES"`; colunas `CARDINALPT 5 [ANG 90] MINNUMSTA 3 …`; `AREAASSIGN "W17" "11-TF" SECTION "W40-C50" OBJMESHTYPE "DEFAULT" ADDRESTRAINT "Yes" CARDINALPOINT "MIDDLE" TRANSFORMSTIFFNESSFOROFFSETS "No"`; lajes `… ADDRESTRAINT "No" CARDINALPOINT "TOP" …` |
| Cargas | `LOADPATTERN "DEAD" TYPE "Dead" SELFWEIGHT 1`; `LINELOAD "B1" "11-TF" TYPE "UNIFF" DIR "GRAV" LC "SDL" FVAL 0,66`; `AREALOAD "F19" "11-TF" TYPE "UNIFF" DIR "GRAV" LC "RLIVE" FVAL 2,5` |
| Análise | `ACTIVEDOF "UX UY UZ RX RY RZ"`, `AUTOMESHOPTIONS MESHTYPE "GENERAL" FLOORMESHMAXSIZE … WALLMESHMAXSIZE …`, `MASSSOURCE …` |
| Fim | `$ LOG` … `$ END OF MODEL FILE` |

## O que o tqs2etabs escreve (`exporters/etabs/e2k_writer.py`)

Um pavimento (`STORY "25 - Tipo" HEIGHT 3,24` + `STORY "BASE" ELEV 71,53`), grids do motor geométrico,
materiais `C<fck>` (E pela NBR 6118 8.2.8, γ = 25 kN/m³), seções `B<b>X<h>-<mat>` (vigas) e `C<B>X<L>-<mat>`
(pilares-frame), `W<t>-<mat>` (paredes), `S<h>-<mat>-SH|-M` (lajes shell-thin / escada membrane), pontos com o
número do nó TQS (auxiliares numerados a partir de max+1), `LINE … BEAM` por trecho (`V7-1`…), `LINE … COLUMN`
por pilar-frame, `AREA … PANEL` por segmento de linha média (dividido nos nós de viga/laje; `PIER "<pilar>"`),
`AREA … FLOOR` por laje, restrições na base em todos os pontos de parede/pilar, um padrão `DEAD` e um caso
linear estático.

Não escreve: cargas (fora do escopo v1), rebaixos/offsets (decisão 18.5), releases ARE/ARD
(`etabs.apply_releases = false`), diafragmas, combinações.

## A conferir na primeira importação no ETABS (NEEDS_REVIEW)

1. Posição da palavra `PIER "P3"` dentro de `AREAASSIGN` (o arquivo de referência define os piers mas não os atribui).
2. `CONCRETESECTION` mínima (sem `LONGBARMATERIAL`): se o ETABS exigir, apagar a seção ou completar com o aço padrão.
3. Orientação de pilar-frame: `ANG = TQS − 90 (mod 180)` — ver `mapping.etabs_column_angle`.
4. Se a máquina usar ponto decimal, exportar com `decimal_separator = "."` (ou `--config`).
