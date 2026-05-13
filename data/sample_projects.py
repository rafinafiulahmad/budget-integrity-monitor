"""
Sample LDC project budgets for demo and testing.
Based on realistic development project patterns.
"""

from core.models import BudgetProject, BudgetLineItem

SAMPLE_PROJECTS = {
    "road_infrastructure": BudgetProject(
        name="Rural Road Network Phase II",
        country="Bangladesh",
        total_budget_usd=8_500_000,
        donor="Asian Development Bank",
        sector="Infrastructure",
        procurement_method="ICB - restricted to ADB member countries",
        beneficiary_count=120_000,
        national_law_reference="Public Procurement Rules (PPR) 2008, Section 32",
        context_notes=(
            "ADB requires use of pre-approved vendor list for equipment supply. "
            "National PPR 2008 mandates open tender above BDT 50 lakh (~USD 45k)."
        ),
        line_items=[
            BudgetLineItem("Consultancy - International Firm", 1_800_000, "consultancy", "foreign"),
            BudgetLineItem("Consultancy - Local Technical Advisor", 700_000, "consultancy", "local"),
            BudgetLineItem("Civil Works - Road Construction", 3_200_000, "civil_works", "local"),
            BudgetLineItem("Equipment - ADB Approved Vendor", 1_200_000, "equipment", "donor_mandated"),
            BudgetLineItem("Project Management Unit", 600_000, "project_management", "local"),
            BudgetLineItem("Training & Capacity Building", 300_000, "training", "local"),
            BudgetLineItem("Contingency", 200_000, "contingency", "local"),
            BudgetLineItem("Monitoring & Evaluation", 500_000, "administration", "foreign"),
        ],
    ),

    "health_program": BudgetProject(
        name="Primary Healthcare Strengthening Program",
        country="Nepal",
        total_budget_usd=4_200_000,
        donor="USAID",
        sector="Health",
        procurement_method="Restricted to USAID-approved vendors for technical assistance",
        beneficiary_count=200_000,
        national_law_reference="Nepal Public Procurement Act 2063 (2007)",
        context_notes=(
            "USAID requires US-registered implementing partner as prime. "
            "Targets maternal and child health in Terai region."
        ),
        line_items=[
            BudgetLineItem("Technical Assistance - US Firm", 1_400_000, "consultancy", "donor_mandated"),
            BudgetLineItem("Medical Equipment", 900_000, "equipment", "foreign"),
            BudgetLineItem("Infrastructure Rehabilitation", 700_000, "civil_works", "local"),
            BudgetLineItem("Community Health Worker Training", 450_000, "training", "local"),
            BudgetLineItem("Medicines & Supplies", 500_000, "direct_delivery", "local"),
            BudgetLineItem("M&E - US Firm Subcontract", 250_000, "administration", "donor_mandated"),
        ],
    ),

    "digital_gov": BudgetProject(
        name="Digital Government Services Initiative",
        country="Mali",
        total_budget_usd=6_000_000,
        donor="World Bank",
        sector="Digital/ICT",
        procurement_method="World Bank procurement guidelines - international shortlist only",
        beneficiary_count=500_000,
        national_law_reference="Mali Loi N°08-023 sur les marchés publics",
        context_notes=(
            "Aims to digitize 50 government services within 3 years. "
            "World Bank nominated international firm for software platform. "
            "Mali procurement law requires 30% local content for ICT contracts."
        ),
        line_items=[
            BudgetLineItem("Software Platform License - Foreign Vendor", 1_800_000, "consultancy", "donor_mandated"),
            BudgetLineItem("Consultancy - WB Nominated Firm", 1_500_000, "consultancy", "donor_mandated"),
            BudgetLineItem("Hardware Procurement", 1_200_000, "equipment", "foreign"),
            BudgetLineItem("Local Staff & Salaries", 600_000, "administration", "local"),
            BudgetLineItem("Training Programs", 500_000, "training", "local"),
            BudgetLineItem("Operational Costs", 400_000, "administration", "local"),
        ],
    ),
}
