"""tqs2etabs — interface web (Streamlit): gerenciador de importacao TQS -> ETABS.

Fluxo: escolher a pasta do edificio TQS (ou enviar um .zip dela) -> varredura -> revisar e
editar pavimentos, materiais e opcoes -> gerar o .e2k e o relatorio de auditoria.

Execucao local:  streamlit run app/streamlit_app.py
Streamlit Cloud: arquivo principal app/streamlit_app.py (usa requirements.txt da raiz).
"""

from __future__ import annotations

import base64
import io
import os
import platform
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tqs2etabs import __version__                                             # noqa: E402
from tqs2etabs.application.building import (BuildingResult, convert_building_definition,  # noqa: E402
                                            format_building_report)
from tqs2etabs.domain.building import BuildingDefinition, PisoDefinition       # noqa: E402
from tqs2etabs.domain.config import Config, EtabsOptions, GridNaming, ModelingPolicy, Tolerances, load_config  # noqa: E402
from tqs2etabs.domain.diagnostics import Level                                # noqa: E402
from tqs2etabs.domain.elements import ColumnKind                              # noqa: E402
from tqs2etabs.domain.materials import SOURCES, fck_of, material_options      # noqa: E402
from tqs2etabs.importers.tqs.building import scan_building                    # noqa: E402

# --------------------------------------------------------------------------- visual
st.set_page_config(page_title="TQS → ETABS", page_icon="🏗️", layout="wide", initial_sidebar_state="expanded")

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;800&family=JetBrains+Mono:wght@400;600&display=swap');
:root { --bg:#0b1020; --card:#121a2e; --card2:#172140; --line:#243055; --txt:#e8edf7; --mut:#93a0bd;
        --acc:#4f8cff; --acc2:#22d3ee; --ok:#22c55e; --warn:#f59e0b; --err:#ef4444; }
html, body, [data-testid="stAppViewContainer"] { background: radial-gradient(1200px 600px at 10% -10%, #17224a 0%, var(--bg) 55%); color: var(--txt); font-family: Inter, system-ui, sans-serif; }
[data-testid="stSidebar"] { background: #0e1528; border-right: 1px solid var(--line); }
h1, h2, h3 { font-weight: 800; letter-spacing: -0.02em; }
.hero { padding: 26px 30px; border-radius: 20px; background: linear-gradient(135deg, #16234d 0%, #0f1a3a 60%, #0b1020 100%);
        border: 1px solid var(--line); box-shadow: 0 20px 60px rgba(0,0,0,.35); margin-bottom: 18px; }
.hero h1 { margin: 0; font-size: 2.1rem; background: linear-gradient(90deg,#fff,#9cc3ff); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.hero p { margin: 6px 0 0; color: var(--mut); }
.badge { display:inline-block; padding: 3px 10px; border-radius: 999px; font-size: .75rem; font-weight: 600; border: 1px solid var(--line); color: var(--mut); margin-right: 6px; }
.badge.ok { color: var(--ok); border-color: rgba(34,197,94,.4); }
.badge.warn { color: var(--warn); border-color: rgba(245,158,11,.4); }
.badge.err { color: var(--err); border-color: rgba(239,68,68,.4); }
.kpi { padding: 16px 18px; border-radius: 16px; background: var(--card); border: 1px solid var(--line); }
.kpi .v { font-size: 1.35rem; font-weight: 800; font-family: 'JetBrains Mono', monospace; color: #fff; overflow-wrap: anywhere; line-height: 1.2; }
.kpi .v.small { font-size: 1rem; }
.kpi .l { font-size: .8rem; color: var(--mut); text-transform: uppercase; letter-spacing: .08em; }
.step { color: var(--acc2); font-weight: 700; font-size: .8rem; letter-spacing: .12em; text-transform: uppercase; }
.diag { font-family: 'JetBrains Mono', monospace; font-size: .8rem; padding: 6px 10px; border-left: 3px solid var(--line); margin: 2px 0; background: rgba(255,255,255,.02); }
.diag.ERROR { border-color: var(--err); } .diag.WARNING { border-color: var(--warn); } .diag.INFO { border-color: var(--acc); }
div[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
[data-testid="stTab"] p { color: var(--mut) !important; font-weight: 600; }
[data-testid="stTab"][aria-selected="true"] p { color: var(--acc2) !important; }
[data-testid="stTabs"] [data-baseweb="tab-highlight"] { background: var(--acc2); }
[data-testid="stTabs"] [data-baseweb="tab-border"] { background: var(--line); }
.stButton>button { border-radius: 12px; font-weight: 600; }
.stDownloadButton>button { border-radius: 12px; font-weight: 700; background: linear-gradient(90deg, var(--acc), var(--acc2)); color: #051024; border: 0; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


def hero() -> None:
    st.markdown(
        f"""<div class="hero"><span class="badge">tqs2etabs v{__version__}</span><span class="badge">LDF · LST → E2K</span>
        <h1>TQS → ETABS</h1><p>Gerenciador de importação: leia o edifício do TQS, revise pavimentos, materiais e regras de
        modelagem, e gere o modelo analítico do ETABS com auditoria completa.</p></div>""",
        unsafe_allow_html=True)


def kpi(col, value, label) -> None:
    col.markdown(f'<div class="kpi"><div class="v">{value}</div><div class="l">{label}</div></div>', unsafe_allow_html=True)


def show_diags(diags, levels=("ERROR", "WARNING"), limit=60) -> None:
    shown = [d for d in diags if d.level.value in levels][:limit]
    if not shown:
        st.markdown('<span class="badge ok">sem avisos</span>', unsafe_allow_html=True)
    for d in shown:
        st.markdown(f'<div class="diag {d.level.value}">{d.format()}</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------- estado
def _find_building_root(folder: Path, max_depth: int = 4) -> Path:
    """Pasta que contem as subpastas de planta. Aceita niveis a mais (zip com a pasta dentro, usuario que
    escolheu a pasta-mae, Desktop-TESTE-TESTE...): busca em largura ate max_depth e devolve a mais rasa."""
    def has_plans(p: Path) -> bool:
        try:
            return any(q.is_dir() and any(f.suffix.upper() == ".LDF" for f in q.iterdir()) for q in p.iterdir() if q.is_dir())
        except OSError:
            return False
    level = [folder]
    for _ in range(max_depth + 1):
        for p in level:
            if has_plans(p):
                return p
        level = [q for p in level for q in p.iterdir() if q.is_dir()]
        if not level:
            break
    return folder


def load_definition(folder: Path, config: Config) -> BuildingDefinition:
    return scan_building(_find_building_root(folder), config)


# --------------------------------------------------------------------------- origem do edificio
# Na nuvem (Streamlit Community Cloud) o servidor e Linux e NAO enxerga o disco do usuario: so valem o seletor
# de pasta do navegador (componente abaixo) e o upload de .zip. O caminho local e o dialogo do Windows so
# existem quando o app roda na propria maquina.
RUNNING_LOCALLY = platform.system() == "Windows" and not os.environ.get("STREAMLIT_SERVER_HEADLESS_CLOUD")
APP_DIR = Path(__file__).resolve().parent
_folder_picker = components.declare_component("tqs_folder_picker", path=str(APP_DIR / "components" / "folder_picker"))


def browser_folder_picker() -> Path | None:
    """Seletor de pasta do navegador (webkitdirectory): o JS filtra .LDF/.LST/.DAT/RESEST2.TXT, compacta e
    devolve o zip em base64; extraimos em uma pasta temporaria e devolvemos a raiz do edificio."""
    val = _folder_picker(key="folder_picker", default=None)
    if not val or not val.get("zip_b64"):
        return Path(st.session_state["picked_dir"]) if st.session_state.get("picked_dir") else None
    key = f"pick:{val.get('root')}:{val.get('count')}:{val.get('stamp')}"
    if st.session_state.get("picked_key") != key:
        tmp = Path(tempfile.mkdtemp(prefix="tqs2etabs_pick_"))
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(val["zip_b64"]))) as z:
            z.extractall(tmp)
        st.session_state["picked_key"] = key
        st.session_state["picked_dir"] = str(tmp)
        st.session_state["picked_info"] = f"{val.get('root')} — {val.get('count')} arquivos lidos de {val.get('total')}"
    return Path(st.session_state["picked_dir"])


def windows_folder_dialog(initial: str = "") -> str | None:
    """Abre o dialogo nativo de pastas do Windows em um processo separado (tkinter nao gosta da thread do
    Streamlit). Devolve o caminho escolhido ou None."""
    code = "; ".join([
        "import sys, tkinter as tk",
        "from tkinter import filedialog",
        "r = tk.Tk()", "r.withdraw()", "r.attributes('-topmost', True)",
        "p = filedialog.askdirectory(title='Pasta do edificio TQS', initialdir=sys.argv[1] or None, mustexist=True)",
        "r.destroy()", "sys.stdout.write(p or '')",
    ])
    try:
        out = subprocess.run([sys.executable, "-c", code, initial], capture_output=True, text=True, timeout=600)
        return out.stdout.strip().replace("/", "\\") or None
    except Exception:  # noqa: BLE001
        return None


PISO_COLS = ["Importar", "Piso", "Título", "Planta", "Cota (m)", "Pé-direito (m)", "fck pilares", "fck vigas", "fck lajes"]


def pisos_to_df(bd: BuildingDefinition) -> pd.DataFrame:
    rows = [{"Importar": True, "Piso": p.index, "Título": p.title, "Planta": p.plan_tag, "Cota (m)": p.elevation,
             "Pé-direito (m)": p.height, "fck pilares": p.materials.get("pilares", ""),
             "fck vigas": p.materials.get("vigas", ""), "fck lajes": p.materials.get("lajes", "")}
            for p in bd.pisos]
    return pd.DataFrame(rows, columns=PISO_COLS)      # colunas fixas mesmo sem pisos


def df_to_pisos(df: pd.DataFrame, bd: BuildingDefinition, base_elevation: float) -> tuple[PisoDefinition, ...]:
    """Linhas marcadas em 'Importar', ordenadas por cota. Piso nao importado: os de cima se apoiam no
    de baixo (a altura do story passa a ser a diferenca de cotas)."""
    df = df.dropna(subset=["Planta"]).copy()
    if "Importar" in df.columns:
        df = df[df["Importar"].fillna(True).astype(bool)]
    df = df.sort_values("Cota (m)").reset_index(drop=True)
    pisos = []
    prev = base_elevation
    for i, row in df.iterrows():
        mats = {"pilares": str(row["fck pilares"] or "C40").upper(), "vigas": str(row["fck vigas"] or "C40").upper(),
                "lajes": str(row["fck lajes"] or "C40").upper()}
        cota = float(row["Cota (m)"])
        height = round(cota - prev, 4) if i > 0 else float(row["Pé-direito (m)"])
        pisos.append(PisoDefinition(i + 1, str(row["Título"] or bd.plans[row["Planta"]].name), cota, height,
                                    str(row["Planta"]), mats))
        prev = cota
    return tuple(pisos)


def build_config(base: Config, ui: dict) -> Config:
    return Config(
        tolerances=replace(base.tolerances, wall_end_snap=ui["wall_end_snap"], max_end_extension=ui["max_ext"]),
        policy=replace(base.policy, wall_aspect_ratio=ui["aspect"], openings=ui["openings"],
                       absorb_offset_slabs=ui["absorb"]),
        grids=replace(base.grids, x_style=ui["gx"], y_style=ui["gy"]),
        etabs=replace(base.etabs, default_material=ui["default_material"], decimal_separator=ui["decimal"],
                      export_loads=ui["loads"], include_columns=ui["columns"], include_beams=ui["beams"],
                      include_slabs=ui["slabs"], e_overrides=dict(ui.get("e_overrides", {})), split_walls_at_nodes=True),
        source=base.source)


# --------------------------------------------------------------------------- app
hero()
base_config = load_config()

with st.sidebar:
    st.markdown('<div class="step">1 · Origem</div>', unsafe_allow_html=True)
    modes = ["Escolher pasta no navegador", "Enviar .zip da pasta"]
    if RUNNING_LOCALLY:
        modes.insert(1, "Pasta local (caminho)")
    mode = st.radio("Como carregar o edifício", modes, label_visibility="collapsed")
    folder: Path | None = None
    if mode.startswith("Escolher"):
        folder = browser_folder_picker()
        if folder is not None and st.session_state.get("picked_info"):
            st.markdown(f'<span class="badge ok">{st.session_state["picked_info"]}</span>', unsafe_allow_html=True)
    elif mode.startswith("Pasta"):
        st.session_state.setdefault("path_input", st.session_state.get("path_text", ""))
        c_txt, c_btn = st.columns([4, 1], vertical_alignment="bottom")
        if c_btn.button("📂", help="Procurar pasta no Windows", use_container_width=True):
            chosen = windows_folder_dialog(st.session_state.get("path_text", ""))
            if chosen:
                st.session_state["path_input"] = chosen
        path_text = c_txt.text_input("Pasta do edifício TQS", key="path_input", placeholder=r"C:\Modelos TQS\MEU_EDIFICIO")
        if path_text:
            st.session_state["path_text"] = path_text
            folder = Path(path_text)
    else:
        up = st.file_uploader("Arquivo .zip com a pasta do edifício", type=["zip"])
        if up is not None:
            key = f"zip:{up.name}:{up.size}"
            if st.session_state.get("zip_key") != key:
                tmp = Path(tempfile.mkdtemp(prefix="tqs2etabs_"))
                with zipfile.ZipFile(io.BytesIO(up.getvalue())) as z:
                    z.extractall(tmp)
                st.session_state["zip_key"] = key
                st.session_state["zip_dir"] = str(tmp)
            folder = Path(st.session_state["zip_dir"])
    load_clicked = st.button("🔎 Varrer edifício", use_container_width=True, type="primary", disabled=folder is None)

    st.markdown('<div class="step" style="margin-top:18px">2 · O que importar</div>', unsafe_allow_html=True)
    sel = {
        "columns": st.checkbox("Pilares / paredes", True),
        "beams": st.checkbox("Vigas", True),
        "slabs": st.checkbox("Lajes", True),
        "loads": st.checkbox("Cargas de uso (ADI / DIS)", base_config.etabs.export_loads),
    }
    st.markdown('<div class="step" style="margin-top:18px">3 · Regras de modelagem</div>', unsafe_allow_html=True)
    ui = {**sel,
        "default_material": st.text_input("Material padrão (sem fck no TQS)", base_config.etabs.default_material),
        "aspect": st.slider("Pilar vira parede (shell) se L/B >", 1.0, 8.0, float(base_config.policy.wall_aspect_ratio), 0.5),
        "wall_end_snap": st.slider("Viga vai para a ponta da parede se a < (m)", 0.0, 0.5, float(base_config.tolerances.wall_end_snap), 0.01),
        "max_ext": st.slider("Extensão máx. da viga até o eixo (m)", 0.1, 1.0, float(base_config.tolerances.max_end_extension), 0.05),
        "absorb": st.toggle("Absorver lajes rebaixadas na laje-mãe", base_config.policy.absorb_offset_slabs),
        "openings": st.selectbox("Reentrâncias de bordo livre", ["opening", "fill"],
                                 index=0 if base_config.policy.openings == "opening" else 1,
                                 format_func=lambda v: "abertura explícita" if v == "opening" else "só preencher"),
        "decimal": st.selectbox("Separador decimal do E2K", [",", "."], index=0 if base_config.etabs.decimal_separator == "," else 1),
        "gx": st.selectbox("Grids X", ["letters", "numbers", "prefix"], index=0),
        "gy": st.selectbox("Grids Y", ["numbers", "letters", "prefix"], index=0),
    }

if load_clicked and folder is not None:
    if not folder.is_dir():
        st.error(f"Pasta não encontrada: {folder}")
    else:
        with st.spinner("Lendo LDF/LST, materiais e catálogo de concreto…"):
            try:
                bd = load_definition(folder, build_config(base_config, ui))
                st.session_state["definition"] = bd
                st.session_state["pisos_df"] = pisos_to_df(bd)
                st.session_state.pop("result", None)
            except Exception as exc:  # noqa: BLE001
                st.exception(exc)

bd: BuildingDefinition | None = st.session_state.get("definition")
if bd is None:
    if not RUNNING_LOCALLY:
        st.warning("Este app está rodando na nuvem: o servidor **não enxerga as pastas do seu computador**. "
                   "Use **Escolher pasta no navegador** (só os .LDF/.LST/.DAT/RESEST2.TXT são enviados) ou envie um .zip da pasta.")
    st.info("Informe a pasta do edifício TQS (ou envie um .zip dela) e clique em **Varrer edifício**. "
            "O app lê os `.LDF`/`.LST` de cada planta, o fck por piso (`ESPACIAL/RESEST2.TXT`) e o E do "
            "concreto (`CONCRETO.DAT`).")
    st.stop()

# ---------------------------------------------------------------- 2 · resumo
st.markdown('<div class="step">2 · Edifício</div>', unsafe_allow_html=True)
c1, c2, c3, c4, c5 = st.columns(5)
kpi(c1, bd.name, "edifício")  # nome pode ser longo
kpi(c2, len(bd.plans), "plantas")
kpi(c3, len(bd.pisos), "pisos")
top = bd.pisos[-1].elevation if bd.pisos else 0.0
kpi(c4, f"{top - bd.base_elevation:.2f} m", "altura total")
mats = sorted({m for p in bd.pisos for m in p.materials.values()})
kpi(c5, " · ".join(mats) or "—", "classes de concreto")

errs = [d for d in bd.diagnostics if d.level == Level.ERROR]
if errs:
    st.error("A varredura encontrou problemas que impedem a geração:")
    show_diags(errs, ("ERROR",))
if not bd.pisos:
    found = ", ".join(f"{t} ({p.name})" for t, p in bd.plans.items()) or "nenhuma"
    st.error("Nenhum pavimento encontrado. O app precisa, em cada pasta de planta, do par `<planta>.LDF` + "
             "`<planta>.LST` com a tabela **Definição de Pisos** (gerada pelo TQS ao processar o edifício). "
             f"Plantas reconhecidas: {found}. Confira a pasta escolhida (deve ser a pasta do edifício, que contém "
             "as subpastas das plantas) e os avisos em *Diagnóstico da varredura*.")

tab_pisos, tab_plantas, tab_mat, tab_cat, tab_diag = st.tabs(["Pavimentos", "Plantas", "Materiais", "Concreto (TQS)",
                                                              "Diagnóstico da varredura"])

with tab_pisos:
    st.caption("Edite cota, pé-direito, planta e fck de cada piso. Use a última linha para **adicionar pavimentos** "
               "(ex.: replicar um tipo); linhas podem ser removidas. Os pisos são renumerados de baixo para cima.")
    plan_tags = [t for t, p in bd.plans.items() if not p.is_base]
    edited = st.data_editor(
        st.session_state["pisos_df"], num_rows="dynamic", use_container_width=True, hide_index=True,
        column_config={
            "Importar": st.column_config.CheckboxColumn(help="Desmarque para não importar este pavimento"),
            "Piso": st.column_config.NumberColumn(step=1, disabled=True),
            "Título": st.column_config.TextColumn(),
            "Planta": st.column_config.SelectboxColumn(options=plan_tags, required=True),
            "Cota (m)": st.column_config.NumberColumn(format="%.2f", step=0.01),
            "Pé-direito (m)": st.column_config.NumberColumn(format="%.2f", step=0.01, min_value=0.5),
            "fck pilares": st.column_config.TextColumn(), "fck vigas": st.column_config.TextColumn(),
            "fck lajes": st.column_config.TextColumn(),
        }, key="pisos_editor")
    colA, colB, colC = st.columns([1, 1, 2])
    add_plan = colA.selectbox("Planta a replicar", plan_tags, key="add_plan")
    add_h = colB.number_input("Pé-direito (m)", value=float(bd.pisos[-1].height if bd.pisos else 3.0), step=0.01, key="add_h")
    if colC.button("➕ Adicionar pavimento no topo", use_container_width=True):
        df = edited.copy()
        last = df.sort_values("Cota (m)").iloc[-1] if len(df) else None
        cota = float(last["Cota (m)"]) + add_h if last is not None else bd.base_elevation + add_h
        proto = next((p for p in bd.pisos if p.plan_tag == add_plan), None)
        df.loc[len(df)] = {"Importar": True, "Piso": len(df) + 1, "Título": proto.title if proto else bd.plans[add_plan].name,
                           "Planta": add_plan, "Cota (m)": round(cota, 2), "Pé-direito (m)": add_h,
                           "fck pilares": proto.materials.get("pilares") if proto else ui["default_material"],
                           "fck vigas": proto.materials.get("vigas") if proto else ui["default_material"],
                           "fck lajes": proto.materials.get("lajes") if proto else ui["default_material"]}
        st.session_state["pisos_df"] = df
        st.rerun()
    st.session_state["pisos_df_edited"] = edited

with tab_plantas:
    rows = []
    for tag, p in bd.plans.items():
        rows.append({"Tag": tag, "Nome": p.name, "LDF": Path(p.ldf_path).name, "LST": "sim" if p.lst_path else "não",
                     "Papel": "fundação (base, referência dos eixos)" if p.is_base else
                     f"pisos {[x.index for x in bd.pisos if x.plan_tag == tag]}"})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(f"Base do modelo: cota {bd.base_elevation:.2f} m" + (f" (planta {bd.base_plan_tag})" if bd.base_plan_tag else ""))

with tab_mat:
    st.caption("Módulo de elasticidade a adotar no ETABS por classe: **Atual** = CONCRETO.DAT do modelo TQS; "
               "**Prudêncio** = valores do modelo de referência MARAMBAIA; **NBR 6118** = Ecs = αi·5600·√fck "
               "(αE = 1,0). Escolha a fonte ou digite um valor manual.")
    pisos_now = st.session_state.get("pisos_df_edited", st.session_state["pisos_df"])
    used = sorted({str(v).upper() for col in ("fck pilares", "fck vigas", "fck lajes") for v in pisos_now[col].dropna()},
                  key=lambda c: fck_of(c) or 0)
    used = [c for c in used if fck_of(c)] or [ui["default_material"]]
    if "mat_df" not in st.session_state or set(st.session_state["mat_df"]["Classe"]) != set(used):
        rows = []
        for cls in used:
            cat = bd.concrete_catalog.get(cls)
            opts = material_options(cls, cat.e_secant_mpa if cat else None)
            src = "atual" if opts["atual"] else ("prudencio" if opts["prudencio"] else "nbr6118")
            rows.append({"Classe": cls, "fck (MPa)": fck_of(cls), "E atual — TQS (MPa)": opts["atual"],
                         "E Prudêncio (MPa)": opts["prudencio"], "E NBR 6118 (MPa)": opts["nbr6118"],
                         "Adotar": src, "E adotado (MPa)": opts[src]})
        st.session_state["mat_df"] = pd.DataFrame(rows)
    mat_edit = st.data_editor(
        st.session_state["mat_df"], use_container_width=True, hide_index=True, key="mat_editor",
        column_config={
            "Classe": st.column_config.TextColumn(disabled=True),
            "fck (MPa)": st.column_config.NumberColumn(disabled=True, format="%.0f"),
            "E atual — TQS (MPa)": st.column_config.NumberColumn(disabled=True, format="%.0f"),
            "E Prudêncio (MPa)": st.column_config.NumberColumn(disabled=True, format="%.0f"),
            "E NBR 6118 (MPa)": st.column_config.NumberColumn(disabled=True, format="%.0f"),
            "Adotar": st.column_config.SelectboxColumn(options=list(SOURCES), required=True),
            "E adotado (MPa)": st.column_config.NumberColumn(format="%.0f", step=100,
                                                              help="Preenchido pela fonte escolhida; editável com Adotar = manual"),
        })
    overrides: dict[str, float] = {}
    resolved = []
    for _, row in mat_edit.iterrows():
        src = row["Adotar"]
        col_map = {"atual": "E atual — TQS (MPa)", "prudencio": "E Prudêncio (MPa)", "nbr6118": "E NBR 6118 (MPa)"}
        value = row["E adotado (MPa)"] if src == "manual" else row[col_map[src]]
        if pd.isna(value) or not value:
            value = row["E NBR 6118 (MPa)"]
            st.warning(f"{row['Classe']}: fonte '{src}' não disponível; usando NBR 6118.")
        overrides[str(row["Classe"])] = float(value)
        resolved.append(f"{row['Classe']} → {float(value):.0f} MPa ({src})")
    ui["e_overrides"] = overrides
    st.markdown(" · ".join(f'<span class="badge ok">{r}</span>' for r in resolved), unsafe_allow_html=True)

with tab_cat:
    if bd.concrete_catalog:
        cat = pd.DataFrame([{"Classe": c.name, "fck (MPa)": c.fck_mpa, "E secante (MPa)": c.e_secant_mpa,
                             "E inicial (MPa)": c.e_initial_mpa, "Só fundação": c.foundation_only}
                            for c in bd.concrete_catalog.values()])
        st.dataframe(cat, use_container_width=True, hide_index=True)
        st.caption("E definido no CONCRETO.DAT substitui a fórmula da NBR 6118 (8.2.8) no material do ETABS.")
    else:
        st.info("CONCRETO.DAT não encontrado: E pela NBR 6118 8.2.8.")
    for k, v in bd.sources.items():
        st.code(f"{k}: {v}", language="text")

with tab_diag:
    show_diags(bd.diagnostics, ("ERROR", "WARNING", "INFO"), limit=200)

# ---------------------------------------------------------------- 4 · gerar
st.markdown('<div class="step" style="margin-top:12px">4 · Gerar modelo ETABS</div>', unsafe_allow_html=True)
gen = st.button("⚙️ Gerar E2K", type="primary", use_container_width=True, disabled=bool(errs))
if gen:
    try:
        pisos = df_to_pisos(st.session_state.get("pisos_df_edited", st.session_state["pisos_df"]), bd, bd.base_elevation)
        if not pisos:
            st.error("Nenhum pavimento selecionado para importar.")
            st.stop()
        edited_bd = replace(bd, pisos=pisos, base_elevation=pisos[0].elevation - pisos[0].height)
        cfg = build_config(base_config, ui)
        with st.spinner("Normalizando plantas, alinhando eixos, gerando E2K e validando…"):
            result = convert_building_definition(edited_bd, None, cfg)
        st.session_state["result"] = result
    except Exception as exc:  # noqa: BLE001
        st.exception(exc)

result: BuildingResult | None = st.session_state.get("result")
if result is not None:
    d = result.description
    c = d.counts()
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    kpi(k1, len(d.stories) - 1, "stories")
    kpi(k2, c["beams"], "vigas")
    kpi(k3, c["walls"], "painéis de parede")
    kpi(k4, c["columns"], "pilares-barra")
    kpi(k5, c["slabs"], "lajes")
    kpi(k6, c["area_loads"] + c["line_loads"], "cargas")
    status = "err" if result.has_errors else "ok"
    st.markdown(f'<span class="badge {status}">{"com erros — veja o diagnóstico" if result.has_errors else "validação pós-exportação sem erros"}</span>'
                f'<span class="badge">piers: {", ".join(d.piers)}</span>'
                f'<span class="badge">grids: {", ".join(g.label for g in d.grids)}</span>', unsafe_allow_html=True)
    fname = f"{d.title.replace(' ', '_') or 'modelo'}.e2k"
    b1, b2 = st.columns(2)
    b1.download_button("⬇️ Baixar .e2k", data=result.e2k_text.encode("ascii", "replace"), file_name=fname,
                       mime="text/plain", use_container_width=True)
    b2.download_button("⬇️ Baixar relatório de auditoria", data=format_building_report(result, verbose=True).encode("utf-8"),
                       file_name=fname.replace(".e2k", "_relatorio.txt"), mime="text/plain", use_container_width=True)

    t1, t2, t3 = st.tabs(["Resumo por planta", "Diagnóstico", "Plantas (desenho)"])
    with t1:
        rows = []
        for tag, pr in result.plans.items():
            m = pr.normalization.model
            val = pr.normalization.engine.step("validate_model").stats
            ov = pr.normalization.engine.step("trim_beams_over_walls").stats
            rows.append({"Planta": tag, "Pisos": ", ".join(pr.stories) or "(fundação)", "Pilares": len(m.columns),
                         "Paredes": sum(1 for x in m.columns.values() if x.kind_hint == ColumnKind.WALL),
                         "Vigas": len(m.beams), "Lajes": len(m.slabs), "Erros": val["ERROR"], "Avisos": val["WARNING"],
                         "Viga sobre parede removida (m)": round(ov["removed_length"], 2)})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    with t2:
        show_diags(result.diagnostics, ("ERROR", "WARNING", "INFO"), limit=200)
        for tag, pr in result.plans.items():
            with st.expander(f"Planta {tag}: alterações registradas ({len(pr.normalization.model.changes)})"):
                ch = [c for c in pr.normalization.model.changes if not c.rule.startswith("coordinate-normalization")]
                st.code("\n".join(x.format() for x in ch[:300]) or "(só arredondamentos)", language="text")
    with t3:
        try:
            import plotly.graph_objects as go
            for tag, pr in result.plans.items():
                m = pr.normalization.model
                fig = go.Figure()
                for col in m.columns.values():
                    for s in col.axes:
                        fig.add_trace(go.Scatter(x=[s.start.x, s.end.x], y=[s.start.y, s.end.y], mode="lines",
                                                 line=dict(color="#f59e0b", width=max(2, s.thickness * 12)),
                                                 name=col.id, showlegend=False, hovertext=f"{col.id} t={s.thickness:.2f}"))
                    if col.kind_hint == ColumnKind.COLUMN:
                        cc = col.centroid
                        fig.add_trace(go.Scatter(x=[cc.x], y=[cc.y], mode="markers", marker=dict(color="#f59e0b", size=10),
                                                 showlegend=False, hovertext=col.id))
                for b in m.beams.values():
                    pts = [m.node(n).point for n in b.axis]
                    fig.add_trace(go.Scatter(x=[p.x for p in pts], y=[p.y for p in pts], mode="lines",
                                             line=dict(color="#4f8cff", width=2), showlegend=False, hovertext=b.id))
                for s in m.slabs.values():
                    pts = [m.node(e.start_node_id).point for e in s.edges]
                    fig.add_trace(go.Scatter(x=[p.x for p in pts] + [pts[0].x], y=[p.y for p in pts] + [pts[0].y],
                                             fill="toself", fillcolor="rgba(34,211,238,.08)", mode="lines",
                                             line=dict(color="#22d3ee", width=1), showlegend=False, hovertext=s.id))
                fig.update_layout(title=f"{tag} — {', '.join(pr.stories) or 'fundação'}", height=520,
                                  paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                  font=dict(color="#e8edf7"), margin=dict(l=20, r=20, t=40, b=20))
                fig.update_yaxes(scaleanchor="x", scaleratio=1, gridcolor="#243055")
                fig.update_xaxes(gridcolor="#243055")
                st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            st.info("Instale `plotly` para ver o desenho das plantas.")
