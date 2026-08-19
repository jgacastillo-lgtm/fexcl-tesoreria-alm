import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection
import datetime

st.set_page_config(page_title="FEXCL Tesorería", layout="wide")
st.title("📊 FEX CAPITAL Loans - Módulo de Tesorería y ALM")

# --- 1. CONEXIÓN A GOOGLE SHEETS ---
# Pega aquí la URL completa de tu archivo "Tesoreria FEXCL"
URL_SHEET = "https://docs.google.com/spreadsheets/d/1MYRlXR03vz5T8bw-g-14Tr6LkGERFXIxTUeL_CwxydE/edit?usp=sharing"

conn = st.connection("gsheets", type=GSheetsConnection)

try:
    # Leemos las dos hojas (ajusta los nombres si tus pestañas se llaman distinto)
    df_activo = conn.read(spreadsheet=URL_SHEET, worksheet="Activo")
    df_pasivo = conn.read(spreadsheet=URL_SHEET, worksheet="Pasivo")
    st.success("¡Datos de FEX CAPITAL Loans conectados exitosamente!")
except Exception as e:
    st.error(f"Error al conectar con Google Sheets. Revisa tus Secrets. Detalle: {e}")
    st.stop()

# --- 2. MOSTRAR DATOS CRUDOS ---
tab1, tab2, tab3 = st.tabs(["Balance Proyectado", "Flujos del Activo", "Flujos del Pasivo"])

with tab1:
    st.write("Próximamente: Cálculos de Liquidez ALM")

with tab2:
    st.subheader("Base de Datos - Activo (Nuestra Cartera)")
    st.dataframe(df_activo.head(10)) # Mostramos solo 10 para probar

with tab3:
    st.subheader("Base de Datos - Pasivo (Nuestros Fondeadores)")
    st.dataframe(df_pasivo)
