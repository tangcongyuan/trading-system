import streamlit as st
import pandas as pd
import numpy as np
from scipy.stats import norm
import yfinance as yf
from datetime import datetime
import re
import io
import sqlite3
import plotly.express as px
import hashlib

# --- 1. 数据库逻辑 (新增哈希字段) ---
def init_db():
    conn = sqlite3.connect('trading_vault.db')
    c = conn.cursor()
    # 新增 file_hash 字段用于内容唯一性校验
    c.execute('''CREATE TABLE IF NOT EXISTS portfolio_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  account_id TEXT,
                  file_name TEXT, 
                  file_hash TEXT UNIQUE, 
                  timestamp DATETIME,
                  portfolio_beta REAL,
                  total_spy_delta REAL,
                  total_dollar_delta REAL,
                  net_value REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS position_details
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  history_id INTEGER,
                  symbol TEXT, pos_type TEXT, qty REAL, price REAL, 
                  beta REAL, delta_shares REAL, dollar_delta REAL)''')
    conn.commit()
    conn.close()

def get_file_hash(file):
    """为文件内容生成唯一 MD5 指纹"""
    file_content = file.getvalue()
    return hashlib.md5(file_content).hexdigest()

def is_hash_exists(file_hash):
    """检查该内容指纹是否已存档"""
    conn = sqlite3.connect('trading_vault.db')
    c = conn.cursor()
    c.execute("SELECT id FROM portfolio_history WHERE file_hash = ?", (file_hash,))
    exists = c.fetchone()
    conn.close()
    return exists is not None

def save_snapshot(account_id, file_name, file_hash, agg_metrics, details_list):
    conn = sqlite3.connect('trading_vault.db')
    c = conn.cursor()
    try:
        # 使用 INSERT OR IGNORE 防止并发冲突
        c.execute('''INSERT OR IGNORE INTO portfolio_history 
                     (account_id, file_name, file_hash, timestamp, portfolio_beta, total_spy_delta, total_dollar_delta, net_value)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
                  (account_id, file_name, file_hash, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                   agg_metrics['beta'], agg_metrics['spy_delta'], 
                   agg_metrics['dollar_delta'], agg_metrics['net_value']))
        
        history_id = c.lastrowid
        # 如果 lastrowid 为 0，说明被 IGNORE 了（哈希重复）
        if history_id:
            details_data = [(history_id, d['标的'], d['类型'], d['数量'], d['现价'], d['Beta'], d['持仓Delta (股)'], d['金额Delta ($)']) for d in details_list]
            c.executemany('''INSERT INTO position_details 
                             (history_id, symbol, pos_type, qty, price, beta, delta_shares, dollar_delta)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', details_data)
            conn.commit()
            return True
        return False
    except Exception as e:
        st.error(f"存档失败: {e}")
        return False
    finally:
        conn.close()

def delete_snapshot(history_id):
    conn = sqlite3.connect('trading_vault.db')
    c = conn.cursor()
    c.execute("DELETE FROM position_details WHERE history_id = ?", (history_id,))
    c.execute("DELETE FROM portfolio_history WHERE id = ?", (history_id,))
    conn.commit()
    conn.close()

def get_all_history_files():
    conn = sqlite3.connect('trading_vault.db')
    try:
        df = pd.read_sql_query("SELECT id, file_name, timestamp, account_id FROM portfolio_history ORDER BY timestamp DESC", conn)
    except:
        df = pd.DataFrame()
    conn.close()
    return df

# --- 2. 核心计算逻辑 (保持不变) ---
def clean_val(x):
    if pd.isna(x) or str(x).strip() in ['--', '', 'n/a']: return 0.0
    s = str(x).replace('$', '').replace(',', '').replace(' ', '')
    if '(' in s and ')' in s: s = '-' + s.replace('(', '').replace(')', '')
    try: return float(s)
    except: return 0.0

def load_fidelity_csv(file):
    # 重置文件指针确保读取完整
    file.seek(0)
    raw_content = file.read().decode('utf-8').splitlines()
    header_idx = 0
    for i, line in enumerate(raw_content):
        if "Symbol" in line: header_idx = i; break
    clean_lines = [line.strip().rstrip(',') for line in raw_content[header_idx:] 
                   if "Symbol" in line or (len(line.split(',')) > 5 and "Total" not in line)]
    return pd.read_csv(io.StringIO("\n".join(clean_lines)))

def get_pos_info(row):
    sym = str(row['Symbol']).strip().upper()
    desc = str(row.get('Description', '')).upper()
    m = re.search(r"([A-Z]+)\s+([A-Z]{3})\s+(\d+)\s+(\d{4})\s+\$(\d+\.?\d*)\s+(PUT|CALL)", desc)
    if m:
        months = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
        return {'is_opt': True, 'ticker': m.group(1), 'exp': datetime(int(m.group(4)), months.index(m.group(2))+1, int(m.group(3))), 'strike': float(m.group(5)), 'type': m.group(6).capitalize()}
    ticker = re.sub(r'[^A-Z]', '', sym)
    return {'is_opt': False, 'ticker': ticker} if 1 <= len(ticker) <= 5 else None

# --- 3. UI 逻辑 ---
st.set_page_config(page_title="Portfolio Alpha Sentinel", layout="wide")
init_db()

if 'view_mode' not in st.session_state: st.session_state.view_mode = 'upload'
if 'selected_history_id' not in st.session_state: st.session_state.selected_history_id = None

st.title("🛡️ 账户风险追踪系统")

# --- A. 历史快照库 ---
st.write("### 📂 历史快照库")
history_files = get_all_history_files()
if not history_files.empty:
    for i, row in history_files.iterrows():
        c1, c2 = st.columns([0.9, 0.1])
        with c1:
            if st.button(f"📄 {row['file_name']} (存于 {row['timestamp']})", key=f"v_{row['id']}", use_container_width=True):
                st.session_state.view_mode = 'history'
                st.session_state.selected_history_id = row['id']
        with c2:
            if st.button("❌", key=f"d_{row['id']}", use_container_width=True):
                delete_snapshot(row['id'])
                st.rerun()
else:
    st.info("尚无历史记录。")

st.divider()

# --- B. 文件上传区 ---
uploaded_files = st.file_uploader("🆕 导入 Fidelity Positions.csv (内容变动自动存档)", type="csv", accept_multiple_files=True)

if uploaded_files:
    if st.session_state.view_mode != 'history':
        st.session_state.view_mode = 'upload'

# --- C. 数据分析与哈希校验存档 ---
BETA_FALLBACKS = {'SPY': 1.0, 'VOO': 1.0, 'QQQ': 1.18, 'TQQQ': 3.55, 'DIA': 0.8}

if st.session_state.view_mode == 'upload' and uploaded_files:
    active_file = uploaded_files[-1]
    
    # 核心：计算当前上传文件的指纹
    current_file_hash = get_file_hash(active_file)
    
    df = load_fidelity_csv(active_file)
    acc_id = str(df['Account Number'].iloc[0]) if 'Account Number' in df.columns else "Unknown"
    
    st.subheader(f"🔍 实时分析: {active_file.name}")
    
    total_net_value = 0.0
    current_results = []
    
    with st.spinner('正在获取实时行情并生成风险画像...'):
        try:
            spy_price = yf.Ticker('SPY').fast_info['last_price']
        except:
            st.error("行情接口超时，请重试"); st.stop()

        for _, row in df.iterrows():
            qty = clean_val(row['Quantity'])
            info = get_pos_info(row)
            if not info or qty == 0: continue
            total_net_value += clean_val(row.get('Current Value', 0))
            
            try:
                tkr = yf.Ticker(info['ticker'])
                price = tkr.fast_info['last_price']
                beta = BETA_FALLBACKS.get(info['ticker']) or tkr.info.get('beta', 1.0)
                if info['is_opt']:
                    T = max((info['exp'] - datetime.now()).days / 365.0, 0.001)
                    d1 = (np.log(price/info['strike']) + (0.045 + 0.5*0.3**2)*T) / (0.3*np.sqrt(T))
                    delta_shares = (norm.cdf(d1) if info['type']=='Call' else norm.cdf(d1)-1) * qty * 100
                    p_type = f"{info['type']} ${info['strike']}"
                else:
                    delta_shares, p_type = qty, "STOCK"
                
                current_results.append({
                    "标的": info['ticker'], "类型": p_type, "数量": qty, "现价": price, 
                    "Beta": beta, "持仓Delta (股)": delta_shares, 
                    "金额Delta ($)": delta_shares * price, 
                    "SPY等效Delta (股)": (delta_shares * price * beta) / spy_price
                })
            except: continue

    if current_results:
        res_df = pd.DataFrame(current_results)
        p_beta = (res_df['金额Delta ($)'] * res_df['Beta']).sum() / total_net_value if total_net_value != 0 else 0
        
        # ✨ 哈希校验：只有数据库中没有这个内容指纹时才保存
        if not is_hash_exists(current_file_hash):
            metrics = {
                'beta': p_beta, 
                'spy_delta': res_df['SPY等效Delta (股)'].sum(), 
                'dollar_delta': res_df['金额Delta ($)'].sum(), 
                'net_value': total_net_value
            }
            if save_snapshot(acc_id, active_file.name, current_file_hash, metrics, current_results):
                st.toast(f"✅ 检测到新数据，已自动存档")
                st.rerun()

        # UI 显示 (Metrics)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Portfolio Beta", f"{p_beta:.2f}")
        c2.metric("SPY 等效股数", f"{res_df['SPY等效Delta (股)'].sum():.1f}")
        c3.metric("总金额 Delta", f"${res_df['金额Delta ($)'].sum():,.0f}")
        c4.metric("净资产", f"${total_net_value:,.0f}")
        st.dataframe(res_df, use_container_width=True)

elif st.session_state.view_mode == 'history' and st.session_state.selected_history_id:
    hid = st.session_state.selected_history_id
    conn = sqlite3.connect('trading_vault.db')
    try:
        meta = pd.read_sql_query(f"SELECT * FROM portfolio_history WHERE id = {hid}", conn).iloc[0]
        details = pd.read_sql_query(f"SELECT * FROM position_details WHERE history_id = {hid}", conn)
        conn.close()

        st.subheader(f"📜 历史存档详情: {meta['file_name']}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Portfolio Beta", f"{meta['portfolio_beta']:.2f}")
        c2.metric("SPY 等效股数", f"{meta['total_spy_delta']:.1f}")
        c3.metric("总金额 Delta", f"${meta['total_dollar_delta']:,.0f}")
        c4.metric("净资产", f"${meta['net_value']:,.0f}")
        st.dataframe(details.drop(columns=['id', 'history_id']), use_container_width=True)
        if st.button("⬅️ 返回上传预览"):
            st.session_state.view_mode = 'upload'
            st.rerun()
    except:
        st.session_state.view_mode = 'upload'

# --- D. 趋势图 ---
st.divider()
st.header("📈 风险趋势总览")
conn = sqlite3.connect('trading_vault.db')
all_hist = pd.read_sql_query("SELECT * FROM portfolio_history ORDER BY timestamp ASC", conn)
conn.close()

if not all_hist.empty:
    fig = px.line(all_hist, x='timestamp', y='portfolio_beta', color='account_id', 
                 title="Beta 演变 (基于数据变动点)", markers=True, hover_data=['file_name'])
    st.plotly_chart(fig, use_container_width=True)