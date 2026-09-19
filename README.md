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
| 5 — Conversão completa | E2K completo; aceite na importação |
| 6 — Validação TQS × ETABS | releitura do E2K implementada; readback via COM pendente |
