import streamlit as st
import pandas as pd
import datetime
import plotly.graph_objects as go
from streamlit_gsheets import GSheetsConnection
from dateutil.relativedelta import relativedelta
import streamlit.components.v1 as components

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
URL_SHEET = "https://docs.google.com/spreadsheets/d/1MYRlXR03vz5T8bw-g-14Tr6LkGERFXIxTUeL_CwxydE/edit?usp=sharing" # <-- RECUERDA PONER TU URL REAL AQUÍ

def limpiar_numeros(df, columnas):
    cols_reales = df.columns.tolist()
    for col_buscada in columnas:
        for col_real in cols_reales:
            if col_buscada.lower() == col_real.lower().strip():
                df[col_real] = pd.to_numeric(
                    df[col_real].astype(str).str.replace(r'[$, ]', '', regex=True), 
                    errors='coerce'
                ).fillna(0)
    return df

def limpiar_tasa(val):
    if pd.isna(val) or val == "": return 0.0
    try:
        val_str = str(val).replace('%', '').strip()
        tasa = float(val_str)
        return tasa / 100.0 if tasa >= 1 else tasa
    except:
        return 0.0

@st.cache_data(ttl=600)
def cargar_datos_sheets():
    conn = st.connection("gsheets", type=GSheetsConnection)
    df_act = conn.read(spreadsheet=URL_SHEET, worksheet="Activo")
    df_pas = conn.read(spreadsheet=URL_SHEET, worksheet="Pasivo")
    
    df_act.columns = df_act.columns.str.strip()
    df_pas.columns = df_pas.columns.str.strip()
    
    df_act = limpiar_numeros(df_act, ['Capital', 'Interés', 'Total'])
    df_pas = limpiar_numeros(df_pas, ['Monto de Inversión'])
    
    col_rendimiento = [c for c in df_pas.columns if 'rendimiento' in c.lower()]
    if col_rendimiento:
        df_pas['Tasa Decimal Pasivo'] = df_pas[col_rendimiento[0]].apply(limpiar_tasa)
    else:
        df_pas['Tasa Decimal Pasivo'] = 0.0
        
    col_tasa_act = [c for c in df_act.columns if c.lower() == 'tasa']
    if col_tasa_act:
        df_act['Tasa Decimal Activo'] = df_act[col_tasa_act[0]].apply(limpiar_tasa)
    else:
        df_act['Tasa Decimal Activo'] = 0.0
        
    return df_act, df_pas

try:
    df_activo, df_pasivo = cargar_datos_sheets()
except Exception as e:
    st.error(f"Error de conexión con Google Sheets: {e}")
    st.stop()

# ==========================================
# 4. MOTORES DE PROYECCIÓN (ALM)
# ==========================================
def proyectar_flujos_pasivo(df):
    flujos = []
    col_inicio = [c for c in df.columns if 'inicio' in c.lower()][0] if [c for c in df.columns if 'inicio' in c.lower()] else 'Fecha de inicio'
    col_fin = [c for c in df.columns if 'vencimiento' in c.lower()][0] if [c for c in df.columns if 'vencimiento' in c.lower()] else 'Fecha de vencimiento'
    
    df[col_inicio] = pd.to_datetime(df[col_inicio], dayfirst=True, errors='coerce')
    df[col_fin] = pd.to_datetime(df[col_fin], dayfirst=True, errors='coerce')
    
    for index, row in df.iterrows():
        fondeador = row.get('Fondeador', 'Desconocido')
        monto_inv = row.get('Monto de Inversión', 0)
        tipo_pago = row.get('Pago Rendimiento', '')
        inicio = row[col_inicio]
        fin = row[col_fin]
        tasa = row.get('Tasa Decimal Pasivo', 0)
            
        if pd.notna(fin) and tipo_pago != 'Amortización':
            flujos.append({'Fecha': fin, 'Fondeador': fondeador, 'Concepto': 'Devolución Capital', 'Monto': monto_inv})
        
        if tipo_pago == 'Mensual' and pd.notna(inicio) and pd.notna(fin):
            fecha_anterior = inicio
            meses_agregados = 1
            while True:
                fecha_actual = inicio + relativedelta(months=meses_agregados)
                try:
                    dia = int(str(row.get('Día pago cupón', '0')).replace('.0', '').strip())
                    if dia > 0: fecha_actual = fecha_actual.replace(day=min(dia, fecha_actual.days_in_month))
                except: pass
                
                if fecha_actual > fin:
                    if fecha_anterior < fin: fecha_actual = fin
                    else: break
                
                dias_naturales = (fecha_actual - fecha_anterior).days
                interes = (tasa / 360.0) * dias_naturales * monto_inv
                
                if interes > 0: flujos.append({'Fecha': fecha_actual, 'Fondeador': fondeador, 'Concepto': 'Interés', 'Monto': interes})
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
    col_fin = [c for c in df.columns if 'fin' in c.lower()][0] if [c for c in df.columns if 'fin' in c.lower()] else 'Fecha Fin'
    df[col_fin] = pd.to_datetime(df[col_fin], dayfirst=True, errors='coerce')
    df = df.dropna(subset=[col_fin]).copy()
    df['Mes-Año'] = df[col_fin].dt.to_period('M').astype(str)
    
    col_total = [c for c in df.columns if 'total' in c.lower()][0] if [c for c in df.columns if 'total' in c.lower()] else 'Total'
    df['Cobro Esperado'] = df[col_total] * (1 - morosidad)
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
    if abs_val >= 1_000_000: return f"{signo}{abs_val/1_000_000:.2f}m"
    elif abs_val >= 1_000: return f"{signo}{int(abs_val/1000)}k"
    else: return f"{signo}{int(abs_val)}"

# ==========================================
# 5. CÁLCULO DE TASAS Y MÁRGENES
# ==========================================
col_capital_act = [c for c in df_activo.columns if 'capital' in c.lower()]
col_cap = col_capital_act[0] if col_capital_act else 'Capital'

total_activo = df_activo[col_cap].sum() if col_cap in df_activo.columns else 0
total_pasivo = df_pasivo['Monto de Inversión'].sum() if 'Monto de Inversión' in df_pasivo.columns else 0

tasa_pond_act = 0.0
col_id = [c for c in df_activo.columns if 'id cr' in c.lower() or 'id_cr' in c.lower()]

if 'Tasa Decimal Activo' in df_activo.columns and col_id:
    id_credito = col_id[0]
    df_agrupado = df_activo.groupby(id_credito).agg(
        Saldo_Insoluto=(col_cap, 'sum'),
        Tasa=('Tasa Decimal Activo', 'max') 
    ).reset_index()
    
    peso_total = df_agrupado['Saldo_Insoluto'].sum()
    if peso_total > 0:
        tasa_pond_act = (df_agrupado['Saldo_Insoluto'] * df_agrupado['Tasa']).sum() / peso_total

tasa_pond_pas = 0.0
if not df_pasivo.empty and total_pasivo > 0:
    tasa_pond_pas = (df_pasivo['Monto de Inversión'] * df_pasivo['Tasa Decimal Pasivo']).sum() / total_pasivo

# ==========================================
# 6. DASHBOARD Y VISUALIZACIÓN
# ==========================================
tab1, tab2, tab3 = st.tabs(["Centro de Mando", "Detalle Activo", "Detalle Pasivo"])

with tab1:
    st.header("Indicadores Clave (KPIs)")
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Cartera Viva Proyectada", f"${total_activo:,.2f}")
    
    if tasa_pond_act > 0:
        col2.metric("Tasa Pond. Activo", f"{tasa_pond_act*100:.2f}%")
        col5.metric("Margen Financiero", f"{(tasa_pond_act - tasa_pond_pas)*100:.2f}%")
    else:
        col2.metric("Tasa Pond. Activo", "Revisando formato...")
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

        st.divider()
        
        # Inyección de CSS para formato de impresión limpio y botón de PDF
        st.markdown("""
            <style>
            @media print {
                section[data-testid="stSidebar"] {display: none !important;}
                header[data-testid="stHeader"] {display: none !important;}
                footer {display: none !important;}
                .stDeployButton {display: none !important;}
                .stApp {background-color: white !important;}
                .block-container {max-width: 100% !important; padding: 0 !important;}
            }
            </style>
        """, unsafe_allow_html=True)
        
        components.html("""
            <script>
            function printReport() {
                window.parent.print();
            }
            </script>
            <div style="text-align: right;">
                <button onclick="printReport()" style="
                    background-color: #2ca02c; 
                    border: none;
                    color: white;
                    padding: 12px 24px;
                    text-align: center;
                    text-decoration: none;
                    display: inline-block;
                    font-size: 16px;
                    font-weight: bold;
                    margin: 4px 2px;
                    cursor: pointer;
                    border-radius: 8px;
                    font-family: sans-serif;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                ">
                    Descargar Reporte PDF
                </button>
            </div>
        """, height=70)

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
