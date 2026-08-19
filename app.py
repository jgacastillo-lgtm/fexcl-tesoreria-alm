import streamlit as st
import pandas as pd
import datetime
import plotly.graph_objects as go
from streamlit_gsheets import GSheetsConnection
from dateutil.relativedelta import relativedelta

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
meses_proyectados = [(datetime.date.today() + pd.DateOffset(months=i)).strftime('%Y-%m') for i in range(6)]
salidas_reales = {}
for mes in meses_proyectados:
    salidas_reales[mes] = st.sidebar.number_input(f"Retiro en {mes} ($)", min_value=0.0, value=0.0, step=50000.0)

# ==========================================
# 3. CONEXIÓN A GOOGLE SHEETS
# ==========================================
# IMPORTANTE: Mantén aquí tu URL real de "Tesoreria FEXCL"
URL_SHEET = "https://docs.google.com/spreadsheets/d/1MYRlXR03vz5T8bw-g-14Tr6LkGERFXIxTUeL_CwxydE/edit?usp=sharing"

@st.cache_data(ttl=600)
def cargar_datos_sheets():
    conn = st.connection("gsheets", type=GSheetsConnection)
    df_act = conn.read(spreadsheet=URL_SHEET, worksheet="Activo")
    df_pas = conn.read(spreadsheet=URL_SHEET, worksheet="Pasivo")
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
        
        # Obtenemos la tasa por si se necesita para intereses
        tasa = row.get('% Rendimiento', 0)
        if isinstance(tasa, str):
            tasa = float(tasa.replace('%', '')) / 100
        
        # 4.1 Regla: Devolución Total al Final (Para Mensual o Al término)
        if pd.notna(fin) and tipo_pago != 'Amortización':
            flujos.append({'Fecha': fin, 'Fondeador': fondeador, 'Concepto': 'Capital', 'Monto': monto_inv})
        
        # 4.2 Regla: Mensual (Solo Intereses)
        if tipo_pago == 'Mensual' and pd.notna(inicio) and pd.notna(fin):
            cupon = row.get('Monto Cupón', 0)
            fechas_pago = pd.date_range(start=inicio, end=fin, freq='MS') 
            for fecha in fechas_pago:
                if fecha > inicio and fecha <= fin:
                    flujos.append({'Fecha': fecha, 'Fondeador': fondeador, 'Concepto': 'Interés', 'Monto': cupon})
                    
        # 4.3 Regla: Al término
        elif tipo_pago == 'Al termino' and pd.notna(fin):
            interes_total = monto_inv * tasa 
            flujos.append({'Fecha': fin, 'Fondeador': fondeador, 'Concepto': 'Interés', 'Monto': interes_total})
            
        # 4.4 Regla: Amortización (NUEVO: Capital en partes iguales)
        elif tipo_pago == 'Amortización' and pd.notna(inicio) and pd.notna(fin):
            fechas_pago = pd.date_range(start=inicio, end=fin, freq='MS')
            
            # Ajustamos al día de pago si existe
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
                    # 1. Pago de Capital (parte igual)
                    flujos.append({'Fecha': fecha, 'Fondeador': fondeador, 'Concepto': 'Amortización de Capital', 'Monto': capital_mensual})
                    
                    # 2. Pago de Interés
                    if cupon > 0:
                        flujos.append({'Fecha': fecha, 'Fondeador': fondeador, 'Concepto': 'Interés', 'Monto': cupon})
                    elif tasa > 0:
                        # Interés sobre saldo insoluto asumiendo tasa anual
                        interes_mes = saldo_insoluto * (tasa / 12)
                        flujos.append({'Fecha': fecha, 'Fondeador': fondeador, 'Concepto': 'Interés (S.I.)', 'Monto': interes_mes})
                    
                    # Reducimos el saldo para el próximo mes
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
    
    # Aplicamos el castigo de morosidad a los cobros proyectados
    df['Cobro Esperado'] = df['Total'] * (1 - morosidad)
    flujos_mensuales = df.groupby('Mes-Año')['Cobro Esperado'].sum().reset_index()
    flujos_mensuales.rename(columns={'Cobro Esperado': 'Entradas (Activo)'}, inplace=True)
    return flujos_mensuales

df_flujos_pasivo = proyectar_flujos_pasivo(df_pasivo)
df_flujos_activo = proyectar_flujos_activo(df_activo, tasa_morosidad)

# --- Consolidación Mensual (Incluyendo retiros manuales) ---
if not df_flujos_pasivo.empty:
    salidas_mensuales = df_flujos_pasivo.groupby('Mes-Año')['Monto'].sum().reset_index()
    salidas_mensuales.rename(columns={'Monto': 'Salidas (Pasivo)'}, inplace=True)
else:
    salidas_mensuales = pd.DataFrame(columns=['Mes-Año', 'Salidas (Pasivo)'])

# Unimos Entradas y Salidas
df_alm = pd.merge(df_flujos_activo, salidas_mensuales, on='Mes-Año', how='outer').fillna(0)

# Sumamos los retiros manuales que pusiste en la barra lateral
for mes, retiro in salidas_reales.items():
    if mes in df_alm['Mes-Año'].values:
        df_alm.loc[df_alm['Mes-Año'] == mes, 'Salidas (Pasivo)'] += retiro
    elif retiro > 0:
        # Si el mes no existía en proyecciones, lo agregamos
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
        fig = go.Figure()
        fig.add_trace(go.Bar(x=df_alm['Mes-Año'], y=df_alm['Entradas (Activo)'], name='Entradas Esperadas (Cobranza)', marker_color='#2ca02c'))
        fig.add_trace(go.Bar(x=df_alm['Mes-Año'], y=-df_alm['Salidas (Pasivo)'], name='Salidas Proyectadas (Fondeadores)', marker_color='#d62728'))
        fig.add_trace(go.Scatter(x=df_alm['Mes-Año'], y=df_alm['Flujo Neto'], name='Flujo Neto del Mes', mode='lines+markers', line=dict(color='black', width=3)))
        
        fig.update_layout(barmode='relative', title="Entradas vs Salidas Proyectadas", xaxis_title="Mes", yaxis_title="Monto ($)")
        st.plotly_chart(fig, use_container_width=True)

with tab2:
    st.subheader("Base de Datos - Entradas de Efectivo (Activo)")
    st.dataframe(df_activo, use_container_width=True)

with tab3:
    st.subheader("Base de Datos - Salidas Proyectadas (Pasivo)")
    if not df_flujos_pasivo.empty:
        # Colocamos color distinto si es capital o interés
        st.dataframe(df_flujos_pasivo.sort_values('Fecha'), use_container_width=True)
