# 🔍 AI Due Diligence Agent

A professional multi-agent pipeline for automated startup investment analysis. This showcase demonstrates the power of **OmniCoreAgent** in orchestrating complex, data-driven workflows.

## 🌟 Overview

The AI Due Diligence Agent automates the grueling process of early-stage investment analysis. It orchestrates 7 specialized agents to transform a simple company name into a comprehensive investment package.

### 🤖 The Agent Team

| Agent | Responsibility | Tools |
| :--- | :--- | :--- |
| **Researcher** | Gathers company intel, funding history, and team data. | Tavily Search (MCP) |
| **Market Analyst** | Analyzes TAM, SAM, competitors, and industry trends. | Tavily Search (MCP) |
| **Financial Modeller** | Builds 5-year revenue projections (Bear/Base/Bull). | Financial Chart Tool |
| **Risk Specialist** | Conducts a "brutal" assessment of market and execution risks. | Internal Analysis |
| **Memo Writer** | Synthesizes all findings into a professional narrative. | Internal Synthesis |
| **Report Generator** | Publishes a McKinsey-style HTML Investment Report. | HTML Report Tool |
| **Infographic Gen** | Creates a high-impact visual dashboard (PNG). | Infographic Tool |

## 🚀 Features

- **Professional Visuals**: Generates data-accurate charts and Goldmann Sachs-style infographics using Matplotlib.
- **McKinsey-Style Reports**: Produces polished, print-ready HTML reports with full markdown support.
- **Scenario Analysis**: Models three financial outcomes (pessimistic, expected, optimistic).
- **Deep Research**: Leverages Tavily's real-time search via MCP for up-to-date startup intelligence.
- **Manual Orchestration**: Showcases precise control over data flow between agents.

## 🛠️ Setup

1. **Install Dependencies**:
   ```bash
   pip install omnicoreagent matplotlib numpy pillow
   ```

2. **Configure Environment**:
   Create a `.env` file with your API keys:
   ```env
   LLM_API_KEY=your_openai_key
   TAVILY_API_KEY=your_tavily_key
   ```

## 📖 Usage

Run the workflow from the terminal. The system supports full CLI control over models and providers.

```bash
# Basic usage (Interactive)
python due_diligence_workflow.py

# Direct analysis (Positional)
python due_diligence_workflow.py "Agno AI"

# Advanced usage (Model & Provider control)
python due_diligence_workflow.py "OpenAI" --model gpt-4o --provider openai

# Full options
python due_diligence_workflow.py --help
```

### CLI Arguments

- `company`: Name of the company to analyze (positional or `--company`)
- `-m, --model`: The LLM model to use (default: `gpt-4o`)
- `-p, --provider`: The LLM provider (default: `openai`)
- `-q, --quiet`: Suppress progress output and only show the final summary.


## 📁 Output

The workflow generates three primary artifacts in the `outputs/` directory:
1. `investment_report_[Company]_[Date].html`: A full-length professional report.
2. `infographic_[Company]_[Date].png`: A one-page visual executive summary.
3. `revenue_chart_[Company]_[Date].png`: A detailed financial projection chart.

---
*Built with [OmniCoreAgent](https://github.com/omnirexflora-labs/omnicoreagent)*
