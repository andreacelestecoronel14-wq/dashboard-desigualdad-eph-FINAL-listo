"""
Dashboard - Dinámica de ingresos y desigualdad (EPH-INDEC)
============================================================
T4 2024 vs T4 2025 | Nacional (31 aglomerados) y aglomerado Corrientes

Ejecutar:  streamlit run app.py
Lee ÚNICAMENTE los CSV de data/processed/ generados por src/etl.py
(no hay ningún número escrito a mano en este archivo, salvo las dos
cifras de referencia del INDEC marcadas abajo).
"""

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

DATA_DIR = Path(__file__).parent / "data" / "processed"
P24, P25 = "T4 2024", "T4 2025"

# Gini oficial publicado por INDEC (Total urbano, 31 aglomerados), tomado de
# la planilla de comparación del proyecto (Gini_Deciles_Corrientes_con_
# comparacion_INDEC.xlsx). Es solo una referencia para contrastar el
# Gini Nacional calculado con los microdatos.
GINI_INDEC_REF = {P24: 0.430, P25: 0.427}

st.set_page_config(page_title="Desigualdad de ingresos | EPH-INDEC", page_icon="📊", layout="wide")


@st.cache_data
def leer(nombre):
    ruta = DATA_DIR / nombre
    return pd.read_csv(ruta) if ruta.exists() else None


gini_df = leer("gini_resumen.csv")
deciles_df = leer("deciles_corrientes.csv")
brechas_df = leer("brechas_decilicas.csv")
poblacion_df = leer("poblacion_corrientes.csv")
if any(x is None for x in (gini_df, deciles_df, brechas_df, poblacion_df)):
    st.error("Faltan los datos procesados. Corré primero: `python src/etl.py --t4_2024 "
             "data/raw/usu_individual_T424.xlsx --t4_2025 data/raw/usu_individual_T425.xlsx`")
    st.stop()

lorenz_grid = leer("lorenz_grid.csv")
lorenz_diff = leer("lorenz_diff.csv")
palma_df = leer("palma.csv")
theil_df = leer("theil.csv")
comp_df = leer("composicion_ingreso.csv")
nr_df = leer("no_respuesta.csv")
pobreza_df = leer("pobreza.csv")

tiene_real = deciles_df["ipcf_promedio_decil_real"].notna().all()
# factor de deflación implícito (T4 2025 respecto de T4 2024), sin hardcodear
factor_ipc = None
if tiene_real:
    a = deciles_df[deciles_df.periodo == P25].iloc[0]
    factor_ipc = a["ipcf_promedio_decil"] / a["ipcf_promedio_decil_real"]

# ------------------------------------------------------------------ encabezado
st.title("📊 Dinámica de ingresos y desigualdad")
st.caption("Fuente: EPH - INDEC · T4 2024 vs T4 2025 · Ingreso: IPCF (per cápita familiar) · "
           "Ponderador: PONDIH · Unidad: persona")
st.warning(
    "**Dos trimestres no son una tendencia.** La EPH es una muestra (en Corrientes, unos 320-340 hogares "
    "por trimestre): un cambio de +0,005 en el Gini puede ser puro error de muestreo. Por eso el Gini se "
    "muestra con su intervalo de confianza del 95%. Si los intervalos se solapan, la conclusión honesta es "
    "**«no hay cambio detectable»**.", icon="⚠️")

recorte = st.sidebar.radio("Recorte geográfico", ["Corrientes", "Nacional"], index=0)
st.sidebar.markdown("---")
st.sidebar.markdown(
    "**Metodología (resumen)**\n\n"
    "- Filtro de calidad: DECCFR entre 1 y 10.\n"
    "- Gini: regla del trapecio sobre la curva de Lorenz (por persona, PONDIH); IC 95% por bootstrap de hogares.\n"
    "- Deciles de Corrientes recalculados **dentro del aglomerado**.\n"
    + ("- Ingresos reales: deflactados por IPC nacional (promedio oct-dic), base T4 2024.\n" if tiene_real else
       "- ⚠️ Ingresos en pesos corrientes (falta la serie de IPC).\n"))

# ------------------------------------------------------------------ KPIs
g = gini_df[gini_df.recorte == recorte].set_index("periodo")
g24, g25 = g.loc[P24], g.loc[P25]
delta = g25.gini - g24.gini
se_solapan = not (g24.ci_high < g25.ci_low or g25.ci_high < g24.ci_low)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Gini T4 2024", f"{g24.gini:.4f}", help=f"IC 95%: [{g24.ci_low:.3f} ; {g24.ci_high:.3f}]")
c2.metric("Gini T4 2025", f"{g25.gini:.4f}", delta=f"{delta:+.4f}", delta_color="off",
          help=f"IC 95%: [{g25.ci_low:.3f} ; {g25.ci_high:.3f}]")
c2.caption("Intervalos solapados → **sin cambio detectable**" if se_solapan
           else "Intervalos separados → cambio estadísticamente significativo")

b = brechas_df.set_index("periodo")
if recorte == "Corrientes":
    c3.metric("Brecha D10/D1 (2025)", f"{b.loc[P25, 'D10_D1']:.2f}x",
              delta=f"{b.loc[P25, 'D10_D1'] - b.loc[P24, 'D10_D1']:+.2f}", delta_color="off")
    c4.metric("% del ingreso del decil 10 (2025)", f"{b.loc[P25, 'pct_ingreso_decil10'] * 100:.1f}%",
              delta=f"{(b.loc[P25, 'pct_ingreso_decil10'] - b.loc[P24, 'pct_ingreso_decil10']) * 100:+.1f} p.p.",
              delta_color="off")
    pob = poblacion_df.set_index("periodo")["poblacion_ponderada"]
    st.caption(f"Población ponderada de Corrientes (con ingreso válido): {pob[P24]:,.0f} → {pob[P25]:,.0f} "
               f"({(pob[P25] / pob[P24] - 1) * 100:+.2f}%).")

k1, k2, k3 = st.columns(3)
if palma_df is not None and recorte == "Corrientes":
    pl = palma_df.set_index("periodo")["ratio_palma"]
    k1.metric("Ratio de Palma (2025)", f"{pl[P25]:.2f}", delta=f"{pl[P25] - pl[P24]:+.2f}", delta_color="off",
              help="% del ingreso del decil 10 ÷ % del ingreso de los deciles 1 a 4.")
if nr_df is not None:
    n25 = nr_df.set_index("periodo").loc[P25]
    k2.metric("Hogares sin ingreso válido (T4 2025)", f"{n25.tasa_no_respuesta * 100:.1f}%",
              help="Hogares con DECCFR = 0 o 12 sobre el total de hogares de la base (todo el país).")
if pobreza_df is not None:
    pv = pobreza_df[(pobreza_df.periodo == P25) & (pobreza_df.recorte == recorte)]
    if not pv.empty and pd.notna(pv.tasa_pobreza_hogares.iloc[0]):
        k3.metric("Incidencia de pobreza (aprox.)", f"{pv.tasa_pobreza_hogares.iloc[0] * 100:.1f}%")
    else:
        k3.metric("Incidencia de pobreza", "Pendiente", help="Falta cargar la CBT (NEA/nacional) en src/etl.py.")

st.markdown("**Cómo leerlo:** Gini cerca de 0 = igualdad; cerca de 1 = máxima desigualdad.")
st.divider()

# ------------------------------------------------------------------ Gini con IC
st.subheader("Gini con intervalo de confianza: Corrientes y Nacional")
st.caption("Punto = estimación; barra = IC 95% (bootstrap sobre hogares). El rombo gris es el Gini publicado por "
           "INDEC para el total urbano, como contraste del cálculo Nacional.")
fig = go.Figure()
for rec, color in [("Nacional", "#1f77b4"), ("Corrientes", "#d62728")]:
    s = gini_df[gini_df.recorte == rec].sort_values("periodo")
    fig.add_trace(go.Scatter(
        x=s.periodo, y=s.gini, mode="markers+lines", name=rec, marker=dict(size=12, color=color),
        line=dict(color=color, dash="dot"),
        error_y=dict(type="data", symmetric=False, array=s.ci_high - s.gini, arrayminus=s.gini - s.ci_low,
                     thickness=2, width=6)))
fig.add_trace(go.Scatter(x=list(GINI_INDEC_REF), y=list(GINI_INDEC_REF.values()), mode="markers",
                         name="INDEC publicado (total urbano)",
                         marker=dict(symbol="diamond", size=11, color="#7f7f7f")))
fig.update_layout(yaxis_title="Gini", height=430, legend=dict(orientation="h", y=1.1))
st.plotly_chart(fig, width="stretch")
st.divider()

# ------------------------------------------------------------------ Lorenz
st.subheader("Diferencia de la curva de Lorenz (2025 − 2024) · Corrientes")
st.caption("Las dos curvas de Lorenz son casi idénticas a ojo; su resta muestra en qué tramo cambió algo. "
           "Por debajo de cero: ese tramo perdió participación en el ingreso acumulado; por encima: ganó.")
if lorenz_diff is not None:
    f = go.Figure()
    f.add_hline(y=0, line_dash="dash", line_color="gray")
    f.add_trace(go.Scatter(x=lorenz_diff.x_pob_acum_pct, y=lorenz_diff.diferencia_pct, mode="lines",
                           fill="tozeroy", line=dict(color="#9467bd", width=2.5)))
    f.update_layout(xaxis_title="Percentil de población acumulada", yaxis_title="Diferencia (p.p. de ingreso acumulado)",
                    xaxis_tickformat=".0%", yaxis_tickformat=".1%", height=400)
    st.plotly_chart(f, width="stretch")
if lorenz_grid is not None:
    with st.expander("Ver las curvas de Lorenz superpuestas (solo referencia)"):
        ls = lorenz_grid[lorenz_grid.recorte == recorte]
        f2 = go.Figure()
        f2.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines", name="Igualdad perfecta",
                                line=dict(dash="dash", color="gray")))
        for p, col in [(P24, "#1f77b4"), (P25, "#d62728")]:
            d = ls[ls.periodo == p]
            f2.add_trace(go.Scatter(x=d.x_pob_acum_pct, y=d.y_ing_acum_pct, mode="lines", name=p,
                                    line=dict(color=col, width=3)))
        f2.update_layout(xaxis_tickformat=".0%", yaxis_tickformat=".0%", height=420,
                         xaxis_title="% de población acumulada", yaxis_title="% de ingreso acumulado")
        st.plotly_chart(f2, width="stretch")
st.divider()

# ------------------------------------------------------------------ Deciles
st.subheader("Deciles de ingreso · aglomerado Corrientes")
piv_sh = deciles_df.pivot(index="decil_local", columns="periodo", values="pct_ingreso_total") * 100
fd = go.Figure()
for p, col in [(P24, "#1f77b4"), (P25, "#d62728")]:
    fd.add_trace(go.Bar(x=piv_sh.index, y=piv_sh[p], name=p, marker_color=col))
fd.update_layout(barmode="group", xaxis_title="Decil (1 = más pobre)", yaxis_title="% del ingreso total",
                 xaxis=dict(tickmode="linear"), height=400, legend=dict(orientation="h", y=1.1))
st.plotly_chart(fd, width="stretch")

col_ing = "ipcf_promedio_decil_real" if tiene_real else "ipcf_promedio_decil"
piv_ing = deciles_df.pivot(index="decil_local", columns="periodo", values=col_ing)
var = (piv_ing[P25] / piv_ing[P24] - 1) * 100
if tiene_real:
    st.markdown(f"**Variación REAL del IPCF promedio por decil** (deflactado por IPC; precios T4 2025 ÷ T4 2024 = "
                f"{factor_ipc:.3f}, es decir, {(factor_ipc - 1) * 100:.1f}% de inflación)")
else:
    st.markdown("**Variación NOMINAL del IPCF promedio por decil** (⚠️ sin deflactar: no indica poder adquisitivo)")
fv = go.Figure(go.Bar(x=var.index, y=var.values, marker_color=["#2ca02c" if v >= 0 else "#d62728" for v in var]))
fv.add_hline(y=0, line_color="gray")
fv.update_layout(xaxis_title="Decil", yaxis_title="Variación " + ("real" if tiene_real else "nominal") + " (%)",
                 xaxis=dict(tickmode="linear"), height=380)
st.plotly_chart(fv, width="stretch")

ca, cb = st.columns(2)
with ca:
    st.markdown("**Brechas entre deciles**")
    st.dataframe(b[["D10_D1", "D9_D1", "D10_D5", "pct_ingreso_decil10"]].rename(columns={
        "D10_D1": "D10/D1", "D9_D1": "D9/D1", "D10_D5": "D10/D5", "pct_ingreso_decil10": "% ingreso D10"}),
        width="stretch")
with cb:
    st.markdown("**IPCF promedio por decil**")
    nom = deciles_df.pivot(index="decil_local", columns="periodo", values="ipcf_promedio_decil")
    tabla = nom.rename(columns=lambda c: f"{c} (nominal)")
    if tiene_real:
        real = piv_ing.rename(columns=lambda c: f"{c} (real, $ T4 2024)")
        tabla = pd.concat([tabla, real], axis=1)
    st.dataframe(tabla.style.format("${:,.0f}"), width="stretch")
st.divider()

# ------------------------------------------------------------------ Composición
st.subheader("Composición del ingreso por decil · Corrientes")
st.caption("Participación de cada fuente en el ingreso individual total de las personas del decil. "
           "Laboral = P21 + TOT_P12; jubilaciones = V2*; transferencias = V4, V5*, V21*, V22*; otros = resto de T_VI.")
if comp_df is not None:
    per = st.radio("Trimestre", [P24, P25], horizontal=True, key="per_comp")
    d = comp_df[comp_df.periodo == per].sort_values("decil_local")
    fc = go.Figure()
    for c, n, col in [("pct_laboral", "Laboral", "#1f77b4"), ("pct_jubilacion", "Jubilaciones/pensiones", "#ff7f0e"),
                      ("pct_transferencias", "Transferencias", "#2ca02c"), ("pct_otros", "Otros no laborales", "#7f7f7f")]:
        fc.add_trace(go.Bar(x=d.decil_local, y=d[c] * 100, name=n, marker_color=col))
    fc.update_layout(barmode="stack", xaxis_title="Decil", yaxis_title="% del ingreso", xaxis=dict(tickmode="linear"),
                     height=400, legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fc, width="stretch")
st.divider()

# ------------------------------------------------------------------ Theil
if theil_df is not None:
    st.subheader("Índice de Theil: dentro y entre formales e informales · Corrientes")
    st.caption("Grupo: PP07H (¿le descuentan jubilación?). Solo personas ocupadas con respuesta válida; "
               "no es toda la población. Sin intervalo de confianza: leer las diferencias entre trimestres con cautela.")
    st.dataframe(theil_df.set_index("periodo")[["theil_total", "theil_within", "theil_between",
                                                "pct_explicado_por_entre_grupos"]], width="stretch")
    st.divider()

# ------------------------------------------------------------------ Hallazgos
st.subheader("Resumen de hallazgos")
L = []
L.append(f"- **Gini {recorte}:** {g24.gini:.4f} → {g25.gini:.4f} ({delta:+.4f}). " +
         ("Los intervalos de confianza se **solapan**: no hay cambio detectable con esta muestra."
          if se_solapan else "Los intervalos **no** se solapan: el cambio es estadísticamente significativo."))
if recorte == "Corrientes":
    sh = piv_sh[P25] - piv_sh[P24]
    gan = ", ".join(f"D{i}" for i in sh.index if sh[i] > 0)
    per_ = ", ".join(f"D{i}" for i in sh.index if sh[i] < 0)
    L.append(f"- **Quién ganó y quién perdió participación en el ingreso:** ganaron {gan}; perdieron {per_}. "
             f"El decil 10 pasó de {b.loc[P24, 'pct_ingreso_decil10'] * 100:.1f}% a "
             f"{b.loc[P25, 'pct_ingreso_decil10'] * 100:.1f}% y D10/D5 bajó de {b.loc[P24, 'D10_D5']:.2f}x a "
             f"{b.loc[P25, 'D10_D5']:.2f}x, mientras D10/D1 subió de {b.loc[P24, 'D10_D1']:.2f}x a "
             f"{b.loc[P25, 'D10_D1']:.2f}x: se abrió la brecha contra el piso y el techo se achicó; ganó el medio.")
    if tiene_real:
        neg = ", ".join(f"D{i} ({var[i]:+.1f}%)" for i in var.index if var[i] < 0)
        pos = ", ".join(f"D{i} ({var[i]:+.1f}%)" for i in var.index if var[i] >= 0)
        L.append(f"- **En términos reales** (IPC +{(factor_ipc - 1) * 100:.1f}%): perdieron poder adquisitivo {neg}; "
                 f"se mantuvieron o mejoraron {pos}. Las cifras son de una muestra chica: interpretarlas como "
                 "orden de magnitud, no como valores exactos.")
    if comp_df is not None:
        c1_ = comp_df[(comp_df.decil_local == 1)].set_index("periodo")
        L.append(f"- **Decil 1:** el ingreso no laboral fue {(1 - c1_.loc[P24, 'pct_laboral']) * 100:.0f}% del total en "
                 f"T4 2024 y {(1 - c1_.loc[P25, 'pct_laboral']) * 100:.0f}% en T4 2025; las transferencias pasaron de "
                 f"{c1_.loc[P24, 'pct_transferencias'] * 100:.0f}% a {c1_.loc[P25, 'pct_transferencias'] * 100:.0f}%.")
L.append("- Todo se calcula con microdatos públicos de la EPH-INDEC y es reproducible con `src/etl.py`.")
st.markdown("\n".join(L))

# ------------------------------------------------------------------ Ficha técnica
with st.expander("📄 Ficha técnica y flujo muestral"):
    if nr_df is not None:
        t = nr_df.set_index("periodo").T
        t.index = ["Hogares en la base (país)", "Excluidos por DECCFR 0/12", "Hogares con ingreso válido",
                   "Tasa de exclusión", "Hogares Corrientes (crudo)", "Hogares Corrientes (tras filtro)",
                   "Personas Corrientes (tras filtro)", "Personas país (tras filtro)"]
        st.dataframe(t, width="stretch")
    st.markdown("Bases: `usu_individual_T424.xlsx` y `usu_individual_T425.xlsx` (INDEC, EPH continua). "
                "Filtro: DECCFR 1-10. Corrientes: AGLOMERADO = 12. Ver README para fórmulas, supuestos y límites.")

st.caption("Fuente: INDEC, Encuesta Permanente de Hogares. Elaboración propia.")
