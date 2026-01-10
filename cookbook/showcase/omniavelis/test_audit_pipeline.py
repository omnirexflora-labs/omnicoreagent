from agents.rule_checker_agent import RuleCheckerAgent
from agents.evidence_agent import EvidenceAgent
from agents.audit_synth_agent import AuditSynthAgent
from agents.appeals_agent import AppealAgent
import asyncio
import xml.etree.ElementTree as ET
import json
from datetime import datetime


print(r"""
██████╗ ███████╗███╗   ██╗██╗ █████╗ ██╗   ██╗██████╗ ██╗████████╗
██╔═══██╗████╔██║████╗  ██║██║██╔══██╗██║   ██║██╔══██╗██║╚══██╔══╝
██║   ██║██╔████╝██╔██╗ ██║██║███████║██║   ██║██║  ██║██║   ██║   
██║   ██║████╔██║██║╚██╗██║██║██╔══██║██║   ██║██║  ██║██║   ██║   
╚██████╔╝███████╔╝██║ ╚████║██║██║  ██║╚██████╔╝██████╔╝██║   ██║   
 ╚═════╝ ╚══════╝ ╚═╝  ╚═══╝╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚═╝   ╚═╝

Project: OmniAudit – Autonomous AI Claim Audit System for Avelis
Author: Abiorh
Framework: OmnicoreAgent — Fully Custom Multi-Agent Orchestration Engine

Why I Built This:
-----------------
Avelis states: “Every year, your health plan pays millions in erroneous claims… We deploy AI to find nuanced billing errors.”

OmniAudit is my working implementation of that promise — built entirely on my own AI agent framework to demonstrate I can engineer the core of your product.

How It Works:
-------------
1. **RuleCheckerAgent**  
   → Audits claims against plan rules (duplicates, non-covered codes, thresholds)  
2. **EvidenceAgent**  
   → Retrieves bulletproof policy evidence (e.g., “Plan A Handbook, Section 5.1”)  
3. **AuditSynthAgent**  
   → Synthesizes violations + evidence into a compliant audit packet  
4. **AppealAgent**  
   → Auto-generates professional provider appeal letters with clear instructions  

Pipeline Flow:
--------------
Claim → RuleChecker → Evidence → AuditSynth → Appeal → Provider

Key Capabilities:
-----------------
Catches nuanced billing errors (duplicates, 80050, threshold breaches)  
Generates ERISA-compliant audit trails with policy citations  
Manages end-to-end provider appeals (as Avelis promises)  
Supports 501(r) charity care screening (via plan rules)  
Built on a 100% custom with OmniCoreAgent, session-aware, XML-driven agent framework  

Status:
-------
All agents operational | End-to-end tested | Ready to scale with Avelis
""")


def extract_violations_for_evidence(rule_checker_response: str, original_claim: dict):
    try:
        if not rule_checker_response.strip().startswith("<"):
            raise ValueError("Invalid XML response")
        root = ET.fromstring(f"<root>{rule_checker_response}</root>")
        violations_elem = root.find("violations") or root
        rule_ids, claim_id = [], None
        for violation in violations_elem.findall("violation"):
            rule_id = violation.findtext("rule_id")
            if rule_id and rule_id not in rule_ids:
                rule_ids.append(rule_id)
            if claim_id is None:
                claim_id = violation.findtext("claim_id")
        return (
            None
            if not rule_ids
            else {
                "claim_id": claim_id or original_claim["claim_id"],
                "plan_id": original_claim["plan_id"],
                "rule_ids": rule_ids,
            }
        )
    except ET.ParseError as e:
        print(f"[ERROR] Failed to parse XML: {e}")
        return None


# === Test Cases ===
TEST_CASES = [
    # Test 1 Duplicate + Non-Covered Code
    {
        "name": "duplicate_and_non_covered",
        "claim": {
            "claim_id": "C10045",
            "member_id": "M8892",
            "patient_id": "P8892",
            "provider_id": "PRV442",
            "plan_id": "PLAN_A",
            "member_status": "active",
            "service_date": "2025-10-15",
            "total_billed": 1600.00,
            "diagnosis": "E11.9",
            "procedure_lines": [
                {
                    "line_id": "L1",
                    "code": "99213",
                    "billed": 120.00,
                    "diagnosis": "E11.9",
                },
                {"line_id": "L2", "code": "80050", "billed": 150.00},
            ],
        },
        "historical_claims": [
            {
                "claim_id": "C10012",
                "patient_id": "P8892",
                "provider_id": "PRV442",
                "service_date": "2025-10-15",
                "procedure_lines": [{"code": "99213", "billed": 100.00}],
            }
        ],
        "provider_email": "billing@prv442-clinic.com",
    },
    # Test 2 501(r) Charity Care Eligible — But Not Applied
    {
        "name": "charity_care_eligible",
        "claim": {
            "claim_id": "C20088",
            "member_id": "M7721",
            "patient_id": "P7721",
            "provider_id": "PRV331",
            "plan_id": "PLAN_A",
            "member_status": "active",
            "service_date": "2025-09-20",
            "total_billed": 2500.00,
            "diagnosis": "J45.90",
            "procedure_lines": [
                {
                    "line_id": "L1",
                    "code": "99214",
                    "billed": 125.00,
                    "diagnosis": "J45.90",
                },
                {
                    "line_id": "L2",
                    "code": "70450",
                    "billed": 210.00,
                    "diagnosis": "J45.90",
                },
            ],
            "income_fpl_pct": 200,
        },
        "historical_claims": [],
        "provider_email": "billing@prv331-hospital.org",
    },
    # Test 3 Frequency Limit + Missing Diagnosis
    {
        "name": "frequency_limit_exceeded",
        "claim": {
            "claim_id": "C30112",
            "member_id": "M5543",
            "patient_id": "P5543",
            "provider_id": "PRV555",
            "plan_id": "PLAN_A",
            "member_status": "active",
            "service_date": "2025-11-01",
            "total_billed": 85.00,
            "procedure_lines": [
                {
                    "line_id": "L1",
                    "code": "99213",
                    "billed": 85.00,
                }
            ],
        },
        "historical_claims": [
            {
                "claim_id": f"C3010{i}",
                "patient_id": "P5543",
                "provider_id": "PRV555",
                "service_date": f"2025-0{i}-15",
                "procedure_lines": [{"code": "99213", "billed": 85.00}],
            }
            for i in range(1, 5)
        ],
        "provider_email": "office@prv555-clinic.com",
    },
]


async def run_single_test(test_case, session_id, agents):
    rule_checker, evidence, audit_synth, appeal = agents
    claim_data = {
        "claim": test_case["claim"],
        "historical_claims": test_case["historical_claims"],
    }

    rule_result = await rule_checker.run(claim_data=claim_data, session_id=session_id)

    evidence_input = extract_violations_for_evidence(
        rule_result["response"], test_case["claim"]
    )
    if not evidence_input:
        evidence_result = {"response": "<evidence></evidence>"}
    else:
        evidence_result = await evidence.run(
            evidence_request=evidence_input, session_id=session_id
        )

    audit_result = await audit_synth.run(
        claim=test_case["claim"],
        violations_xml=rule_result["response"],
        evidence_xml=evidence_result["response"],
        session_id=session_id,
    )

    appeal_result = await appeal.run(
        claim=test_case["claim"],
        final_audit_xml=audit_result["response"],
        provider_email=test_case["provider_email"],
        session_id=session_id,
    )

    return {
        "input": claim_data,
        "rule_checker_output": rule_result["response"],
        "evidence_output": evidence_result["response"],
        "audit_output": audit_result["response"],
        "appeal_output": appeal_result["response"],
    }


async def main():
    agents = (RuleCheckerAgent(), EvidenceAgent(), AuditSynthAgent(), AppealAgent())

    # Initialize all agents
    for agent in agents:
        await agent.initialize_mcp_servers()

    all_results = []
    try:
        for i, test_case in enumerate(TEST_CASES, 1):
            print(f"\n{'=' * 80}")
            print(f"🧪 RUNNING TEST CASE {i}: {test_case['name']}")
            print(f"{'=' * 80}")

            session_id = f"test_{i}_{test_case['name']}"
            result = await run_single_test(test_case, session_id, agents)
            result["test_name"] = test_case["name"]
            all_results.append(result)

            print(f"\n✅ Test {i} Appeal Result:")
            print(result["appeal_output"])

    finally:
        # Cleanup
        for agent in agents:
            await agent.cleanup_mcp_servers()

    log_data = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "framework": "OmnicoreAgent",
        "project": "OmniAudit for Avelis",
        "test_cases": all_results,
    }

    with open("demo_audit_log.json", "w") as f:
        json.dump(log_data, f, indent=2)

    print(f"\n{'=' * 80}")
    print("✅ ALL TESTS COMPLETED!")
    print("📄 Full audit log saved to: demo_audit_log.json")
    print("📊 Use this file to showcase end-to-end capabilities to Avelis.")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    asyncio.run(main())
