# Dinámica de ingresos y desigualdad — EPH-INDEC (Grupo 5)

Dashboard en Streamlit que compara el **Gini** y la **distribución por deciles del IPCF**
entre el **T4 2024** y el **T4 2025**, para el **aglomerado Corrientes** y el **total nacional**
(31 aglomerados), con microdatos públicos de la EPH del INDEC.

- Repositorio: https://github.com/ccelesteccoronel/dashboard-desigualdad-eph-FINAL
- App: https://dashboard-desigualdad-eph-final-akbx524qwawwjerelegvfc.streamlit.app/
- Datos: https://www.indec.gob.ar/indec/web/Institucional-Indec-BasesDeDatos

## 1. Fuente y fecha
Bases individuales EPH continua `usu_individual_T424.xlsx` (T4 2024) y `usu_individual_T425.xlsx`
(T4 2025), descargadas del INDEC el **[COMPLETAR: fecha de descarga]**. Versión del código: **[COMPLETAR: tag/commit]**.

## 2. Qué se transformó (flujo muestral, salida real del ETL)
| | T4 2024 | T4 2025 |
|---|---|---|
| Hogares en la base (país) | 16.511 | 15.534 |
| Excluidos por DECCFR 0 ó 12 (sin ingreso válido) | 4.280 (25,9%) | 3.976 (25,6%) |
| Hogares con ingreso válido | 12.231 | 11.558 |
| Corrientes (AGLOMERADO 12): hogares crudos → tras filtro | 346 → 342 | 320 → 319 |
| Corrientes: personas tras filtro | 1.040 | 957 |

Un cuarto de los hogares queda fuera por el filtro (criterio estándar del INDEC) y la muestra de
Corrientes es chica (~320-340 hogares): esto explica los intervalos de confianza anchos.

## 3. Resultados verificados
El Gini puntual se reprodujo desde los microdatos y coincide con las planillas de trabajo
(`Gini_EPH_2024_2025.xlsx`). El Gini Nacional calculado (0,4283 / 0,4248) queda a ~0,002 del publicado
por el INDEC para el total urbano (0,430 / 0,427).

| Gini | T4 2024 [IC 95%] | T4 2025 [IC 95%] |
|---|---|---|
| Nacional | 0,4283 [0,417 ; 0,439] | 0,4248 [0,412 ; 0,438] |
| Corrientes | 0,3306 [0,297 ; 0,361] | 0,3358 [0,305 ; 0,363] |

**Los intervalos se solapan en ambos recortes: no hay cambio detectable.** El +0,0052 de Corrientes
es indistinguible de ruido muestral.

Lo que sí muestra la distribución (Corrientes): el decil 10 baja de 26,2% a 24,7% del ingreso,
D10/D5 baja de 3,48x a 3,07x, D10/D1 sube de 8,78x a 9,83x. En términos **reales** (IPC +31,4%), los deciles
1-3 y el 10 pierden poder adquisitivo (−16,5%, −10,2%, −6,2% y −6,5%) y los deciles 4 a 9 quedan
entre 0% y +8%: ganó el medio. Ratio de Palma 1,33 → 1,31.

## 4. Metodología
- **Variable:** `IPCF`. **Ponderador:** `PONDIH` (no `PONDERA`). **Unidad:** la persona (el IPCF se repite
  entre integrantes del hogar y cada persona pondera con `PONDIH`).
- **Filtro:** `DECCFR` entre 1 y 10. **Recorte:** `AGLOMERADO == 12`.
- **Gini (trapecio):** ordenar por IPCF; X = población acumulada, Y = ingreso acumulado (ambos como proporción);
  `Gini = 1 − Σ (Xᵢ − Xᵢ₋₁)(Yᵢ + Yᵢ₋₁)`.
- **IC del Gini:** bootstrap de **hogares** (unidad de muestreo), 1.000 repeticiones, percentiles 2,5 y 97,5.
- **Deciles locales:** agrupar por valor de IPCF sumando `PONDIH`, acumular población y cortar en 10 tramos
  (`decil = ceil(pob_acum × 10)`); no se usa el `DECCFR` nacional.
- **Brechas:** `D10/D1`, `D9/D1`, `D10/D5` = IPCF medio del decil ÷ IPCF medio del decil de referencia.
- **Palma:** % del ingreso del decil 10 ÷ % del ingreso de los deciles 1-4.
- **Diferencia de Lorenz:** Lorenz(2025) − Lorenz(2024) sobre una grilla común de 101 percentiles.
- **Ingresos reales:** `IPCF / (IPC_T4 / IPC_T4 2024)`. IPC nacional nivel general (base dic-2016=100), promedio
  oct-nov-dic: T4 2024 = 7.499,80 (serie oficial); T4 2025 = 9.853,89 (**reconstruido** desde dic-2024 con las
  variaciones publicadas: dic/dic +31,5%, dic +2,8% m/m, nov +2,5% m/m; error < 0,1%). Verificar contra la
  serie oficial (datos.gob.ar, serie `148.3_INIVELNAL_DICI_M_26`).
- **Composición del ingreso:** laboral = `P21 + TOT_P12`; jubilaciones = `V2*`; transferencias = `V4, V5*, V21*, V22*`;
  otros = `T_VI` menos lo anterior. Los códigos negativos (−9) se tratan como 0.
- **Theil GE(1)** descompuesto entre/dentro de formales e informales (`PP07H`), solo sobre ocupados con respuesta válida.

## 5. Límites
1. Dos trimestres no permiten hablar de tendencia (falta una serie de más trimestres; requiere descargar T1-T3 2025 y
   anteriores de la EPH).
2. Muestra chica en Corrientes; los porcentajes por decil y la variación real son orden de magnitud.
3. 25% de los hogares no tienen ingreso válido; si su no respuesta no es aleatoria, hay sesgo.
4. El IPC es nacional; el de la región Noreste sería más apropiado para Corrientes.
5. **Incidencia de pobreza: pendiente.** Falta cargar la CBT (NEA/nacional, promedio del trimestre) en `CBT_PER_CAPITA`;
   además usa IPCF vs CBT per cápita sin adulto equivalente (aproximación, no cifra oficial).
6. El Theil no tiene intervalo de confianza; su caída entre trimestres puede ser ruido.
7. Los ingresos EPH refieren al mes anterior a la entrevista; usar el promedio oct-dic es una aproximación
   (con sep-nov el factor sería 1,315: diferencia irrelevante).

## 6. Reproducir
```bash
pip install -r requirements.txt
# colocar las bases en data/raw/ (no se versionan)
python src/etl.py --t4_2024 data/raw/usu_individual_T424.xlsx --t4_2025 data/raw/usu_individual_T425.xlsx
streamlit run app.py
```
El ETL genera en `data/processed/` los CSV que lee `app.py` (que no contiene números escritos a mano,
salvo las dos cifras de referencia del INDEC, comentadas en el código).

## 7. Despliegue (Streamlit Community Cloud)
Subir el repo a GitHub con `data/processed/*.csv` incluidos → share.streamlit.io → New app → `app.py`.
