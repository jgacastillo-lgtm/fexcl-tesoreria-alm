import streamlit as st
import pandas as pd
import datetime
import plotly.graph_objects as go
from streamlit_gsheets import GSheetsConnection

# ==========================================
# 1. CONFIGURACIÓN DE LA PÁGINA
# ==========================================
st.set_page_config(page_title="FEXCL Tesorería", page_icon="📈", layout="wide")
st.title("📊 FEX CAPITAL Loans - Módulo de Tesorería y ALM")
st.markdown("Centro de Control de Liquidez: Proyección de Activos vs Fondeadores")

# ==========================================
# 2. BARRA LATERAL (CONTROLES Y RIESGO)
# ==========================================
st.sidebar.header("⚙️ Supuestos del Modelo")

st.sidebar.subheader("Riesgo de Cartera")
tasa_morosidad = st.sidebar.slider(
    "Tasa de Morosidad (Impago en Activo)", 
    min_value=0.0, max_value=30.0, value=5.0, step=0.5, format="%f%%"
) / 100.0

st.sidebar.subheader("Salidas No Renovadas (Pasivo)")
st.sidebar.markdown("Capital que sabemos que NO se renovará (Afecta Liquidez):")
# Proyectamos 6 meses a partir de hoy (Agosto 2026)
meses_proyectados = [(datetime.date.today() + pd.DateOffset(months=i)).strftime('%Y-%m') for i in range(6)]
salidas_reales = {}
for mes in meses_proyectados:
    salidas_reales[mes] = st.sidebar.number_input(f"Retiro en {mes} ($)", min_value=0.0, value=0.0, step=50000.0)

# ==========================================
# 3. CONEXIÓN A GOOGLE SHEETS Y LIMPIEZA
# ==========================================
# IMPORTANTE: Mantén aquí tu URL real de "Tesoreria FEXCL"
URL_SHEET = "https://docs.google.com/spreadsheets/d/1MYRlXR03vz5T8bw-g-14Tr6LkGERFXIxTUeL_CwxydE/edit?usp=sharing"

def limpiar_numeros(df, columnas):
    """Limpia formatos de moneda y texto para convertirlos a números matemáticos puros."""
    for col in columnas:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(r'[$,% ]', '', regex=True), 
                errors='coerce'
            ).fillna(0)
    return df

@st.cache_data(ttl=600)
def cargar_datos_sheets():
    conn = st.connection("gsheets", type=GSheetsConnection)
    df_act = conn.read(spreadsheet=URL_SHEET, worksheet="Activo")
    df_pas = conn.read(spreadsheet=URL_SHEET, worksheet="Pasivo")
    
    df_act = limpiar_numeros(df_act, ['Capital', 'Interés', 'Total'])
    df_pas = limpiar_numeros(df_pas, ['Monto de Inversión', 'Monto Cupón', '% Rendimiento'])
    
    return df_act, df_pas

try:
    df_activo, df_pasivo = cargar_datos_sheets()
except Exception as e:
    st.error(f"Error de conexión con Google Sheets: {e}")
    st.stop()

# ==========================================
# 4. MOTORES DE PROYECCIÓN (ALM)
# ==========================================
# --- Motor del Pasivo ---
def proyectar_flujos_pasivo(df):
    flujos = []
    df['Fecha de inicio'] = pd.to_datetime(df['Fecha de inicio'], errors='coerce')
    df['Fecha de vencimiento'] = pd.to_datetime(df['Fecha de vencimiento'], errors='coerce')
    
    for index, row in df.iterrows():
        fondeador = row['Fondeador']
        monto_inv = row.get('Monto de Inversión', 0)
        tipo_pago = row.get('Pago Rendimiento', '')
        inicio = row['Fecha de inicio']
        fin = row['Fecha de vencimiento']
        
        tasa = row.get('% Rendimiento', 0)
        if isinstance(tasa, (int, float)) and tasa > 1:
            tasa = tasa / 100.0
            
        if pd.notna(fin) and tipo_pago != 'Amortización':
            flujos.append({'Fecha': fin, 'Fondeador': fondeador, 'Concepto': 'Capital', 'Monto': monto_inv})
        
        if tipo_pago == 'Mensual' and pd.notna(inicio) and pd.notna(fin):
            cupon = row.get('Monto Cupón', 0)
            fechas_pago = pd.date_range(start=inicio, end=fin, freq='MS') 
            for fecha in fechas_pago:
                if fecha > inicio and fecha <= fin:
                    flujos.append({'Fecha': fecha, 'Fondeador': fondeador, 'Concepto': 'Interés', 'Monto': cupon})
                    
        elif tipo_pago == 'Al termino' and pd.notna(fin):
            interes_total = monto_inv * tasa 
            flujos.append({'Fecha': fin, 'Fondeador': fondeador, 'Concepto': 'Interés', 'Monto': interes_total})
            
        elif tipo_pago == 'Amortización' and pd.notna(inicio) and pd.notna(fin):
            fechas_pago = pd.date_range(start=inicio, end=fin, freq='MS')
            try:
                dia = int(str(row.get('Día pago cupón', '1')).replace('.0', '').strip())
                fechas_pago = fechas_pago.map(lambda x: x.replace(day=min(dia, x.days_in_month)))
            except:
                pass
            
            fechas_validas = [f for f in fechas_pago if f > inicio and f <= fin]
            num_pagos = len(fechas_validas)
            
            if num_pagos > 0:
                capital_mensual = monto_inv / num_pagos
                saldo_insoluto = monto_inv
                cupon = row.get('Monto Cupón', 0)
                
                for fecha in fechas_validas:
                    flujos.append({'Fecha': fecha, 'Fondeador': fondeador, 'Concepto': 'Amortización Capital', 'Monto': capital_mensual})
                    if cupon > 0:
                        flujos.append({'Fecha': fecha, 'Fondeador': fondeador, 'Concepto': 'Interés', 'Monto': cupon})
                    elif tasa > 0:
                        interes_mes = saldo_insoluto * (tasa / 12)
                        flujos.append({'Fecha': fecha, 'Fondeador': fondeador, 'Concepto': 'Interés (S.I.)', 'Monto': interes_mes})
                    
                    saldo_insoluto -= capital_mensual

    df_flujos = pd.DataFrame(flujos)
    if not df_flujos.empty:
        df_flujos['Mes-Año'] = df_flujos['Fecha'].dt.to_period('M').astype(str)
    return df_flujos

# --- Motor del Activo ---
def proyectar_flujos_activo(df, morosidad):
    df['Fecha Fin'] = pd.to_datetime(df['Fecha Fin'], errors='coerce')
    df = df.dropna(subset=['Fecha Fin']).copy()
    df['Mes-Año'] = df['Fecha Fin'].dt.to_period('M').astype(str)
    
    df['Cobro Esperado'] = df['Total'] * (1 - morosidad)
    flujos_mensuales = df.groupby('Mes-Año')['Cobro Esperado'].sum().reset_index()
    flujos_mensuales.rename(columns={'Cobro Esperado': 'Entradas (Activo)'}, inplace=True)
    return flujos_mensuales

df_flujos_pasivo = proyectar_flujos_pasivo(df_pasivo)
df_flujos_activo = proyectar_flujos_activo(df_activo, tasa_morosidad)

# --- Consolidación Mensual ---
if not df_flujos_pasivo.empty:
    salidas_mensuales = df_flujos_pasivo.groupby('Mes-Año')['Monto'].sum().reset_index()
    salidas_mensuales.rename(columns={'Monto': 'Salidas (Pasivo)'}, inplace=True)
else:
    salidas_mensuales = pd.DataFrame(columns=['Mes-Año', 'Salidas (Pasivo)'])

df_alm = pd.merge(df_flujos_activo, salidas_mensuales, on='Mes-Año', how='outer').fillna(0)

# Sumamos los retiros manuales
for mes, retiro in salidas_reales.items():
    if mes in df_alm['Mes-Año'].values:
        df_alm.loc[df_alm['Mes-Año'] == mes, 'Salidas (Pasivo)'] += retiro
    elif retiro > 0:
        nueva_fila = pd.DataFrame({'Mes-Año': [mes], 'Entradas (Activo)': [0], 'Salidas (Pasivo)': [retiro]})
        df_alm = pd.concat([df_alm, nueva_fila], ignore_index=True)

df_alm = df_alm.sort_values('Mes-Año')
df_alm['Flujo Neto'] = df_alm['Entradas (Activo)'] - df_alm['Salidas (Pasivo)']
df_alm['Liquidez Acumulada'] = df_alm['Flujo Neto'].cumsum()

# ==========================================
# 5. DASHBOARD Y VISUALIZACIÓN
# ==========================================
tab1, tab2, tab3 = st.tabs(["📊 Centro de Mando (Dashboard)", "📥 Detalle Activo", "📤 Detalle Pasivo"])

with tab1:
    st.header("Indicadores Clave (KPIs)")
    
    total_activo = df_activo['Capital'].sum() if 'Capital' in df_activo.columns else 0
    total_pasivo = df_pasivo['Monto de Inversión'].sum() if 'Monto de Inversión' in df_pasivo.columns else 0
    flujo_proximo_mes = df_alm['Flujo Neto'].iloc[0] if not df_alm.empty else 0
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Cartera Viva (Activo)", f"${total_activo:,.2f}")
    col2.metric("Fondeo Total (Pasivo)", f"${total_pasivo:,.2f}")
    col3.metric("Tasa Morosidad", f"{tasa_morosidad*100:.1f}%")
    col4.metric("Flujo Neto (Próx. Mes)", f"${flujo_proximo_mes:,.2f}")

    st.divider()
    st.subheader("Gráfica de Liquidez Mensual (Calce de Plazos)")
    
    if not df_alm.empty:
        # --- NUEVO: Control de Rango de Fechas ---
        meses_disponibles = sorted(df_alm['Mes-Año'].unique())
        
        if len(meses_disponibles) > 1:
            mes_inicio, mes_fin = st.select_slider(
                "Selecciona el horizonte de tiempo que deseas analizar:",
                options=meses_disponibles,
                value=(meses_disponibles[0], meses_disponibles[-1])
            )
            # Filtramos el dataframe base según la selección del usuario
            df_grafica = df_alm[(df_alm['Mes-Año'] >= mes_inicio) & (df_alm['Mes-Año'] <= mes_fin)]
        else:
            df_grafica = df_alm
            
        # Graficamos con los datos filtrados
        fig = go.Figure()
        fig.add_trace(go.Bar(x=df_grafica['Mes-Año'], y=df_grafica['Entradas (Activo)'], name='Entradas Esperadas (Cobranza)', marker_color='#2ca02c'))
        fig.add_trace(go.Bar(x=df_grafica['Mes-Año'], y=-df_grafica['Salidas (Pasivo)'], name='Salidas Proyectadas (Fondeadores)', marker_color='#d62728'))
        fig.add_trace(go.Scatter(x=df_grafica['Mes-Año'], y=df_grafica['Flujo Neto'], name='Flujo Neto del Mes', mode='lines+markers', line=dict(color='black', width=3)))
        
        fig.update_layout(barmode='relative', title=f"Entradas vs Salidas Proyectadas ({mes_inicio} a {mes_fin})", xaxis_title="Mes", yaxis_title="Monto ($)")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No hay datos para graficar.")

with tab2:
    st.subheader("Base de Datos - Entradas de Efectivo (Activo)")
    st.dataframe(df_activo, use_container_width=True)

with tab3:
    st.subheader("Base de Datos - Salidas Proyectadas (Pasivo)")
    if not df_flujos_pasivo.empty:
        st.dataframe(df_flujos_pasivo.sort_values('Fecha'), use_container_width=True)
