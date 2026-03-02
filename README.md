# 个人量化交易与风控系统 (Personal Quant Trading & Risk System)

## 📌 项目愿景 (Project Vision)
本项目旨在为长期美股投资者（具备丰富期权交易经验）构建一个专属的自动化量化交易、回测与风控系统。
**非高频交易 (Non-HFT)**，系统核心侧重于：
1. **资产配置与再平衡**：基于长期视角的动态仓位管理。
2. **期权策略风控**：复杂期权组合（如 Wheel, LEAPS, 动态对冲）的 Greeks（Delta, Gamma, Theta, Vega）敞口实时监控。
3. **策略回测与验证**：提供贴近真实市场（考虑 Bid-Ask 滑点、IV 波动、保证金要求）的期权及股票策略回测环境。
4. **交易日志与复盘**：自动化记录每一笔交易的逻辑、预期收益与实际执行偏差。

---

## 🏗️ 阶段一：业务逻辑理清与架构设计 (Phase 1: Architecture & Requirements)
*当前阶段目标：明确系统边界、定义核心模块、完成技术选型、设计数据流与数据库表结构。*

### 📋 任务清单 (To-Do List)

#### 1. 明确交易标的与核心策略 (Define Universe & Strategies)
- [ ] **界定股票池 (Universe)**：例如标普500成分股、纳斯达克100大盘股、特定的高IV个股或ETF（SPY, QQQ, IWM）。
- [ ] **量化现有策略**：将大脑中的交易逻辑伪代码化 (Pseudocode)。
  - *示例：当 SPY RSI(14) < 30 且 VIX > 25 时，卖出 30 Delta 的 45 DTE Cash-Secured Put。*
  - *示例：核心底仓的动态 Covered Call 覆盖率计算逻辑。*
- [ ] **明确风控底线**：定义单只股票最大仓位、总账户 Margin 使用率上限、投资组合整体 Beta/Delta 上限。

#### 2. 系统架构设计 (System Architecture Design)
- [ ] **绘制系统数据流图 (Data Flow Diagram)**，划分四大核心模块：
  - **Data Layer (数据层)**：行情获取（历史+实时）、数据清洗、特征计算（如隐含波动率 IV 面计算）。
  - **Strategy Layer (策略层)**：回测引擎、信号生成、组合优化。
  - **Execution Layer (执行层)**：订单生成、滑点控制、券商 API 路由交互（Paper/Live）。
  - **Monitor Layer (监控层)**：实时持仓面板、风险预警、交易日记自动化。

#### 3. 技术栈与第三方依赖评估 (Tech Stack & Dependencies)
- [ ] **开发语言**：Python 3.10+
- [ ] **券商 API (Broker)**：评估并开通 API 权限（优选 Interactive Brokers，测试 `ib_insync` 库的联通性）。
- [ ] **数据源 (Data Provider)**：
  - 股票日线/分钟线数据源对比测试（Yahoo Finance / Polygon.io / Tiingo）。
  - 期权链及 Greeks 数据源对比测试（ThetaData / Polygon / IBKR API）。
- [ ] **本地存储 (Storage)**：
  - 时序历史数据：评估 SQLite + Parquet 格式，或 TimescaleDB (PostgreSQL)。
  - 实时状态缓存：评估是否需要 Redis。
- [ ] **监控看板 (Dashboard)**：评估 Streamlit 或 Dash 的快速构建能力。

#### 4. 数据字典与表结构设计 (Database Schema Design)
- [ ] 设计 **行情数据表**（Tickers, Daily_Bars, Option_Chains, Greeks_History）。
- [ ] 设计 **交易日志表**（Trade_Logs, Strategy_Tags, Expected_Return, Actual_Return, Slippage）。
- [ ] 设计 **账户快照表**（Daily_Account_Snapshot, Margin_Usage, Portfolio_Delta）。
  - *关键：必须设计一个能将复杂期权组合（如 Iron Condor）拆解并合并计算总体风险敞口的数据结构。*

#### 5. 制定 API 规范与模块接口 (Define Interfaces)
- [ ] 定义 `DataFeed` 类的基类接口（`get_historical_data`, `get_option_chain`）。
- [ ] 定义 `Broker` 类的基类接口（`get_positions`, `place_order`, `cancel_order`）。
- [ ] 定义 `Strategy` 类的基类接口（`on_data`, `calculate_signals`）。

---

## 🛠️ 系统架构草图 (Architecture Draft)

```text
[ Data Sources ] (Polygon / ThetaData / Yahoo)
       │
       ▼
+-----------------------+      +-------------------------+
|    Data Ingestion     | ───> |  Local Database         |
| (API Fetchers, Cron)  |      | (PostgreSQL / Parquet)  |
+-----------------------+      +-------------------------+
                                           │
                                           ▼
+-----------------------+      +-------------------------+
|  Strategy & Backtest  | <─── |    Feature Engine       |
|  (VectorBT / Pandas)  |      | (IV Calc, Indicators)   |
+-----------------------+      +-------------------------+
       │
       ▼ (Signals / Orders)
+-----------------------+      +-------------------------+
|   Execution Engine    | ───> | Interactive Brokers API |
| (Order Manager, Risk) | <─── | (Live Data, Executions) |
+-----------------------+      +-------------------------+
       │
       ▼ (Trade Logs & Portfolio State)
+-----------------------+
|  Monitoring Dashboard |
| (Streamlit / Dash)    |
+-----------------------+
