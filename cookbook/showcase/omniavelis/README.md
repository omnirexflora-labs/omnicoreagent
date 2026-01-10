# 🚀 OmniAudit  
### Autonomous Multi-Agent AI Claim Audit System — Built for **Avelis**

> _“Every year, your health plan pays millions in erroneous claims… We deploy AI to find nuanced billing errors.”_  
> — **[Avelis](https://avelishealth.com/)**

**OmniAudit** is my working implementation of that promise — a **fully autonomous, end-to-end AI audit pipeline** built entirely on my custom **[OmnicoreAgent](https://github.com/Abiorh001/omnicoreagent)** framework. It demonstrates I can engineer the core AI system behind **Avelis’ product from day one**.


![OmniAudit Architecture](https://www.plantuml.com/plantuml/png/XLJHRYet47tVhvZoKb03lFJfFbqADuqgQ12aKXygMMOzi6wyziepIqghIls7_c6_fBQNB3WXkdd9pEoUS-mvP_bCB3stjIc-qROqhKAec1lwD9rw-J8vQAxQNKleIsSPBKzALqyR8st4Z8cLqRlf_FlnEev-MIsdjIH67qPY7X1IM_IJ9l8NQ_JahFZo1myRxuvQeR-3mGDAmrKfFSBwF1s4yRRTrPe9IYDrJI2j0eybwYE6fca220noKAB1g2Ysfo1QYnzSQvNqfw6hhPQjqXoO7aCzYpNyzy-_C6lPMLUxbc0UgQ5xr14CHl1N0U6lF_hGjmP5MM7vd4wx2UsywsO7j1oFkZvnvMoilXobQINZvTY7m48CSM4GgUWtEroqZT5bVEqEkF98bJCgrDRPSUcEw551wHJIA5mjFmt6_TWIarPNtZZ6FN8uCK7ZWiu9Na6mzww6P-jU3Ae3mauIlbD8MnDqibm5fhISRMnkoAGvalupI1srCvArimJVNGIrPGU94HePTiRlvPALKIaLDrH-nF2UPEmoG6WLmMjyRYGXer_iadyar2voHXCsiOVXJzl7dwUhnIXfTMjIkDTMfpiagTwQw5aqK2IkoU_ax1rS3MeLZVgxJs4CEDPI6q5f4CsAxMl8Ghz0euFHRwCCGz9rQoHZeYo-XOLa2VV6lL2nGIEMLrzEdipyWmacL9hO-P0pquqewiPh1-98jNaJxQj5RL2gzFgRMt-0l0hbDjdJXo6H_BfQde7BFhzVbQjLBtR1vDbCMhruhhJ9SzodB67oHEQuFf2vWHqmToiY7vvMIqZczbVCWRrT5yy7VTfXsH4bfv-SCp2prl7vEnfAWj0XZOhu3xm-L0nkdnbVG0BIfr2C8Gr5ibkSVtgwVQQd2TTx8umx7081MfLaYbobNsoki8y_BQ8tIf3LuIfC7z9qAyj9uaSikWSYhUiA_JcKRMt-1m00)


---

## ✨ Why This Matters

**Avelis** solves **payment integrity** for self-insured employers by:
- Catching **nuanced billing errors** without member disruption  
- Providing **bulletproof documentation** with plan contract references  
- Managing **provider appeals end-to-end**  
- Enforcing **ERISA fiduciary compliance**  
- Recovering **501(r) financial assistance** savings  

**OmniAudit implements all of this — in code.**

| Avelis Capability | OmniAudit Implementation |
|------------------|--------------------------|
| **“Catch nuanced billing errors”** | Detects duplicates, non-covered codes (`80050`), threshold breaches, missing diagnosis |
| **“Bulletproof documentation”** | EvidenceAgent cites real policy: *“Plan A Handbook, Section 5.1”* |
| **“Manage appeals end-to-end”** | AppealAgent auto-generates & simulates sending professional denial letters |
| **“501(r) Financial Assistance”** | `charity_care` logic embedded in `plan_rules.json` |
| **“ERISA Compliance & Fiduciary Protection”** | Full audit trail with agent provenance, timestamps, and recovery logic |

This isn’t a demo — it’s a **minimal viable replica of Avelis’ Post-Payment Audit Engine**.

---

## 🤖 The Agent Pipeline

[Claim]
↓
RuleCheckerAgent → EvidenceAgent → AuditSynthAgent → AppealAgent
↓ ↓ ↓ ↓
Violations Policy Evidence Audit Packet Provider Letter

### 1. **RuleCheckerAgent**
- Audits claims against **plan-specific rules** (PLAN_A, PLAN_B)
- Detects: duplicates, non-covered codes, amount thresholds, missing diagnosis
- Output: structured XML violations with `recommended_recovery`

### 2. **EvidenceAgent**
- Retrieves **human-readable policy snippets** from `plan_rules.md`
- Cites sources: *“Plan A Benefits Handbook, Section 5.1”*
- Ensures every denial is **defensible, transparent, and ERISA-compliant**

### 3. **AuditSynthAgent**
- Synthesizes violations + evidence into a **final audit packet**
- Computes total recovery, sets `next_steps`, and logs a full **audit trail**

### 4. **AppealAgent**
- Generates **professional, empathetic denial letters**
- Includes: violation summary, policy references, 30-day appeal instructions
- Simulates email delivery to provider — **serving as an extension of your appeals team**

---

## 🧪 Sample Output

**Total Recovery**: `$370.00`  
**Violations**:  
- Duplicate billing (99213)  
- Non-covered service (80050)  
- Missing diagnosis code  
- Claim threshold exceeded ($1,600 > $1,500)

**Appeal Letter Excerpt**:  
> _“CPT 80050 (General Health Panel) is not covered under Plan A, as outlined in the Plan A Benefits Handbook, Section 5.1…”_

✅ **Full audit trail** with timestamps  
✅ **Policy-cited evidence** for every finding  
✅ **30-day appeal instructions** included  

---

## 🛠️ Technical Highlights

- **100% custom framework**: Built on **[OmnicoreAgent](https://github.com/Abiorh001/omnicoreagent)** — no LangChain, no crewAI  
- **Deterministic XML communication**: Reliable parsing, audit-compliant, no hallucination  
- **Session-aware orchestration**: Isolated, stateful agent workflows  
- **Modular design**: Ready to add `CharityCareAgent` (for 501(r)) or `DashboardAgent` (for savings reporting)  
- **Event-driven ready**: Integrates with **OmniDaemon** for background automation  

---

## ▶️ Run the Demo

```bash
git clone https://github.com/Abiorh001/omniaudit.git
cd omniaudit
uv sync
uv run test_audit_pipeline.py