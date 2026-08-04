# Build a Production-Grade AI-Driven Multi-Market Options Buying Paper Trading Platform

I want you to build a complete, production-quality application for automated **Options Buying Paper Trading**. Your responsibility is to determine the best architecture, technology stack, project structure, database design, development roadmap, APIs, services, deployment model, and implementation strategy. Do not limit yourself to a basic trading bot—design this as an institutional-grade research and execution platform that can continuously improve its own trading performance through data collection and AI-assisted optimization.

The application should be designed from the ground up with scalability, modularity, maintainability, and future transition to live trading in mind. Initially, however, **the entire system must operate only in paper trading mode.**

---

# Primary Objective

The application should automatically discover, evaluate, and execute high-probability **Option Buying trades** across multiple markets using quantitative analysis, market microstructure analysis, institutional order flow concepts, option chain analytics, and AI-driven feedback.

The system should continuously analyze the market, detect opportunities before large directional moves occur, paper trade those opportunities automatically, evaluate performance, learn from every trade, and progressively improve strategy quality.

The application should not rely on a single rigid strategy. Instead, it should function as an intelligent trading research platform capable of evolving over time.

---

# Trading Segments

The application must support four completely independent trading segments.

1. NSE Stock Options
2. NSE Index Options
3. US Stock Options
4. US Index Options

These four segments should operate independently.

Each segment should have:

* Separate capital
* Separate strategies
* Separate trade history
* Separate analytics
* Separate risk management
* Separate performance statistics
* Separate optimization
* Separate AI feedback

No segment should depend on another.

---

# Paper Trading Capital

Each segment starts with dedicated paper capital.

NSE Stock Options

₹50,000

NSE Index Options

₹50,000

US Stock Options

$50,000

US Index Options

$50,000

Risk calculations, position sizing, capital allocation, drawdown, and performance statistics must all be calculated independently for every segment.

If one segment performs poorly it must never affect the others.

As profits increase, risk calculations should dynamically scale.

As losses increase, exposure should automatically reduce.

Risk must always adapt according to current account equity rather than initial capital.

---

# Data Sources

## NSE Markets

Use the Upstox Analytics Long Term API.

The system should automatically ingest:

Historical underlying data

Historical option chain data (where available)

Historical OI

Historical Greeks

Historical volume

Historical IV

Live underlying market feed

Live option chain

Live Greeks

Live open interest

Live volume

Live bid/ask

Market depth whenever available

Corporate action adjustments wherever necessary.

---

## US Markets

Use the LondonStrategicEdge API.

The system should ingest:

Historical underlying data

Historical options data

Historical option chains

Historical Greeks

Historical implied volatility

Real-time underlying data

Real-time option chain

Real-time Greeks

Real-time market data

Live bid/ask

Volume

Open Interest

---

# Market Analysis Engine

The system should continuously analyze markets instead of waiting for fixed signals.

It should build an evolving understanding of market structure.

Examples include:

Institutional order flow

Liquidity zones

Smart money footprints

Volume profile

Market profile

Auction theory

VWAP behavior

Opening range

Market regime

Trend strength

Momentum

Acceleration

Distribution

Accumulation

Delta imbalance

Bid/ask imbalance

Absorption

Exhaustion

Breakout quality

False breakout detection

Volatility expansion

Volatility contraction

Gamma exposure

Dealer positioning (where derivable)

Option flow

Open interest changes

Put-call positioning

Greeks interaction

Time decay environment

Sector strength

Index correlation

Relative strength

Relative weakness

Inter-market relationships

Higher timeframe bias

Intraday structure

Price acceptance

Price rejection

Support/resistance evolution

Liquidity sweeps

Market inefficiencies

The engine should continuously combine multiple independent observations before generating trade opportunities.

Avoid simplistic indicator-only logic.

Indicators may assist decision-making but should never be the sole reason for entering trades.

---

# Early Trade Detection Philosophy

One of the primary objectives of the application is identifying trades **before** the majority of the move has already occurred.

The application should prioritize:

Early momentum development

Institutional accumulation

Liquidity absorption

Directional expansion beginning

Option pricing inefficiencies

Fresh volatility expansion

Emerging trend transitions

Market regime shifts

The objective is to participate in the strongest directional portion of the move while minimizing:

Theta decay

Late entries

Volatility crush

Poor reward-to-risk

Chasing extended moves

The application should continuously seek asymmetric opportunities where the potential upside significantly exceeds the downside.

---

# Segment-Specific Intelligence

Do not force one strategy across every market.

Different markets behave differently.

The application should recognize this.

For example:

NSE Index Options may require different logic than NSE Stock Options.

US Index Options may behave differently from US Equity Options.

Volatility characteristics differ.

Liquidity differs.

Expiration behavior differs.

Institutional participation differs.

Market sessions differ.

Each segment should be free to evolve its own optimized decision framework.

---

# Strategy Research Framework

The application should behave like an automated quantitative research platform.

Whenever new ideas or rule combinations are introduced, they should first undergo historical validation.

The application should automatically evaluate:

Win rate

Profit factor

Maximum drawdown

Sharpe ratio

Sortino ratio

Expectancy

Average winner

Average loser

Holding time

Trade frequency

Risk-adjusted returns

Stability

Consistency

Confidence intervals where applicable

Only strategies demonstrating statistically meaningful performance should be eligible for paper trading.

Avoid curve fitting and over-optimization. Favor robust, repeatable behavior across different market regimes.

---

# Trade Selection

The system should never force trades.

No trade is a valid decision.

The application should wait patiently until multiple high-confidence factors align.

Quality is more important than quantity.

Trade frequency should naturally emerge from opportunity quality.

---

# Option Contract Selection

The application should intelligently determine the most appropriate contract.

Selection should consider:

Expiry

Liquidity

Bid-ask spread

Delta

Gamma

Theta

Vega

Open Interest

IV

Premium efficiency

Expected move

Probability

Directional conviction

Capital efficiency

Avoid illiquid contracts.

Avoid excessive spreads.

Avoid contracts likely to suffer excessive theta decay.

---

# Risk Management

Risk management is one of the highest priorities.

The application should include professional-grade controls including:

Dynamic position sizing

Maximum risk per trade

Daily loss limits

Weekly loss limits

Maximum drawdown controls

Exposure limits

Capital preservation

Maximum simultaneous trades

Correlation-aware exposure

Volatility-adjusted sizing

Automatic stop-loss

Automatic trailing stop

Partial exits when appropriate

Profit targets

Time-based exits

Event-based exits

Emergency risk shutdown

Circuit breaker logic

No revenge trading

No averaging down

No uncontrolled pyramiding

Risk should always be determined before trade entry.

---

# Trade Lifecycle

Every trade should move through a complete lifecycle.

Opportunity discovered

Opportunity scored

Risk evaluated

Contract selected

Position sized

Paper order placed

Trade monitored

Risk adjusted

Exit executed

Performance analyzed

Trade archived

Learning generated

The application should maintain complete traceability for every decision.

---

# Paper Trading Engine

The paper trading engine should realistically simulate execution.

It should account for:

Bid/ask spread

Slippage

Order fill assumptions

Partial fills where appropriate

Market liquidity

Order timing

Execution latency assumptions

Transaction costs if applicable

The objective is to produce results that closely resemble real-world execution rather than idealized fills.

---

# Trade Storage

Every paper trade should be stored permanently.

Each trade record should capture as much contextual information as possible.

Examples include:

Timestamp

Market

Segment

Underlying

Option contract

Strike

Expiry

Direction

Entry price

Exit price

Position size

Premium paid

Greeks at entry

Greeks at exit

Implied volatility

Open Interest

Volume

Delta

Volume profile state

Market regime

Order flow observations

Liquidity conditions

Institutional signals

Trade rationale

Risk parameters

Expected reward

Actual reward

Holding duration

Exit reason

Profit/loss

Maximum favorable excursion

Maximum adverse excursion

Screenshots or chart references if supported

Strategy version

Feature values used by the decision engine

Confidence score

The objective is to create an extremely rich historical dataset for future AI training and analysis.

---

# AI-Assisted Research Loop (Manual Review System)

The application should **not include any autonomous AI feedback loop or self-learning mechanism inside the system.** Instead, all learning, analysis, and improvement decisions will be handled externally by manually providing trade history and logs to Claude Code for review.

The platform’s responsibility is strictly to **capture, structure, and preserve all trading data in a clean, consistent, and highly detailed format** so that external analysis can be performed without ambiguity or missing context.

The system must maintain a complete and immutable record of all trades, including full execution details, market context, decision inputs, and outcome metrics. This ensures that when trade history and logs are shared externally, Claude Code can accurately analyze:

what strategies are working,
what should be improved,
what should be discarded,
and what patterns are emerging over time.

All trade history and logs must be stored in a **well-structured, standardized, and queryable format** to avoid confusion, misinterpretation, or data loss during analysis.

The platform should ensure:

* Every trade is fully traceable from signal generation to exit
* All decision inputs and market conditions are preserved
* Logs are consistent across all four trading segments
* Data is normalized and easy to export for external review
* No critical context is ever omitted or overwritten

This creates a disciplined workflow:

Observe → Store → Export → External Analyze (Claude Code) → Manual Improve → Update System

All improvements, strategy changes, and optimizations will be applied only after external review and validation.

---

# Performance Analytics

The application should continuously calculate comprehensive statistics.

Examples include:

Daily P&L

Weekly P&L

Monthly P&L

Annual P&L

Win rate

Profit factor

Expectancy

Drawdown

Recovery factor

Sharpe ratio

Sortino ratio

Average trade

Best trade

Worst trade

Capital growth

Trade frequency

Average holding time

Strategy comparison

Segment comparison

Heatmaps

Distribution analysis

Equity curves

Rolling performance

Risk-adjusted returns

Performance by market regime

Performance by weekday

Performance by session

Performance by volatility regime

Performance by expiration

Performance by strike selection

---

# Dashboard

The application should provide an intuitive modern dashboard.

There should be five primary dashboards.

## Master Dashboard

Displays:

Overall P&L

Combined equity

Combined exposure

Combined statistics

Segment comparison

Portfolio allocation

Capital utilization

Recent trades

Performance charts

Risk summary

Current opportunities

System health

Data feed status

AI insights

Strategy health

---

## NSE Stock Dashboard

Displays:

Current market state

Live opportunities

Open paper positions

Trade history

Performance

Risk

Charts

Analytics

Strategy statistics

Capital utilization

---

## NSE Index Dashboard

Dedicated dashboard with the same level of detail tailored specifically to NSE index option trading.

---

## US Stock Dashboard

Dedicated dashboard with the same level of detail tailored specifically to US stock option trading.

---

## US Index Dashboard

Dedicated dashboard with the same level of detail tailored specifically to US index option trading.

---

# Logging & Observability

Every important event should be logged.

Examples include:

Market ingestion

Signal generation

Strategy decisions

Risk decisions

Trade execution

Trade exits

AI analysis

Errors

Warnings

API failures

Performance bottlenecks

Health checks

The application should expose clear diagnostics to simplify debugging and maintenance.

---

# Reliability

The platform should be resilient to failures.

It should gracefully handle:

API outages

Rate limits

Missing market data

Delayed feeds

Unexpected responses

Network interruptions

Clock synchronization issues

Duplicate data

Corrupted records

Partial updates

Recovery after restart

No single failure should compromise the integrity of the system or historical data.

---

# Scalability

Design the system so additional markets, brokers, exchanges, data providers, AI models, or trading strategies can be integrated without major architectural changes.

The application should be modular and extensible, allowing independent evolution of ingestion, analysis, strategy evaluation, risk management, execution simulation, analytics, AI research, and user interface components.

---

# Code Quality Expectations

The project should reflect production-grade engineering standards.

Use clean architecture and strong separation of concerns.

Write maintainable, well-documented, and testable code.

Prefer composition over tight coupling.

Avoid duplicated logic.

Use robust validation, comprehensive error handling, meaningful logging, and automated testing where appropriate.

The codebase should remain easy to understand, extend, and maintain as the platform grows.

---

# Technology Stack & Architecture

The system should primarily be built using **Python** and **FastAPI**, with a clear separation of responsibilities between general Python usage and FastAPI-specific usage:

- **Python** should be used for all core computational, analytical, and domain-heavy logic, including quantitative research, strategy development, backtesting, and risk management.
- **FastAPI** should be used specifically for API layer development, including REST endpoints, WebSocket communication, and dashboard-facing services where low-latency request/response handling and async performance are required.

---

## Component-wise Technology Mapping

| Component | Recommended Technology | Why |
|---|---|---|
| Trading API | Python + FastAPI | Fast development, async support, rich ecosystem |
| Quantitative Analysis | Python | NumPy, SciPy, Polars, Pandas, TA-Lib, Statsmodels |
| Strategy Engine | Python | Easier to iterate and research |
| Backtesting | Python | Mature quant ecosystem (Backtrader-based) |
| Risk Engine | Python | Easier to maintain and modify |
| Dashboard API | FastAPI | Excellent REST/WebSocket support |
| High-frequency computations (only if needed) | C++ | For CPU-intensive algorithms |

---

## Core Infrastructure Stack

| Layer | Technology |
|---|---|
| Backend | Python (FastAPI for API layer, Python modules for all core logic) |
| Frontend | Next.js (React + TypeScript + Tailwind CSS + Shadcn/UI) |
| Database | PostgreSQL with TimescaleDB for time-series market data |
| Cache | Redis |
| Background Jobs | Celery |
| Object Storage | S3-compatible storage (MinIO) |
| Charts | TradingView Lightweight Charts |
| Communication | REST APIs and WebSockets |

---

## Recommended Python Libraries

Unless there is a compelling technical reason to choose otherwise, use the following libraries for their respective responsibilities:

### Numerical Computing

- NumPy
- SciPy
- Numba

### Data Processing

- Polars (preferred)
- Pandas (for ecosystem compatibility where required)

### Time-Series Analysis

- TimescaleDB (database layer)
- Polars
- Pandas

### Technical Indicators

- TA-Lib

### Statistical Analysis

- SciPy
- Statsmodels

### Optimization

- Optuna (for hyperparameter optimization and strategy tuning)

### Backtesting Framework

- Backtrader (primary engine)
- Extended with custom modules for:
  - Options strategies
  - Greeks modeling
  - Multi-asset portfolios
  - Institutional execution simulation

### Performance Analytics

- QuantStats
- PyFolio (where compatible)

### Options Pricing & Greeks

- py_vollib (or equivalent actively maintained library)
- Custom implementations where required for advanced or institutional-grade modeling

---

## Architectural Principle

This architecture ensures that:

- **Python remains the primary language** for all financial intelligence, research, and computation.
- **FastAPI is strictly an interface layer**, not a business logic layer.
- The system remains **modular, extensible, and research-driven**.
- **Quantitative rigor** is prioritized over framework complexity.

---

# Overall Vision

This application should function as an **institutional-grade, AI-assisted options buying research and paper trading platform** rather than a simple signal generator or automated trading bot. It should continuously ingest market data, interpret market structure, identify high-probability directional opportunities early, simulate realistic option trades, enforce strict risk management, collect exhaustive contextual data for every decision, and use that history to drive evidence-based strategy refinement.

The ultimate goal is to create a self-improving quantitative research ecosystem capable of discovering, validating, and executing high-quality **Options Buying** opportunities across **NSE Stocks, NSE Indices, US Stocks, and US Indices**, while maintaining complete transparency, explainability, robustness, and production-level software quality.
