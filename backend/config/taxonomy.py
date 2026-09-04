"""Document taxonomy (spec §24).

Configurable and defined once. Seeded into ``document_categories``; the
classifier reads the active rows from the database, never this list directly.
"""

from __future__ import annotations

DEFAULT_TAXONOMY: list[dict[str, object]] = [
    {"slug": "administrative-internal", "name": "Administrative & Internal", "sort_order": 10,
     "description": "Internal memos, circulars, general administration."},
    {"slug": "finance-procurement", "name": "Finance & Procurement", "sort_order": 20,
     "description": "Invoices, purchase orders, budgets, vendor agreements."},
    {"slug": "legal-compliance", "name": "Legal & Compliance", "sort_order": 30,
     "description": "Contracts, regulatory filings, compliance policies."},
    {"slug": "job-role", "name": "Job & Role", "sort_order": 40,
     "description": "Job descriptions, role definitions, competency frameworks."},
    {"slug": "recruitment-hiring", "name": "Recruitment & Hiring", "sort_order": 50,
     "description": "CVs, interview records, offer letters."},
    {"slug": "employee-identity-personal", "name": "Employee Identity & Personal", "sort_order": 60,
     "description": "Identity documents such as PAN and Aadhaar cards, address proof."},
    {"slug": "previous-employment", "name": "Previous Employment", "sort_order": 70,
     "description": "Experience letters, relieving letters, references."},
    {"slug": "payroll-benefits", "name": "Payroll & Benefits", "sort_order": 80,
     "description": "Payslips, tax forms, insurance and benefits documents."},
    {"slug": "performance-conduct", "name": "Performance & Conduct", "sort_order": 90,
     "description": "Appraisals, disciplinary records, conduct policies."},
    {"slug": "exit-offboarding", "name": "Exit & Offboarding", "sort_order": 100,
     "description": "Resignations, exit interviews, clearance forms."},
]

UNCATEGORISED_SLUG = "uncategorised"
