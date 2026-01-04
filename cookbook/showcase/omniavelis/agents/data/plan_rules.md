# Plan Policy Rules – Evidence Source (v2.0)

---

## PLAN_A: Comprehensive Commercial PPO

### RULE_001: Duplicate Claim Detection  
**Source ID**: `PLAN_A_RULE_001`  
A claim is considered a duplicate if the same patient, provider, date of service, and CPT/HCPCS code appear in a previously paid claim within the same benefit year. Duplicate services require new clinical documentation to justify medical necessity.

### RULE_002: Service Frequency Limit  
**Source ID**: `PLAN_A_RULE_002`  
- Office visits (99213–99215): Maximum of 6 established patient visits per calendar year.  
- CT Head without contrast (70450): Limited to 1 per 12-month rolling period.  
Exceeding these limits without prior authorization results in denial of excess units.

### RULE_003: Missing Required Diagnosis Code  
**Source ID**: `PLAN_A_RULE_003`  
All procedure lines must include at least one valid ICD-10-CM diagnosis code that aligns with CMS medical necessity guidelines. Claims missing diagnosis codes will be returned for correction.

### RULE_004: Ineligible Member  
**Source ID**: `PLAN_A_RULE_004`  
Services are covered only if the member’s policy was active on the date of service. Claims submitted for members with terminated, suspended, or pending coverage status will be denied in full.

### RULE_005: Service Not Covered  
**Source ID**: `PLAN_A_NON_COVERED_CODES`  
The following services are explicitly excluded from coverage under Plan A:  
- `80050`, `80048` – General health panels and comprehensive metabolic panels  
- `G0101` – Cervical cancer screening (covered only under preventive benefit with specific modifiers)  
- `X9999`, `S9999` – Non-standard or experimental codes  
> *Source: Plan A Benefits Handbook, Section 5.1 & 7.3*

### RULE_006: Claim Amount Threshold  
**Source ID**: `PLAN_A_CLAIM_THRESHOLD`  
Total billed amount per claim must not exceed **$1,500.00**. Claims exceeding this threshold require itemized line-level breakdown and financial review.

### RULE_007: Missing Place of Service  
**Source ID**: `PLAN_A_RULE_007`  
All professional claims must include a valid Place of Service (POS) code (e.g., 11 = office, 22 = outpatient hospital). Missing POS will result in claim rejection.

### RULE_008: 501(r) Charity Care Eligibility  
**Source ID**: `PLAN_A_CHARITY_CARE`  
Patients with household income at or below 250% of the Federal Poverty Level (FPL) are eligible for full or sliding-scale financial assistance under Plan A’s 501(r) compliance policy. Claims for eligible patients must be adjusted to $0 or discounted accordingly. Failure to apply charity care results in overpayment.

---

## PLAN_B: High-Deductible Health Plan (HDHP)

### RULE_001: Duplicate Claim Detection  
**Source ID**: `PLAN_B_RULE_001`  
Duplicate claims are denied unless new medical records demonstrate changed clinical circumstances justifying repeat service.

### RULE_003: Missing Required Diagnosis Code  
**Source ID**: `PLAN_B_RULE_003`  
Diagnosis codes are mandatory for all billed services. Missing or invalid ICD-10 codes will result in claim return.

### RULE_005: Service Not Covered  
**Source ID**: `PLAN_B_NON_COVERED_CODES`  
Excluded services include:  
- `80050`, `80047` – Routine lab panels not tied to specific diagnosis  
- `S9999` – Non-standard codes  
All other CPT codes are covered subject to deductible and coinsurance.

### RULE_006: Claim Amount Threshold  
**Source ID**: `PLAN_B_CLAIM_THRESHOLD`  
Maximum allowable per-claim billed amount: **$1,000.00**. Higher amounts require pre-submission review.

### RULE_007: Missing Place of Service  
**Source ID**: `PLAN_B_RULE_007`  
Place of Service (POS) and referring provider NPI are required fields. Omission will cause rejection.

---

## PLAN_C: Medicaid Managed Care

### RULE_003: Missing Required Diagnosis Code  
**Source ID**: `PLAN_C_RULE_003`  
Diagnosis codes must be submitted with all claims. Medicaid requires ICD-10 codes for encounter tracking and quality reporting.

### RULE_005: Service Not Covered  
**Source ID**: `PLAN_C_NON_COVERED_CODES`  
Non-standard codes (`S9999`, `X9999`) are not reimbursable under state Medicaid guidelines.

### RULE_006: Claim Amount Threshold  
**Source ID**: `PLAN_C_CLAIM_THRESHOLD`  
Threshold: **$2,000.00** per claim. Higher thresholds reflect Medicaid’s bundled payment model.

---

## Global Rules (Apply to All Plans)

### RULE_002: Service Frequency Limit (General)  
**Source ID**: `GLOBAL_RULE_002`  
Certain high-cost or high-volume services (e.g., imaging, office visits) are subject to annual utilization limits. Providers must monitor usage and obtain prior authorization when approaching limits.

### RULE_004: Ineligible Member (General)  
**Source ID**: `GLOBAL_RULE_004`  
Coverage eligibility is determined as of the date of service. Retroactive terminations do not invalidate otherwise eligible claims.

> ℹ️ All policies comply with CMS, state DOI, and NAIC model regulations.