# 🎬 Greenlight Studio
> **Autonomous Film ROI & Production Risk Simulator**  
> *Built for "Agentic Cinema: The Blockbuster Hackathon" (ClickHouse Track)*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Powered by Google Cloud](https://img.shields.io/badge/Google%20Cloud-Gemini%20Enterprise-4285F4)](https://cloud.google.com)
[![Powered by ClickHouse](https://img.shields.io/badge/ClickHouse-Fast%20OLAP%20%26%20Vector-FFAA00)](https://clickhouse.com)
[![Studio S.O](https://img.shields.io/badge/Studio-Studio%20S.O-2F3E46)](https://sutekioojisan-so.com)

---

## 💡 Overview & Inspiration
Greenlighting a \$50M–\$200M blockbuster film is often a high-stakes gamble driven by intuition, siloed opinions, or months of fragmented research. **Greenlight Studio** transforms this uncertainty into data-driven confidence.

By orchestrating **Gemini Enterprise Multi-Agents** connected via **Model Context Protocol (MCP)** to **ClickHouse Cloud**, Greenlight Studio parses raw screenplays and matches them against 50+ years of historical box office, cast performance, and weekly trend metrics in milliseconds.

```
[ Screenplay PDF / Text ]
          │
          ▼
┌────────────────────────────────────────────────────────┐
│  Gemini Enterprise Agent Platform (Studio Head)        │
│   ├── Script Analyst Agent (Tone, Pacing, VFX Load)    │
│   ├── Market Comps Agent (Vector Search in 8.7ms)      │
│   ├── Budget & ROI Agent (Bear/Base/Bull Scenarios)   │
│   └── Cast & Release Advisor (Casting Synergy)         │
└───────────────────────┬────────────────────────────────┘
                        │ MCP Protocol (SQL & Vector)
                        ▼
┌────────────────────────────────────────────────────────┐
│  ⚡ ClickHouse Cloud (Agent's Real-Time Memory)        │
│   ├── 50-Year Historical Movies & Vector Embeddings   │
│   ├── Cast & Director ROI Power Scores                │
│   └── Granular Theatrical Weekly Trajectories         │
└────────────────────────────────────────────────────────┘
          │
          ▼
[ 📑 Executive Greenlight Dossier Dashboard ]
```

---

## 🌟 Key Features

1. **Sub-second Vector Similarity & Comps Retrieval**:
   - Compares 768-dimensional script tone embeddings against thousands of movies in **under 10 milliseconds** using ClickHouse vector distance functions.
2. **Autonomous Multi-Agent Orchestration**:
   - 4 specialized agents collaborate asynchronously to dissect story structure, forecast financial outcomes, and stress-test budgets.
3. **Probabilistic ROI Simulation**:
   - Generates Bear, Base, and Bull gross projections with break-even probability distribution curves.
4. **Data-Driven Casting Recommendations**:
   - Suggests high-synergy actors and directors based on historical genre-specific multiplier metrics.

---

## 🚀 Architecture & Tech Stack

* **AI & Agent Orchestration**: Google Cloud Gemini Enterprise Agent Platform, Vertex AI, FastMCP
* **Database & Analytical Memory**: ClickHouse Cloud (OLAP Engine + Vector Search)
* **Backend**: Python, FastMCP Server, FastAPI
* **Frontend**: Next.js 14 (App Router), Tailwind CSS, Recharts, shadcn/ui

---

## 🛠️ Quick Start

### 1. Prerequisites
- Python 3.10+
- Node.js 18+
- ClickHouse Cloud Account (or Local Memory Simulator)

### 2. Backend Setup
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env

# Generate realistic datasets and verify MCP pipeline
python test_pipeline.py

# Start FastMCP Server
python mcp_server/server.py
```

### 3. Frontend Setup (Coming in Phase 2)
```bash
cd frontend
npm install
npm run dev
```

---

## 👥 Creator & Team

* **Studio S.O (スタジオ・エスオー)**
  * **Founder & AI Lead**: Hisayuki Tsue (津江 久幸)
  * **Devpost**: [@studioso928](https://devpost.com/studioso928)
  * **GitHub**: [@studioso-tech](https://github.com/studioso-tech)
  * **Website**: [https://sutekioojisan-so.com](https://sutekioojisan-so.com)
  * **Email**: studioso@sutekioojisan-so.com

---

## 📜 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
