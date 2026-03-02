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

# --- 1. 数据库逻辑 (新增 Gamma/Theta 字段) ---
def init_db():
    conn = sqlite3.connect('trading_vault.db')
    c = conn.cursor()
    # 历史汇总表：增加 total_gamma, total_theta
    c.execute('''CREATE TABLE IF NOT EXISTS portfolio_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  account_id TEXT, file_name TEXT, file_hash TEXT UNIQUE, 
                  timestamp DATETIME, portfolio_beta REAL, total_spy_delta REAL,
                  total_gamma REAL, total_theta REAL, 
                  total_dollar_delta REAL, net_value REAL)''')
    # 明细表：增加 gamma, theta
    c.execute('''CREATE TABLE IF NOT EXISTS position_details
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, history_id INTEGER,
                  symbol TEXT, pos_type TEXT, qty REAL, price REAL, 
                  beta REAL, delta_shares REAL, dollar_delta REAL,
                  gamma REAL, theta REAL)''')
    conn.commit()
    conn.close()

# --- 2. 核心计算逻辑 (升级 Black-Scholes) ---
def calc_greeks(S, K, T, r, sigma, option_type='Call'):
    """计算 Delta, Gamma, 和每日 Theta"""
    if T <= 0: return 0, 0, 0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    
    # Delta
    delta = norm.cdf(d1) if option_type == 'Call' else norm.cdf(d1) - 1
    
    # Gamma (风险加速度)
    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    
    # Theta (现金流/时间衰减)
    term1 = -(S * norm.pdf(d1) * sigma) / (2 * np.sqrt(T))
    term2 = r * K * np.exp(-r * T) * (norm.cdf(d2) if option_type == 'Call' else -norm.cdf(-d2))
    theta_daily = (term1 - term2) / 365.0
    
    return delta, gamma, theta_daily

# --- 3. 辅助功能 ---
# 核心 ETF 的准确 Beta 值（相对于 SPY）
KNOWN_BETAS = {
    'QQQ': 1.18,   # 纳斯达克 100，科技股权重高
    'SPY': 1.0,    # 标普 500 基准
    'IWM': 1.15,   # 罗素 2000 小盘股
    'DIA': 0.95,   # 道琼斯工业平均
    'VTI': 1.0,    # 全市场 ETF
    'VOO': 1.0,    # 标普 500 (Vanguard)
    'TQQQ': 3.54,  # 3x 杠杆纳斯达克
    'SQQQ': -3.54, # 3x 反向纳斯达克
    'UPRO': 3.0,   # 3x 杠杆标普
    'SPXU': -3.0,  # 3x 反向标普
}

def get_file_hash(file):
    return hashlib.md5(file.getvalue()).hexdigest()

def is_hash_exists(file_hash):
    conn = sqlite3.connect('trading_vault.db')
    c = conn.cursor()
    c.execute("SELECT id FROM portfolio_history WHERE file_hash = ?", (file_hash,))
    res = c.fetchone()
    conn.close()
    return res is not None

def clean_val(x):
    if pd.isna(x) or str(x).strip() in ['--', '', 'n/a']: return 0.0
    s = str(x).replace('$', '').replace(',', '').replace(' ', '')
    if '(' in s and ')' in s: s = '-' + s.replace('(', '').replace(')', '')
    try: return float(s)
    except: return 0.0

def load_fidelity_csv(file):
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

# --- 4. UI 逻辑 ---
st.set_page_config(page_title="Alpha Sentinel Pro", layout="wide")
init_db()

if 'view_mode' not in st.session_state: st.session_state.view_mode = 'upload'
st.title("🛡️ 账户风险追踪系统 (Gamma/Theta 专业版)")

# --- A. 历史快照库 ---
with st.sidebar:
    st.header("📂 存档库")
    conn = sqlite3.connect('trading_vault.db')
    history_df = pd.read_sql_query("SELECT id, file_name, timestamp FROM portfolio_history ORDER BY timestamp DESC LIMIT 10", conn)
    conn.close()
    for _, row in history_df.iterrows():
        if st.button(f"{row['file_name']}\n{row['timestamp']}", key=f"h_{row['id']}", use_container_width=True):
            st.session_state.view_mode = 'history'
            st.session_state.selected_id = row['id']
            st.rerun()

# --- B. 数据处理与分析 ---
uploaded_files = st.file_uploader("导入 Fidelity CSV", type="csv", accept_multiple_files=True)

if uploaded_files:
    active_file = uploaded_files[-1]
    f_hash = get_file_hash(active_file)
    df = load_fidelity_csv(active_file)
    acc_id = str(df['Account Number'].iloc[0]) if 'Account Number' in df.columns else "Acc_1"
    
    total_metrics = {'net_val': 0.0, 'gamma': 0.0, 'theta': 0.0, 'dollar_delta': 0.0, 'spy_delta': 0.0}
    current_results = []
    
    with st.spinner('计算实时 Greeks...'):
        spy_price = yf.Ticker('SPY').fast_info['last_price']
        for _, row in df.iterrows():
            qty = clean_val(row['Quantity'])
            info = get_pos_info(row)
            if not info or qty == 0: continue
            
            cur_val = clean_val(row.get('Current Value', 0))
            total_metrics['net_val'] += cur_val
            
            try:
                tkr = yf.Ticker(info['ticker'])
                S = tkr.fast_info['last_price']
                # 优先使用硬编码的准确 Beta 值
                beta = KNOWN_BETAS.get(info['ticker'], tkr.info.get('beta', 1.0))
                
                if info['is_opt']:
                    T = max((info['exp'] - datetime.now()).days / 365.0, 0.001)
                    # 假定 IV=0.25, r=0.045
                    d, g, t = calc_greeks(S, info['strike'], T, 0.045, 0.25, info['type'])
                    pos_delta = d * qty * 100
                    pos_gamma = g * qty * 100
                    pos_theta = t * qty * 100
                    p_type = f"{info['type']} ${info['strike']}"
                else:
                    pos_delta, pos_gamma, pos_theta = qty, 0.0, 0.0
                    p_type = "STOCK"
                
                # 累加指标
                total_metrics['gamma'] += pos_gamma
                total_metrics['theta'] += pos_theta
                total_metrics['dollar_delta'] += pos_delta * S
                total_metrics['spy_delta'] += (pos_delta * S * beta) / spy_price
                
                current_results.append({
                    "标的": info['ticker'], "类型": p_type, "数量": qty, 
                    "Delta (股)": pos_delta, "Gamma": pos_gamma, "Theta (日)": pos_theta,
                    "金额Delta": pos_delta * S, "Beta": beta
                })
            except: continue

    # 存档逻辑
    if not is_hash_exists(f_hash):
        conn = sqlite3.connect('trading_vault.db')
        c = conn.cursor()
        c.execute('''INSERT INTO portfolio_history 
                     (account_id, file_name, file_hash, timestamp, portfolio_beta, total_spy_delta, total_gamma, total_theta, total_dollar_delta, net_value)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (acc_id, active_file.name, f_hash, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   total_metrics['dollar_delta']/total_metrics['net_val'] if total_metrics['net_val']!=0 else 0,
                   total_metrics['spy_delta'], total_metrics['gamma'], total_metrics['theta'], total_metrics['dollar_delta'], total_metrics['net_val']))
        h_id = c.lastrowid
        # 批量存入明细
        details_data = [(h_id, r['标的'], r['类型'], r['数量'], 0.0, r['Beta'], r['Delta (股)'], r['金额Delta'], r['Gamma'], r['Theta (日)']) for r in current_results]
        c.executemany("INSERT INTO position_details (history_id, symbol, pos_type, qty, price, beta, delta_shares, dollar_delta, gamma, theta) VALUES (?,?,?,?,?,?,?,?,?,?)", details_data)
        conn.commit(); conn.close()
        st.toast("新数据已存档"); st.rerun()

    # --- C. 核心可视化展示 ---
    st.subheader("🔥 组合实时风险看板")
    
    # 1. 风险看板 (带有逻辑预警)
    c1, c2, c3, c4 = st.columns(4)
    
    # Delta
    c1.metric("SPY 等效股数", f"{total_metrics['spy_delta']:.1f}")
    
    # Gamma 风险预警
    gamma_val = total_metrics['gamma']
    gamma_color = "normal"
    if gamma_val < -10: # 阈值可根据账户大小调整
        st.error(f"⚠️ 高负 Gamma 警告: 组合具有极高的波动率风险！当前 Net Gamma: {gamma_val:.4f}")
        gamma_color = "inverse"
    c2.metric("Net Gamma (加速度)", f"{gamma_val:.4f}", delta_color=gamma_color)
    
    # Theta 现金流监控
    theta_val = total_metrics['theta']
    theta_help = "这是你账户每天流逝/赚取的‘时间租金’"
    c3.metric("每日 Theta 收入", f"${theta_val:.2f}", help=theta_help)
    
    # 净资产
    c4.metric("账户净值", f"${total_metrics['net_val']:,.2f}")

    # 2. 持仓明细表
    st.write("#### 📝 详细持仓清单")
    st.dataframe(pd.DataFrame(current_results), use_container_width=True)

# --- D. 趋势总览 ---
st.divider()
st.header("📈 风险演变趋势")
conn = sqlite3.connect('trading_vault.db')
trend_df = pd.read_sql_query("SELECT timestamp, total_theta, total_gamma FROM portfolio_history ORDER BY timestamp ASC", conn)
conn.close()

if not trend_df.empty:
    fig = px.line(trend_df, x='timestamp', y=['total_theta', 'total_gamma'], 
                 title="时间价值 (Theta) 与 风险加速度 (Gamma) 的平衡演变",
                 labels={"value": "指标数值", "variable": "指标类型"})
    st.plotly_chart(fig, use_container_width=True)
