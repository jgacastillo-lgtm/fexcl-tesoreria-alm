import streamlit as st
import pandas as pd
import datetime

# 1. Configuración básica de la página
st.set_page_config(page_title="FEXCL Control de Tesorería", page_icon="📈", layout="wide")
st.title("📊 FEX CAPITAL Loans - Módulo de Tesorería y ALM")
st.markdown("Proyección de Liquidez y Calce de Plazos (Activo vs Pasivo)")

# 2. Barra lateral (Sidebar) para los controles y supuestos del modelo
st.sidebar.header("⚙️ Supuestos del Modelo")

# Control de Tasa de Morosidad (NPL)
st.sidebar.subheader("Riesgo de Cartera")
tasa_morosidad = st.sidebar.slider(
    "Tasa de Morosidad (Impago proyectado en el Activo)", 
    min_value=0.0, 
    max_value=30.0, 
    value=5.0, 
    step=0.5,
    format="%f%%"
) / 100.0

# 3. Módulo de Renovación de Fondeadores (Roll-over)
st.sidebar.subheader("Salidas No Renovadas (Pasivo)")
st.sidebar.markdown("Ingresa el monto de capital que **sabes que NO se va a renovar** por mes (retiro real de liquidez).")

# Generamos dinámicamente los próximos 6 meses para el input manual
meses_proyectados = [(datetime.date.today() + pd.DateOffset(months=i)).strftime('%Y-%m') for i in range(6)]
salidas_reales = {}

for mes in meses_proyectados:
    salidas_reales[mes] = st.sidebar.number_input(
        f"Retiro de Capital en {mes} ($)", 
        min_value=0.0, 
        value=0.0, 
        step=50000.0,
        format="%f"
    )

# 4. Esqueleto de las Pestañas de Visualización
tab1, tab2, tab3 = st.tabs(["Balance Proyectado", "Flujos del Activo", "Flujos del Pasivo"])

with tab1:
    st.subheader("Liquidez Neta Acumulada")
    st.info("Aquí insertaremos la gráfica principal que cruzará las entradas menos las salidas, aplicando la morosidad del {}%".format(tasa_morosidad * 100))

with tab2:
    st.subheader("Entradas de Efectivo (Activo)")
    st.write("Aquí cargaremos y agruparemos la hoja de Activos.")

with tab3:
    st.subheader("Salidas de Efectivo (Pasivo)")
    st.write("Aquí programaremos el motor que calculará los cupones y vencimientos según el tipo de fondeo.")

# Por ahora, mostramos en pantalla el diccionario de los retiros manuales para confirmar que funciona
st.sidebar.divider()
st.sidebar.write("Resumen de Retiros Manuales:", salidas_reales)
