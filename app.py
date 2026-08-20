import streamlit as st
import pandas as pd
import datetime
import plotly.graph_objects as go
from streamlit_gsheets import GSheetsConnection
from dateutil.relativedelta import relativedelta

# ==========================================
# 1. CONFIGURACIÓN DE LA PÁGINA E IDENTIDAD
# ==========================================
st.set_page_config(page_title="Tesorería y ALM", layout="wide")

st.image("LOGO_FEX.png", width=300) 

st.title("Módulo de Tesorería y ALM")
st.markdown("Centro de Control de Liquidez: Proyección de Activos vs Fondeadores")

# ==========================================
# 2. BARRA LATERAL (CONTROLES Y RIESGO)
# ==========================================
st.sidebar.header("Supuestos del Modelo")

tasa_morosidad = st.sidebar.slider(
    "Tasa de Morosidad (Impago en Activo)", 
    min_value=0.0, max_value=30.0, value=5.0, step=0.5, format="%f%%"
) / 100.0

st.sidebar.subheader("Salidas No Renovadas (Pasivo)")
st.sidebar.markdown("Capital que sabemos que NO se renovará:")
meses_proyectados = [(datetime.date.today() + pd.DateOffset(months=i)).strftime('%Y-%m') for i in range(6)]
salidas_reales = {}
for mes in meses_proyectados:
    salidas_reales[mes] = st.sidebar.number_input(f"Retiro en {mes} ($)", min_value=0.0, value=0.0, step=50000.0)

# ==========================================
# 3. CONEXIÓN A GOOGLE SHEETS Y LIMPIEZA
# ==========================================
URL_SHEET = "https://docs.google.com/spreadsheets/d/1MYRlXR03vz5T8bw-g-14Tr6LkGERFXIxTUeL_CwxydE/edit?usp=sharing" # Reemplaza con tu URL

def limpiar_numeros(df, columnas):
    for col in columnas:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(r'[$, ]', '', regex=True), 
                errors='coerce'
            ).fillna(0)
    return df

def limpiar_tasa_pasivo(val):
    if pd.isna(val): return 0.0
    try:
        val_str = str(val).replace('%', '').strip()
        tasa = float(val_str)
        return tasa / 100.0 if tasa > 1 else tasa
    except:
        return 0.0

def limpiar_tasa_activo(val):
    if pd.isna(val): return 0.0
    try:
        val_str = str(val).replace('%', '').strip()
        tasa = float(val_str)
        return tasa / 100.0
    except:
        return 0.0

@st.cache_data(ttl=600)
def cargar_datos_sheets():
    conn = st.connection("gsheets", type=GSheetsConnection)
    df_act = conn.read(spreadsheet=URL_SHEET, worksheet="Activo")
    df_pas = conn.read(spreadsheet=URL_SHEET, worksheet="Pasivo")
    
    df_act = limpiar_numeros(df_act, ['Capital', 'Interés', 'Total'])
    df_pas = limpiar_numeros(df_pas, ['Monto de Inversión'])
    
    if '% Rendimiento' in df_pas.columns:
        df_pas['Tasa Decimal'] = df_pas['% Rendimiento'].apply(limpiar_tasa_pasivo)
    else:
        df_pas['Tasa Decimal'] = 0.0
        
    if 'Tasa' in df_act.columns:
        df_act['Tasa Decimal Activo'] = df_act['Tasa'].apply(limpiar_tasa_activo)
    else:
        df_act['Tasa Decimal Activo'] = 0.0
        
    return df_act, df_pas

try:
    df_activo, df_pasivo = cargar_datos_sheets()
except Exception as e:
    st.error(f"Error de conexión con Google Sheets: {e}")
    st.stop()

# ==========================================
# 4. MOTORES DE PROYECCIÓN (ALM) EXACTOS
# ==========================================
def proyectar_flujos_pasivo(df):
    flujos = []
    
    df['Fecha de inicio'] = pd.to_datetime(df['Fecha de inicio'], dayfirst=True, errors='coerce')
    df['Fecha de vencimiento'] = pd.to_datetime(df['Fecha de vencimiento'], dayfirst=True, errors='coerce')
    
    for index, row in df.iterrows():
        fondeador = row.get('Fondeador', 'Desconocido')
        monto_inv = row.get('Monto de Inversión', 0)
        tipo_pago = row.get('Pago Rendimiento', '')
        inicio = row['Fecha de inicio']
        fin = row['Fecha de vencimiento']
        tasa = row.get('Tasa Decimal', 0)
            
        if pd.notna(fin) and tipo_pago != 'Amortización':
            flujos.append({'Fecha': fin, 'Fondeador': fondeador, 'Concepto': 'Devolución Capital', 'Monto': monto_inv})
        
        if tipo_pago == 'Mensual' and pd.notna(inicio) and pd.notna(fin):
            fecha_anterior = inicio
            meses_agregados = 1
            
            while True:
                fecha_actual = inicio + relativedelta(months=meses_agregados)
                try:
                    dia = int(str(row.get('Día pago cupón', '0')).replace('.0', '').strip())
                    if dia > 0:
                        fecha_actual = fecha_actual.replace(day=min(dia, fecha_actual.days_in_month))
                except: pass
                
                if fecha_actual > fin:
                    if fecha_anterior < fin: fecha_actual = fin
                    else: break
                
                dias_naturales = (fecha_actual - fecha_anterior).days
                interes = (tasa / 360.0) * dias_naturales * monto_inv
                
                if interes > 0:
                    flujos.append({'Fecha': fecha_actual, 'Fondeador': fondeador, 'Concepto': 'Interés', 'Monto': interes})
                
                if fecha_actual == fin: break
                fecha_anterior = fecha_actual
                meses_agregados += 1
                
        elif tipo_pago == 'Al termino' and pd.notna(inicio) and pd.notna(fin):
            dias_totales = (fin - inicio).days
            interes_total = (tasa / 360.0) * dias_totales * monto_inv
            flujos.append({'Fecha': fin, 'Fondeador': fondeador, 'Concepto': 'Interés', 'Monto': interes_total})
            
        elif tipo_pago == 'Amortización' and pd.notna(inicio) and pd.notna(fin):
            fechas_pago = []
            m = 1
            while True:
                f_pago = inicio + relativedelta(months=m)
                try:
                    dia = int(str(row.get('Día pago cupón', '0')).replace('.0', '').strip())
                    if dia > 0: f_pago = f_pago.replace(day=min(dia, f_pago.days_in_month))
                except: pass
                    
                if f_pago > fin:
                    if f_pago != fin and (inicio + relativedelta(months=m-1)) < fin: fechas_pago.append(fin)
                    break
                fechas_pago.append(f_pago)
                m += 1
            
            num_pagos = len(fechas_pago)
            if num_pagos > 0:
                capital_mensual = monto_inv / num_pagos
                saldo_insoluto = monto_inv
                fecha_anterior = inicio
                
                for f_actual in fechas_pago:
                    flujos.append({'Fecha': f_actual, 'Fondeador': fondeador, 'Concepto': 'Amortización Capital', 'Monto': capital_mensual})
                    if tasa > 0:
                        dias_naturales = (f_actual - fecha_anterior).days
                        interes_s_i = (tasa / 360.0) * dias_naturales * saldo_insoluto
                        flujos.append({'Fecha': f_actual, 'Fondeador': fondeador, 'Concepto': 'Interés', 'Monto': interes_s_i})
                    saldo_insoluto -= capital_mensual
                    fecha_anterior = f_actual

    df_flujos = pd.DataFrame(flujos)
    if not df_flujos.empty:
        df_flujos['Mes-Año'] = df_flujos['Fecha'].dt.to_period('M').astype(str)
        df_flujos['Categoria'] = df_flujos['Concepto'].apply(
            lambda x: 'Intereses' if 'Interés' in x else ('Amortización' if 'Amortización' in x else 'Devolución Capital')
        )
    return df_flujos

def proyectar_flujos_activo(df, morosidad):
    df['Fecha Fin'] = pd.to_datetime(df['Fecha Fin'], dayfirst=True, errors='coerce')
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
    salidas_pivot = df_flujos_pasivo.groupby(['Mes-Año', 'Categoria'])['Monto'].sum().unstack(fill_value=0).reset_index()
    for col in ['Intereses', 'Amortización', 'Devolución Capital']:
        if col not in salidas_pivot.columns:
            salidas_pivot[col] = 0.0
else:
    salidas_pivot = pd.DataFrame(columns=['Mes-Año', 'Intereses', 'Amortización', 'Devolución Capital'])

df_alm = pd.merge(df_flujos_activo, salidas_pivot, on='Mes-Año', how='outer').fillna(0)

for mes, retiro in salidas_reales.items():
    if mes in df_alm['Mes-Año'].values:
        df_alm.loc[df_alm['Mes-Año'] == mes, 'Devolución Capital'] += retiro
    elif retiro > 0:
        nueva_fila = pd.DataFrame({'Mes-Año': [mes], 'Entradas (Activo)': [0], 'Intereses': [0], 'Amortización': [0], 'Devolución Capital': [retiro]})
        df_alm = pd.concat([df_alm, nueva_fila], ignore_index=True)

df_alm = df_alm.sort_values('Mes-Año')
df_alm['Salidas Totales'] = df_alm['Intereses'] + df_alm['Amortización'] + df_alm['Devolución Capital']
df_alm['Flujo Neto'] = df_alm['Entradas (Activo)'] - df_alm['Salidas Totales']

def formatear_cifra(val):
    if pd.isna(val) or val == 0: return ""
    abs_val = abs(val)
    signo = "-" if val < 0 else ""
    if abs_val >= 1_000_000:
        return f"{signo}{abs_val/1_000_000:.2f}m"
    elif abs_val >= 1_000:
        return f"{signo}{int(abs_val/1000)}k"
    else:
        return f"{signo}{int(abs_val)}"

# ==========================================
# 5. CÁLCULO DE TASAS Y MÁRGENES
# ==========================================
total_activo = df_activo['Capital'].sum() if 'Capital' in df_activo.columns else 0
total_pasivo = df_pasivo['Monto de Inversión'].sum() if 'Monto de Inversión' in df_pasivo.columns else 0
flujo_proximo_mes = df_alm['Flujo Neto'].iloc[0] if not df_alm.empty else 0

tasa_pond_act = 0.0
if 'Tasa Decimal Activo' in df_activo.columns and 'Id Crédito' in df_activo.columns:
    # Agrupamos para calcular el Saldo Insoluto real de cada crédito
    df_agrupado = df_activo.groupby('Id Crédito').agg(
        Saldo_Insoluto=('Capital', 'sum'),
        Tasa=('Tasa Decimal Activo', 'first')
    ).reset_index()
    
    peso_total = df_agrupado['Saldo_Insoluto'].sum()
    if peso_total > 0:
        tasa_pond_act = (df_agrupado['Saldo_Insoluto'] * df_agrupado['Tasa']).sum() / peso_total

tasa_pond_pas = 0.0
if not df_pasivo.empty and total_pasivo > 0:
    tasa_pond_pas = (df_pasivo['Monto de Inversión'] * df_pasivo['Tasa Decimal']).sum() / total_pasivo

# ==========================================
# 6. DASHBOARD Y VISUALIZACIÓN
# ==========================================
tab1, tab2, tab3 = st.tabs(["Centro de Mando", "Detalle Activo", "Detalle Pasivo"])

with tab1:
    st.header("Indicadores Clave (KPIs)")
    
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Cartera Viva Proyectada", f"${total_activo:,.2f}")
    
    if 'Tasa Decimal Activo' in df_activo.columns and 'Id Crédito' in df_activo.columns:
        col2.metric("Tasa Pond. Activo", f"{tasa_pond_act*100:.2f}%")
        col5.metric("Margen Financiero", f"{(tasa_pond_act - tasa_pond_pas)*100:.2f}%")
    else:
        col2.metric("Tasa Pond. Activo", "Requiere Id Crédito")
        col5.metric("Margen Financiero", "N/A")
        
    col3.metric("Fondeo Total", f"${total_pasivo:,.2f}")
    col4.metric("Tasa Pond. Pasivo", f"{tasa_pond_pas*100:.2f}%")

    st.divider()
    st.subheader("Gráfica de Liquidez Mensual")
    
    if not df_alm.empty:
        meses_disponibles = sorted(df_alm['Mes-Año'].unique())
        if len(meses_disponibles) > 1:
            mes_inicio, mes_fin = st.select_slider("Selecciona el horizonte de tiempo:", options=meses_disponibles, value=(meses_disponibles[0], meses_disponibles[-1]))
            df_grafica = df_alm[(df_alm['Mes-Año'] >= mes_inicio) & (df_alm['Mes-Año'] <= mes_fin)].copy()
        else:
            df_grafica = df_alm.copy()
            
        fig = go.Figure()
        
        fig.add_trace(go.Bar(
            x=df_grafica['Mes-Año'], y=df_grafica['Entradas (Activo)'], 
            name='Entradas (Cobranza)', marker_color='#2ca02c',
            text=df_grafica['Entradas (Activo)'].apply(formatear_cifra), textposition='inside'
        ))
        
        fig.add_trace(go.Bar(
            x=df_grafica['Mes-Año'], y=-df_grafica['Intereses'], 
            name='Pago de Intereses', marker_color='#fdb863',
            text=df_grafica['Intereses'].apply(formatear_cifra), textposition='inside'
        ))
        
        fig.add_trace(go.Bar(
            x=df_grafica['Mes-Año'], y=-df_grafica['Amortización'], 
            name='Amortizaciones', marker_color='#e66101',
            text=df_grafica['Amortización'].apply(formatear_cifra), textposition='inside'
        ))
        
        fig.add_trace(go.Bar(
            x=df_grafica['Mes-Año'], y=-df_grafica['Devolución Capital'], 
            name='Devolución Capital', marker_color='#b2182b',
            text=df_grafica['Devolución Capital'].apply(formatear_cifra), textposition='inside'
        ))
        
        fig.add_trace(go.Scatter(
            x=df_grafica['Mes-Año'], y=df_grafica['Flujo Neto'], 
            name='Flujo Neto del Mes', mode='lines+markers+text', 
            text=df_grafica['Flujo Neto'].apply(formatear_cifra), textposition='top center',
            textfont=dict(size=12, color='black'), line=dict(color='black', width=3)
        ))
        
        fig.update_layout(barmode='relative', title="Desglose de Entradas vs Salidas", xaxis_title="Mes", yaxis_title="Monto ($)", height=600)
        st.plotly_chart(fig, use_container_width=True)

with tab2:
    st.subheader("Base de Datos - Entradas (Activo)")
    st.dataframe(df_activo, use_container_width=True)

with tab3:
    st.subheader("Base de Datos - Salidas (Pasivo con Cálculo Exacto)")
    if not df_flujos_pasivo.empty:
        df_flujos_pasivo_display = df_flujos_pasivo.sort_values('Fecha').copy()
        df_flujos_pasivo_display['Fecha'] = df_flujos_pasivo_display['Fecha'].dt.strftime('%d/%m/%Y')
        df_flujos_pasivo_display['Monto'] = df_flujos_pasivo_display['Monto'].apply(lambda x: f"${x:,.2f}")
        st.dataframe(df_flujos_pasivo_display, use_container_width=True)
