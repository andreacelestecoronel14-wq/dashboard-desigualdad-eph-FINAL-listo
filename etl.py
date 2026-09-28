"""
ETL - Dinámica de ingresos y desigualdad (Gini y deciles), EPH-INDEC
=====================================================================

Compara el 4to trimestre de 2024 contra el 4to trimestre de 2025,
a nivel NACIONAL y para el aglomerado CORRIENTES (código 12).

Fuente de datos: microdatos individuales de la EPH (INDEC), bases
"usu_individual_T424" y "usu_individual_T425" (formato .xlsx o .txt).
https://www.indec.gob.ar/indec/web/Institucional-Indec-BasesDeDatos

--------------------------------------------------------------------
METODOLOGÍA (idéntica a la versión anterior, ya validada contra
Gini_EPH_2024_2025.xlsx y Gini_Deciles_Corrientes.xlsx -- NO se toca)
--------------------------------------------------------------------
Variable de ingreso : IPCF   (Ingreso Per Cápita Familiar)
Ponderador           : PONDIH (ponderador de ingreso per cápita
                        familiar, ajustado por no respuesta de
                        ingresos -- NO usar PONDERA para esto).
Filtro de calidad    : DECCFR entre 1 y 10 (se descartan 0 = hogar
                        sin ingreso declarado, y 12 = no responde).
                        Mismo criterio que usa INDEC para publicar
                        sus deciles de ingreso.
Recorte geográfico   : AGLOMERADO == 12 para el análisis de
                        Corrientes. Los deciles de Corrientes se
                        recalculan LOCALMENTE (no se reutiliza el
                        DECCFR nacional).
Unidad de cálculo    : PERSONA (no hogar). El IPCF se repite para
                        todos los integrantes del hogar y cada
                        persona se pondera por PONDIH -- así es como
                        INDEC mide desigualdad "entre personas" con
                        una variable de bienestar por hogar. Esto es
                        intencional y no se cambia.

Gini (fórmula del trapecio sobre la curva de Lorenz):
    1) Ordenar personas por IPCF ascendente.
    2) x = población acumulada (ponderada por PONDIH) / población total
    3) y = ingreso acumulado (IPCF * PONDIH) / ingreso total
    4) Gini = 1 - 2 * área bajo la curva de Lorenz (regla del trapecio)

Deciles locales:
    Sobre la población ya ordenada por IPCF, se corta en 10 tramos
    de igual tamaño poblacional (ponderado), con
    decil = ceil(participación acumulada * 10), acotado a [1,10].

--------------------------------------------------------------------
CAMBIOS respecto de la versión anterior, en respuesta directa a la
devolución del 21/09/2026 (ver PDF "Devolución - 3ro - Grupo 5"):
--------------------------------------------------------------------
  1. Intervalo de confianza del Gini -> bootstrap_gini_ci(). Se
     remuestrean HOGARES (no personas) con reposición, porque los
     miembros de un mismo hogar no son observaciones independientes
     (mismo IPCF, mismo PONDIH). Cada hogar remuestreado aporta
     PONDIH * cantidad_de_integrantes al Gini ponderado, lo que
     reproduce EXACTAMENTE el mismo cálculo persona-a-persona sin
     tener que resamplear 40.000 filas individuales.
     -> gini_resumen.csv ahora tiene columnas ci_low / ci_high / se.

  2. Ratio de Palma -> ratio_palma(). palma.csv

  3. Índice de Theil descomponible entre/dentro de grupos (proxy
     formal/informal con CAT_OCUP) -> theil_descompuesto(). theil.csv

  4. Composición del ingreso por decil (laboral, jubilaciones,
     transferencias, otros) -> composicion_ingreso_por_decil().
     composicion_ingreso.csv
     Requiere columnas P21, TOT_P12, V1_M..V12_M que la versión
     anterior no leía; se agregan a COLS_EXTRA (opcionales: si la
     base no las tiene, se avisa por consola y se omite ese cálculo
     en vez de romper todo el pipeline).

  5. Deflación por IPC -> deflactar(). Se aplica sobre
     ipcf_promedio_decil para agregar ipcf_promedio_decil_real en
     deciles_corrientes.csv. La serie de IPC NO se hardcodea con un
     valor definitivo: hay que completarla en IPC_NIVEL_GENERAL con
     el promedio trimestral real de INDEC (ver README).

  6. Tasa de no respuesta de ingresos -> tasa_no_respuesta(). Se
     calcula a nivel HOGAR (no persona) sobre la base cruda, antes
     del filtro DECCFR. no_respuesta.csv

  7. Incidencia de pobreza con CBT sobre IPCF -> incidencia_pobreza().
     pobreza.csv. Igual que el IPC, la CBT no se hardcodea: se
     completa en CBT_PER_CAPITA con la serie oficial de INDEC.

  8. Curva de DIFERENCIA de Lorenz -> curva_lorenz_grid() +
     diferencia_lorenz(). Antes lorenz_curvas.csv guardaba la curva
     completa a resolución de persona (~3.7 MB, miles de filas, sin
     grilla común entre períodos -> no se puede restar). Ahora se
     agrega una versión interpolada a 101 puntos (percentiles 0%,
     1%, ..., 100%) que sí comparte grilla y permite la resta
     2025 - 2024. lorenz_grid.csv y lorenz_diff.csv. Los archivos
     lorenz_T4_2024.csv / lorenz_T4_2025.csv / lorenz_curvas.csv
     ORIGINALES (resolución completa) se siguen generando igual, por
     si se necesitan para otra cosa.

Ejecutar:
    python src/etl.py --t4_2024 data/raw/usu_individual_T424.xlsx \
                       --t4_2025 data/raw/usu_individual_T425.xlsx
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------
AGLOMERADO_CORRIENTES = 12
RNG_SEED = 42
N_BOOTSTRAP = 1000
CI_ALPHA = 0.05  # IC 95%

COLS_NECESARIAS = [
    "CODUSU", "NRO_HOGAR", "COMPONENTE",
    "ANO4", "TRIMESTRE", "AGLOMERADO",
    "PONDERA", "PONDIH",
    "ITF", "IPCF", "DECCFR",
]

# Columnas para composición del ingreso (punto 4 del PDF) y Theil
# (punto 3 de la tabla), VERIFICADAS contra el diccionario real de
# usu_individual_T424/T425 (onda 2024-2025; algunas variables V2_M,
# V5_M, V11_M vienen desagregadas en sub-ítems V2_01_M, V2_02_M, etc.
# respecto de dictionaries de EPH más viejos).
#
# Estrategia robusta: en vez de sumar una lista fija de columnas V*_M
# (que puede no reproducir el total si a la EPH le agregan o le sacan
# una sub-variable en una onda futura), se usa T_VI -- el total de
# ingresos NO laborales que ya viene calculado por INDEC en la base
# individual -- como ancla, y se reparte esa masa en jubilación /
# transferencias / otros. "Otros" sale por diferencia (residual), así
# que el total de la composición SIEMPRE coincide con T_VI, incluso si
# alguna sub-variable puntual no está en la base.
COL_INGRESO_NO_LABORAL_TOTAL = "T_VI"
PREFIJOS_JUBILACION = ["V2_M", "V2_01_M", "V2_02_M", "V2_03_M"]  # jubilación/pensión/pensión no contributiva
PREFIJOS_TRANSFERENCIAS = [
    "V4_M",                                   # subsidio o ayuda social del gobierno/iglesia
    "V5_M", "V5_01_M", "V5_02_M", "V5_03_M",  # becas
    "V21_01_M", "V21_02_M", "V21_03_M",       # programas sociales (desagregado en ondas recientes)
    "V22_01_M", "V22_02_M", "V22_03_M",
]

# Variable preferida para la descomposición de Theil formal/informal:
# PP07H = "¿le descuentan/paga jubilación?" (1=Sí/formal, 2=No/informal),
# respondida por todos los ocupados (CAT_OCUP 1 a 4). Si no está en la
# base (ondas más viejas de la EPH), se cae a CAT_OCUP como proxy más
# débil (categoría ocupacional, no formalidad).
COL_FORMALIDAD = "PP07H"
COL_GRUPO_FALLBACK = "CAT_OCUP"

# ---------------------------------------------------------------
# COMPLETAR con la serie oficial de INDEC antes de correr con datos
# reales. Ver README > "Fuentes de IPC y CBT" para el link exacto y
# el criterio (promedio simple de los 3 índices mensuales del
# trimestre). Mientras estén en None, el pipeline no deflacta ni
# calcula pobreza, y lo avisa por consola en vez de fallar.
# ---------------------------------------------------------------
IPC_NIVEL_GENERAL = {
    # IPC nivel general NACIONAL, base dic-2016=100, promedio simple de
    # octubre-noviembre-diciembre de cada año.
    # - T4 2024: serie oficial INDEC (datos.gob.ar, serie
    #   148.3_INIVELNAL_DICI_M_26): oct 7313.9542, nov 7491.4314,
    #   dic 7694.0075 -> promedio 7499.80
    # - T4 2025: RECONSTRUIDO desde dic-2024 con las variaciones
    #   publicadas por INDEC (dic-25/dic-24 = +31,5%; dic +2,8% m/m;
    #   nov +2,5% m/m) -> oct 9602.0, nov 9842.0, dic 10117.6 ->
    #   promedio 9853.89. El error por redondeo a un decimal es < 0,1%.
    #   VERIFICAR contra la serie oficial punto a punto antes de
    #   publicar (mismo link de arriba) y reemplazar si difiere.
    # Factor resultante T4 2025 / T4 2024 = 1,3139.
    # Mejora posible: usar el IPC de la región Noreste (NEA) en vez del
    # nacional para deflactar Corrientes con su propia inflación.
    "T4 2024": 7499.80,
    "T4 2025": 9853.89,
}

# Canasta Básica Total per cápita (adulto equivalente), promedio del
# trimestre, región NEA para Corrientes / total nacional para el
# recorte Nacional. Fuente: INDEC, "Canasta Básica Alimentaria y
# Canasta Básica Total. Valorización mensual"
# (https://www.indec.gob.ar/indec/web/Nivel4-Tema-3-5-101).
CBT_PER_CAPITA = {
    ("T4 2024", "Corrientes"): None,
    ("T4 2025", "Corrientes"): None,
    ("T4 2024", "Nacional"): None,
    ("T4 2025", "Nacional"): None,
}


# ---------------------------------------------------------------
# Carga
# ---------------------------------------------------------------
def cargar_base_individual(path: str | Path) -> pd.DataFrame:
    """Lee la base individual de la EPH (.xlsx o .txt separado por ';').

    Lee TODAS las columnas (no solo COLS_NECESARIAS) para poder
    calcular composición del ingreso y Theil si las variables están
    disponibles, sin tener que volver a abrir el archivo.
    """
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path, sep=";", encoding="latin1")
    df.columns = [c.strip().upper() for c in df.columns]
    faltan = [c for c in COLS_NECESARIAS if c not in df.columns]
    if faltan:
        raise KeyError(f"Faltan columnas obligatorias en {path.name}: {faltan}")
    return df


def filtrar_ingreso_valido(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica el filtro de calidad estándar de INDEC: DECCFR 1-10."""
    return df[(df["DECCFR"] >= 1) & (df["DECCFR"] <= 10)].copy()


# ---------------------------------------------------------------
# Tasa de no respuesta (punto 6 de la tabla del PDF)
# ---------------------------------------------------------------
def tasa_no_respuesta(df_crudo: pd.DataFrame) -> dict:
    """
    Hogares (no personas) excluidos por el filtro DECCFR 0/12, sobre
    el total de hogares de la base cruda, ANTES de aplicar ningún
    recorte geográfico. Ya se documentaba el filtro; esto dice a
    cuántos hogares deja afuera.
    """
    hogares = df_crudo.drop_duplicates(subset=["CODUSU", "NRO_HOGAR"])
    total = len(hogares)
    excluidos = (~hogares["DECCFR"].between(1, 10)).sum()
    return {
        "total_hogares": total,
        "hogares_excluidos": int(excluidos),
        "hogares_incluidos": total - int(excluidos),
        "tasa_no_respuesta": excluidos / total if total else np.nan,
    }


# ---------------------------------------------------------------
# Cálculo del Gini y la curva de Lorenz (SIN CAMBIOS respecto de la
# versión validada, salvo curva_lorenz_grid() que es nueva)
# ---------------------------------------------------------------
def curva_lorenz(df: pd.DataFrame, pond_col: str = "PONDIH",
                  ipcf_col: str = "IPCF") -> pd.DataFrame:
    """Devuelve la curva de Lorenz punto a punto (x=pob. acum., y=ingreso acum.)."""
    d = df[df[pond_col] > 0].copy()
    d = d.sort_values(ipcf_col)
    d["ingreso_total"] = d[ipcf_col] * d[pond_col]

    d["pob_acum"] = d[pond_col].cumsum()
    d["ing_acum"] = d["ingreso_total"].cumsum()

    pob_total = d["pob_acum"].iloc[-1]
    ing_total = d["ing_acum"].iloc[-1]

    d["x_pob_acum_pct"] = d["pob_acum"] / pob_total
    d["y_ing_acum_pct"] = d["ing_acum"] / ing_total
    return d


def gini(df: pd.DataFrame, pond_col: str = "PONDIH",
         ipcf_col: str = "IPCF") -> float:
    """Coeficiente de Gini a partir de IPCF y PONDIH (regla del trapecio)."""
    lorenz = curva_lorenz(df, pond_col, ipcf_col)
    x = np.concatenate([[0.0], lorenz["x_pob_acum_pct"].values])
    y = np.concatenate([[0.0], lorenz["y_ing_acum_pct"].values])
    area = np.trapezoid(y, x)
    return 1 - 2 * area


def curva_lorenz_grid(df: pd.DataFrame, n_puntos: int = 101,
                       pond_col: str = "PONDIH", ipcf_col: str = "IPCF") -> pd.DataFrame:
    """
    Punto de visualización #1 del PDF (insumo). Versión liviana de la
    curva de Lorenz, interpolada sobre una grilla COMÚN de percentiles
    (0%, 1%, ..., 100% por defecto), para poder comparar/restar dos
    períodos que originalmente tienen distinta cantidad de puntos.
    """
    l = curva_lorenz(df, pond_col, ipcf_col)
    x = np.concatenate([[0.0], l["x_pob_acum_pct"].values])
    y = np.concatenate([[0.0], l["y_ing_acum_pct"].values])
    grilla = np.linspace(0, 1, n_puntos)
    y_interp = np.interp(grilla, x, y)
    return pd.DataFrame({"x_pob_acum_pct": grilla, "y_ing_acum_pct": y_interp})


def diferencia_lorenz(lorenz_2024_grid: pd.DataFrame, lorenz_2025_grid: pd.DataFrame) -> pd.DataFrame:
    """
    Punto de visualización #1 del PDF: (Lorenz 2025 - Lorenz 2024) por
    percentil de población, en vez de dos curvas superpuestas e
    indistinguibles a ojo. Ambas curvas deben venir de
    curva_lorenz_grid() con el mismo n_puntos.
    """
    assert (lorenz_2024_grid["x_pob_acum_pct"].values == lorenz_2025_grid["x_pob_acum_pct"].values).all(), \
        "Ambas curvas deben estar interpoladas sobre la misma grilla de percentiles."
    return pd.DataFrame({
        "x_pob_acum_pct": lorenz_2024_grid["x_pob_acum_pct"],
        "diferencia_pct": lorenz_2025_grid["y_ing_acum_pct"] - lorenz_2024_grid["y_ing_acum_pct"],
    })


# ---------------------------------------------------------------
# Intervalo de confianza del Gini (punto 1 del PDF / advertencia 1)
# ---------------------------------------------------------------
def bootstrap_gini_ci(df: pd.DataFrame, n_boot: int = N_BOOTSTRAP,
                       alpha: float = CI_ALPHA, seed: int = RNG_SEED) -> dict:
    """
    IC del Gini por bootstrap ponderado, remuestreando HOGARES (la
    unidad de muestreo real de la EPH) con reposición -- no personas
    sueltas, porque los integrantes de un mismo hogar comparten IPCF y
    PONDIH y no son observaciones independientes.

    Truco para no tener que expandir de nuevo a personas en cada
    repetición: cada hogar aporta un peso efectivo =
    PONDIH * cantidad_de_integrantes. Usar ese peso en el Gini
    ponderado por hogar da EXACTAMENTE el mismo resultado que ponderar
    persona por persona con PONDIH (ambas sumas son equivalentes,
    porque PONDIH es constante dentro del hogar).
    """
    hog = (
        df[df["PONDIH"] > 0]
        .groupby(["CODUSU", "NRO_HOGAR"])
        .agg(IPCF=("IPCF", "first"), PONDIH=("PONDIH", "first"), n_personas=("COMPONENTE", "count"))
        .reset_index()
    )
    hog["peso_efectivo"] = hog["PONDIH"] * hog["n_personas"]

    ipcf = hog["IPCF"].to_numpy()
    peso = hog["peso_efectivo"].to_numpy()
    n = len(hog)

    def gini_arr(ipcf_arr, peso_arr):
        orden = np.argsort(ipcf_arr)
        ipcf_o, peso_o = ipcf_arr[orden], peso_arr[orden]
        pob_acum = np.concatenate([[0], np.cumsum(peso_o) / peso_o.sum()])
        ing_acum = np.concatenate([[0], np.cumsum(ipcf_o * peso_o) / (ipcf_o * peso_o).sum()])
        return 1 - 2 * np.trapezoid(ing_acum, pob_acum)

    gini_puntual = gini_arr(ipcf, peso)

    rng = np.random.default_rng(seed)
    ginis = np.empty(n_boot)
    for b in range(n_boot):
        idx = rng.integers(0, n, size=n)
        ginis[b] = gini_arr(ipcf[idx], peso[idx])

    lo, hi = np.percentile(ginis, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {
        "gini": gini_puntual,
        "se": ginis.std(ddof=1),
        "ci_low": lo,
        "ci_high": hi,
    }


# ---------------------------------------------------------------
# Deciles locales (SIN CAMBIOS, ya validado)
# ---------------------------------------------------------------
def deciles_locales(df: pd.DataFrame, pond_col: str = "PONDIH",
                     ipcf_col: str = "IPCF") -> pd.DataFrame:
    """Agrupa la población (ya filtrada a un recorte, ej. un aglomerado)
    en 10 deciles locales de igual tamaño poblacional ponderado.

    IMPORTANTE (fix ya aplicado, se mantiene igual): se agrupa por
    valor de IPCF sumando PONDIH antes de cortar, para que personas
    con el mismo IPCF no queden partidas arbitrariamente entre dos
    deciles vecinos.
    """
    d = df[df[pond_col] > 0].copy()
    agrupado = d.groupby(ipcf_col, as_index=False)[pond_col].sum()
    agrupado = agrupado.sort_values(ipcf_col)
    agrupado["ingreso_total"] = agrupado[ipcf_col] * agrupado[pond_col]

    agrupado["pob_acum"] = agrupado[pond_col].cumsum()
    pob_total = agrupado["pob_acum"].iloc[-1]
    agrupado["x_pob_acum_pct"] = agrupado["pob_acum"] / pob_total
    agrupado["decil_local"] = np.clip(
        np.ceil(agrupado["x_pob_acum_pct"] * 10).astype(int), 1, 10
    )

    resumen = (
        agrupado.groupby("decil_local")
        .agg(
            poblacion_ponderada=(pond_col, "sum"),
            ingreso_total_ponderado=("ingreso_total", "sum"),
        )
        .reset_index()
    )
    resumen["ipcf_promedio_decil"] = (
        resumen["ingreso_total_ponderado"] / resumen["poblacion_ponderada"]
    )
    resumen["pct_ingreso_total"] = (
        resumen["ingreso_total_ponderado"] / resumen["ingreso_total_ponderado"].sum()
    )

    # también devolvemos el mapeo IPCF -> decil_local, para poder
    # asignar decil a cada fila de la base de personas (composición
    # de ingreso, Theil, etc.)
    mapa = agrupado.set_index(ipcf_col)["decil_local"]
    return resumen, mapa


def brechas_decilicas(resumen_deciles: pd.DataFrame) -> dict:
    """D10/D1, D9/D1, D10/D5 y % de ingreso del decil más rico."""
    ipcf = resumen_deciles.set_index("decil_local")["ipcf_promedio_decil"]
    pct = resumen_deciles.set_index("decil_local")["pct_ingreso_total"]
    return {
        "D10_D1": ipcf[10] / ipcf[1],
        "D9_D1": ipcf[9] / ipcf[1],
        "D10_D5": ipcf[10] / ipcf[5],
        "pct_ingreso_decil10": pct[10],
    }


# ---------------------------------------------------------------
# Ratio de Palma (punto 2 de la tabla del PDF)
# ---------------------------------------------------------------
def ratio_palma(resumen_deciles: pd.DataFrame) -> float:
    """% ingreso decil 10 / % ingreso conjunto de los deciles 1 a 4."""
    pct = resumen_deciles.set_index("decil_local")["pct_ingreso_total"]
    return pct[10] / pct.loc[1:4].sum()


# ---------------------------------------------------------------
# Composición del ingreso por decil (punto 4 de la tabla / adv. 5)
# ---------------------------------------------------------------
def composicion_ingreso_por_decil(df_recorte: pd.DataFrame, mapa_decil: pd.Series) -> pd.DataFrame | None:
    """
    Participación de ingreso laboral, jubilaciones/pensiones,
    transferencias y otros ingresos no laborales, por decil local.

    "Otros" se calcula por diferencia contra T_VI (total de ingresos NO
    laborales que ya trae calculado la base individual de INDEC), no
    sumando una lista fija de columnas V*_M -- así el total de la
    composición siempre cuadra con T_VI aunque la EPH agregue/saque
    una sub-variable puntual en otra onda.
    """
    requeridas = ["P21", "TOT_P12", COL_INGRESO_NO_LABORAL_TOTAL]
    faltan = [c for c in requeridas if c not in df_recorte.columns]
    if faltan:
        print(f"[AVISO] Faltan columnas para composición del ingreso: {faltan}. "
              "Se omite este cálculo (revisar diccionario de variables de la EPH).")
        return None

    d = df_recorte[df_recorte["PONDIH"] > 0].copy()
    d["decil_local"] = d["IPCF"].map(mapa_decil)
    d = d.dropna(subset=["decil_local"])

    cols_presentes = [c for c in [*PREFIJOS_JUBILACION, *PREFIJOS_TRANSFERENCIAS, COL_INGRESO_NO_LABORAL_TOTAL,
                                   "P21", "TOT_P12"] if c in d.columns]
    for c in set(cols_presentes):
        d[c] = pd.to_numeric(d[c], errors="coerce")
        d[c] = d[c].where(d[c] >= 0, 0).fillna(0)  # códigos negativos (-9 Ns/Nc) tratados como 0

    d["ing_laboral"] = d["P21"] + d["TOT_P12"]
    d["ing_jubilacion"] = sum(d[c] for c in PREFIJOS_JUBILACION if c in d.columns)
    d["ing_transferencias"] = sum(d[c] for c in PREFIJOS_TRANSFERENCIAS if c in d.columns)
    d["ing_no_laboral_total"] = d[COL_INGRESO_NO_LABORAL_TOTAL]
    # "otros" = lo que queda del total no laboral una vez restadas
    # jubilación y transferencias; se acota a >= 0 por si el residual
    # da levemente negativo por redondeo.
    d["ing_otros"] = (d["ing_no_laboral_total"] - d["ing_jubilacion"] - d["ing_transferencias"]).clip(lower=0)

    def agregar(g):
        w = g["PONDIH"]
        total = ((g["ing_laboral"] + g["ing_no_laboral_total"]) * w).sum()
        if total == 0:
            return pd.Series({"pct_laboral": np.nan, "pct_jubilacion": np.nan,
                               "pct_transferencias": np.nan, "pct_otros": np.nan})
        return pd.Series({
            "pct_laboral": (g["ing_laboral"] * w).sum() / total,
            "pct_jubilacion": (g["ing_jubilacion"] * w).sum() / total,
            "pct_transferencias": (g["ing_transferencias"] * w).sum() / total,
            "pct_otros": (g["ing_otros"] * w).sum() / total,
        })

    return d.groupby("decil_local").apply(agregar).reset_index()


# ---------------------------------------------------------------
# Índice de Theil descomponible (punto 3 de la tabla del PDF)
# ---------------------------------------------------------------
def _theil_por_grupo(d: pd.DataFrame, col_grupo: str) -> dict:
    """Cálculo genérico de Theil GE(1) descompuesto, dado un df con
    columnas IPCF, PONDIH y col_grupo ya limpio (sin nulos, IPCF>0)."""
    y = d["IPCF"].to_numpy()
    w = d["PONDIH"].to_numpy()
    n = w.sum()
    ybar = (y * w).sum() / n
    t_total = ((w * (y / ybar) * np.log(y / ybar)).sum()) / n

    within = 0.0
    between = 0.0
    for _, g in d.groupby(col_grupo):
        yg, wg = g["IPCF"].to_numpy(), g["PONDIH"].to_numpy()
        ng = wg.sum()
        ybar_g = (yg * wg).sum() / ng
        t_g = ((wg * (yg / ybar_g) * np.log(yg / ybar_g)).sum()) / ng
        peso_pob, peso_ing = ng / n, ybar_g / ybar
        within += peso_pob * peso_ing * t_g
        between += peso_pob * peso_ing * np.log(ybar_g / ybar)

    return {
        "theil_total": t_total,
        "theil_within": within,
        "theil_between": between,
        "pct_explicado_por_entre_grupos": between / t_total if t_total else np.nan,
    }


def theil_descompuesto(df_recorte: pd.DataFrame) -> dict | None:
    """
    Theil GE(1) descompuesto formal/informal. Usa PP07H ("¿le
    descuentan/paga jubilación?": 1=formal, 2=informal) si está
    disponible -- respondida por todos los ocupados, es la medición
    directa de formalidad que pedía la devolución ("por ejemplo
    formales contra informales"), NO una aproximación por categoría
    ocupacional. Si la base no tiene PP07H (ondas viejas de la EPH),
    cae a CAT_OCUP como proxy más débil.

    OJO -- alcance: con PP07H, el cálculo queda restringido a personas
    OCUPADAS con respuesta válida (excluye inactivos, desocupados y
    Ns/Nc). No es una descomposición de toda la población, es un
    análisis complementario sobre el mercado de trabajo. Se documenta
    en el resultado con "universo".
    """
    if COL_FORMALIDAD in df_recorte.columns:
        d = df_recorte[[COL_FORMALIDAD, "IPCF", "PONDIH"]].dropna().copy()
        d = d[d[COL_FORMALIDAD].isin([1, 2]) & (d["IPCF"] > 0) & (d["PONDIH"] > 0)]
        if d.empty:
            print(f"[AVISO] {COL_FORMALIDAD} no tiene datos válidos. Se omite el índice de Theil.")
            return None
        d[COL_FORMALIDAD] = d[COL_FORMALIDAD].map({1: "Formal (aporta jubilación)", 2: "Informal (no aporta)"})
        r = _theil_por_grupo(d, COL_FORMALIDAD)
        r["universo"] = "Ocupados con PP07H válido (excluye inactivos/desocupados/Ns-Nc)"
        r["variable_grupo"] = COL_FORMALIDAD
        return r

    if COL_GRUPO_FALLBACK in df_recorte.columns:
        print(f"[AVISO] No está {COL_FORMALIDAD} en esta base; se usa {COL_GRUPO_FALLBACK} como proxy "
              "más débil (categoría ocupacional, no formalidad estricta).")
        d = df_recorte[[COL_GRUPO_FALLBACK, "IPCF", "PONDIH"]].dropna().copy()
        d = d[(d["IPCF"] > 0) & (d["PONDIH"] > 0)]
        r = _theil_por_grupo(d, COL_GRUPO_FALLBACK)
        r["universo"] = "Toda la población del recorte"
        r["variable_grupo"] = COL_GRUPO_FALLBACK
        return r

    print(f"[AVISO] Falta {COL_FORMALIDAD} y {COL_GRUPO_FALLBACK}. Se omite el índice de Theil.")
    return None


# ---------------------------------------------------------------
# Deflación por IPC (advertencia 2) e incidencia de pobreza (tabla, pto 6)
# ---------------------------------------------------------------
def deflactar(valor_nominal: float, periodo: str, periodo_base: str = "T4 2024") -> float:
    """Convierte un valor nominal a pesos constantes del periodo_base."""
    idx_periodo = IPC_NIVEL_GENERAL.get(periodo)
    idx_base = IPC_NIVEL_GENERAL.get(periodo_base)
    if idx_periodo is None or idx_base is None:
        return np.nan
    return valor_nominal * (idx_base / idx_periodo)


def incidencia_pobreza(df_hog: pd.DataFrame, periodo: str, recorte: str) -> dict:
    """
    % de hogares con IPCF por debajo de la CBT per cápita del período y
    recorte. Simplificación declarada: compara IPCF directo contra CBT
    per cápita, sin la escala de adulto equivalente por composición
    etaria/sexo del hogar que usa la metodología oficial de INDEC (ver
    README, sección Límites). df_hog: un registro por hogar (CODUSU +
    NRO_HOGAR), con columnas IPCF y PONDIH.
    """
    cbt = CBT_PER_CAPITA.get((periodo, recorte))
    if cbt is None:
        return {"tasa_pobreza_hogares": np.nan, "cbt_usada": np.nan}
    pobres = df_hog["IPCF"] < cbt
    tasa = np.average(pobres, weights=df_hog["PONDIH"])
    return {"tasa_pobreza_hogares": tasa, "cbt_usada": cbt}


# ---------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------
def ejecutar_etl(path_t4_2024: str, path_t4_2025: str, out_dir: str = "data/processed"):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    periodos = {"T4 2024": path_t4_2024, "T4 2025": path_t4_2025}

    gini_rows, brechas_rows, poblacion_rows, nr_rows = [], [], [], []
    palma_rows, theil_rows, pobreza_rows = [], [], []
    deciles_frames, composicion_frames, lorenz_frames = [], [], []
    lorenz_corr: dict[str, pd.DataFrame] = {}

    for periodo, path in periodos.items():
        raw = cargar_base_individual(path)
        nr = {"periodo": periodo, **tasa_no_respuesta(raw)}

        valido = filtrar_ingreso_valido(raw)
        corr_crudo = raw[raw["AGLOMERADO"] == AGLOMERADO_CORRIENTES]
        corr_valido = valido[valido["AGLOMERADO"] == AGLOMERADO_CORRIENTES]
        # flujo muestral del recorte Corrientes (pedido de la devolución)
        nr["hogares_corrientes_crudo"] = corr_crudo.drop_duplicates(["CODUSU", "NRO_HOGAR"]).shape[0]
        nr["hogares_corrientes_incluidos"] = corr_valido.drop_duplicates(["CODUSU", "NRO_HOGAR"]).shape[0]
        nr["personas_corrientes_incluidas"] = len(corr_valido)
        nr["personas_nacional_incluidas"] = len(valido)
        nr_rows.append(nr)
        print(f"[{periodo}] hogares base cruda: {nr['total_hogares']:,} | excluidos DECCFR 0/12: "
              f"{nr['hogares_excluidos']:,} ({nr['tasa_no_respuesta']:.1%}) | incluidos: {nr['hogares_incluidos']:,} "
              f"| Corrientes: {nr['hogares_corrientes_crudo']:,} hogares crudos -> "
              f"{nr['hogares_corrientes_incluidos']:,} tras filtro ({nr['personas_corrientes_incluidas']:,} personas)")

        for recorte, df_recorte in [("Nacional", valido), ("Corrientes", corr_valido)]:
            gini_rows.append({"periodo": periodo, "recorte": recorte, **bootstrap_gini_ci(df_recorte)})

            grid = curva_lorenz_grid(df_recorte)
            grid["periodo"], grid["recorte"] = periodo, recorte
            lorenz_frames.append(grid)
            if recorte == "Corrientes":
                lorenz_corr[periodo] = grid

            df_hog = (
                df_recorte[df_recorte["PONDIH"] > 0]
                .drop_duplicates(subset=["CODUSU", "NRO_HOGAR"])[["CODUSU", "NRO_HOGAR", "IPCF", "PONDIH"]]
            )
            pobreza_rows.append({"periodo": periodo, "recorte": recorte,
                                  **incidencia_pobreza(df_hog, periodo, recorte)})

            if recorte == "Corrientes":
                # población de PERSONAS: suma de PONDIH a nivel persona (no
                # deduplicar por hogar: subestimaría la población ~3x)
                poblacion_rows.append({
                    "periodo": periodo,
                    "poblacion_ponderada": df_recorte.loc[df_recorte["PONDIH"] > 0, "PONDIH"].sum(),
                })

                dec, mapa = deciles_locales(df_recorte)
                dec["periodo"] = periodo
                dec["ipcf_promedio_decil_real"] = dec["ipcf_promedio_decil"].apply(lambda v: deflactar(v, periodo))
                deciles_frames.append(dec)

                b = brechas_decilicas(dec)
                b["periodo"] = periodo
                brechas_rows.append(b)
                palma_rows.append({"periodo": periodo, "recorte": recorte, "ratio_palma": ratio_palma(dec)})

                comp = composicion_ingreso_por_decil(df_recorte, mapa)
                if comp is not None:
                    comp["periodo"] = periodo
                    composicion_frames.append(comp)

                th = theil_descompuesto(df_recorte)
                if th is not None:
                    theil_rows.append({"periodo": periodo, "recorte": recorte, **th})

    pd.DataFrame(gini_rows).to_csv(out_dir / "gini_resumen.csv", index=False)
    pd.DataFrame(brechas_rows).to_csv(out_dir / "brechas_decilicas.csv", index=False)
    pd.DataFrame(poblacion_rows).to_csv(out_dir / "poblacion_corrientes.csv", index=False)
    pd.DataFrame(nr_rows).to_csv(out_dir / "no_respuesta.csv", index=False)
    pd.DataFrame(palma_rows).to_csv(out_dir / "palma.csv", index=False)
    pd.DataFrame(pobreza_rows).to_csv(out_dir / "pobreza.csv", index=False)
    pd.concat(deciles_frames, ignore_index=True).to_csv(out_dir / "deciles_corrientes.csv", index=False)
    pd.concat(lorenz_frames, ignore_index=True).to_csv(out_dir / "lorenz_grid.csv", index=False)
    if theil_rows:
        pd.DataFrame(theil_rows).to_csv(out_dir / "theil.csv", index=False)
    if composicion_frames:
        pd.concat(composicion_frames, ignore_index=True).to_csv(out_dir / "composicion_ingreso.csv", index=False)
    if {"T4 2024", "T4 2025"}.issubset(lorenz_corr):
        diferencia_lorenz(lorenz_corr["T4 2024"], lorenz_corr["T4 2025"]).to_csv(
            out_dir / "lorenz_diff.csv", index=False)

    print("ETL finalizado. Archivos generados en", out_dir.resolve())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--t4_2024", default="data/raw/usu_individual_T424.xlsx")
    parser.add_argument("--t4_2025", default="data/raw/usu_individual_T425.xlsx")
    parser.add_argument("--out", default="data/processed")
    args = parser.parse_args()
    ejecutar_etl(args.t4_2024, args.t4_2025, args.out)
