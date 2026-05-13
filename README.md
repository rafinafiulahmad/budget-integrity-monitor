# Budget Integrity Monitor (BIM) v1.0

> **AI-powered budget transparency and accountability tool for civil society organizations, journalists, and governance monitors in Least Developed Countries.**

**Author:** Nafiul Ahmad Rafi — Civic Tech Architect & AI Policy Researcher  
**Organizations:** [Insights Bangladesh](https://insightsbangladesh.org) · [NagorikBD](https://nagorikbd.org) · AtlasAii  
**License:** See [LICENSE](./LICENSE)

---

## What is BIM?

Budget Integrity Monitor analyzes development project budgets using AI agents to detect corruption risks, flag donor conditionality that violates national law, and measure how much money actually reaches citizens. Reports are generated in English and Bengali, exportable as PDF and Excel.

---

## Features

### 🤖 AI Analysis Agents
| Agent | What it does |
|-------|-------------|
| **Leakage Detection** | Flags ghost contractors, inflated line items, consultancy bloat |
| **Impact-to-Citizen Ratio** | Measures % of budget reaching citizens vs overhead |
| **Sovereign Alignment** | Checks donor conditions against national procurement law (PPR 2008 BD, UNCITRAL, Paris Declaration) |
| **Bengali Translation** | Auto-translates full reports into Bengali |
| **Civil Society Summary** | Plain-language summary for journalists and citizens |

### 🔐 Authentication & Organizations
| Feature | Description |
|---------|-------------|
| **Multi-org auth** | Full org isolation — each org sees only its own data |
| **Role-based access** | `viewer` → `analyst` → `admin` → `superadmin` |
| **Password reset** | OTP email flow |
| **Invite onboarding** | Admins invite members by email with secure link |
| **Google SSO** | OAuth2 sign-in, optional domain restriction |
| **Audit log** | Append-only log of every user action |
| **Org settings** | Per-org currency, sector, country, timezone, logo |
| **Super-admin panel** | Manage all orgs and users from one interface |

### 📊 Data & Reporting
| Feature | Description |
|---------|-------------|
| **CSV bulk import** | Upload many projects at once |
| **PDF export** | Full report as downloadable PDF |
| **Excel export** | Reports and donor comparisons as XLSX |
| **CSV template** | Blank template for structured data entry |
| **Project history** | Snapshots of every analysis run |
| **History diff** | Compare two snapshots to track changes |
| **Risk trends dashboard** | Country-level risk over time |
| **Donor comparison** | Side-by-side donor analysis in Excel |
| **Map view** | Geographic project markers by country |
| **Alerting** | Configurable alerts when risk thresholds are crossed |

---

## Role Permissions

| Role | Level | Access |
|------|-------|--------|
| `viewer` | 0 | Map, History, Trends, Benchmarks, Anomalies |
| `analyst` | 1 | + Analyze, Bulk CSV, Donors, Alerts |
| `admin` | 2 | + Org Settings, Audit Log, Invite Members |
| `superadmin` | 3 | + Super-Admin Panel (all orgs) |

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/yourorg/budget-integrity-monitor.git
cd budget-integrity-monitor
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY
```

### 3. Run

```bash
python app.py
# Open http://localhost:5000
```

Demo accounts are created automatically on first run:

| Username | Password | Org | Role |
|----------|----------|-----|------|
| `admin` | `admin123` | PIBD | admin |
| `analyst` | `analyst123` | PIBD | analyst |
| `viewer` | `viewer123` | UNDP | viewer |

> ⚠️ Change demo passwords before any production deployment.

---

## Project Structure

```
budget-integrity-monitor/
│
├── app.py                  # Flask app — all API endpoints & web UI
├── auth.py                 # Core auth layer (sessions, hashing)
├── auth_org_backend.py     # Extended auth: SSO, invites, audit, super-admin
│
├── agents/
│   ├── leakage_agent.py    # Budget leakage detection
│   ├── impact_agent.py     # Citizen impact ratio analysis
│   ├── sovereign_agent.py  # Sovereign alignment / donor conditionality
│   └── bengali_agent.py    # Bengali translation
│
├── core/
│   ├── models.py           # Data models (BudgetProject, IntegrityReport, etc.)
│   ├── orchestrator.py     # Runs all agents, synthesizes report
│   └── claude_client.py    # Anthropic API client wrapper
│
├── tools/
│   ├── csv_import.py       # CSV parsing and template generation
│   ├── pdf_export.py       # PDF report generation
│   ├── excel_export.py     # Excel/XLSX export
│   ├── alerting.py         # Alert configuration and dispatch
│   ├── org_history.py      # Per-org project history storage
│   └── history.py          # Snapshot comparison utilities
│
├── config/
│   └── settings.py         # App configuration from env vars
│
├── data/
│   ├── country_coords.py   # Country coordinates for map view
│   ├── sample_projects.py  # Sample data for testing
│   └── example_project.json
│
├── uploads/                # Temp upload directory (git-ignored)
├── reports/                # Generated reports (git-ignored)
│
├── .env.example            # Environment variable template
├── .gitignore
├── requirements.txt
└── LICENSE
```

---

## API Reference

### Auth

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/auth/register` | — | Register user + org |
| POST | `/api/auth/login` | — | Get session token |
| POST | `/api/auth/logout` | token | Invalidate token |
| GET | `/api/auth/me` | token | Current session info |
| GET | `/api/auth/users` | admin | List users in org |
| POST | `/api/auth/reset-request` | — | Request password reset OTP |
| POST | `/api/auth/reset-verify` | — | Verify OTP token |
| POST | `/api/auth/reset-confirm` | — | Set new password |
| POST | `/api/auth/invite` | admin | Invite member by email |
| GET | `/api/auth/invite/<token>` | — | View invite details |
| POST | `/api/auth/invite/<token>/accept` | — | Accept invite |
| GET | `/api/auth/google` | — | Google OAuth2 redirect |
| GET | `/api/auth/google/callback` | — | Google OAuth2 callback |

### Projects & Analysis

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/analyze` | analyst | Analyze a single project (JSON) |
| POST | `/api/bulk` | analyst | Bulk import via CSV |
| GET | `/api/projects` | analyst | List projects with history |
| GET | `/api/history/<slug>` | analyst | Project history snapshots |
| GET | `/api/compare/<slug>` | analyst | Diff two latest snapshots |

### Exports

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/report/<slug>/pdf` | analyst | Download PDF report |
| GET | `/api/report/<slug>/xlsx` | analyst | Download Excel report |
| GET | `/api/template/csv` | — | Download blank CSV template |
| GET | `/api/donor-comparison/xlsx` | analyst | Donor comparison Excel |

### Dashboard

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/map` | analyst | Map markers (all projects) |
| GET | `/api/risk-trends` | analyst | Country risk over time |

### Org & Admin

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/org/settings` | any | Get org settings |
| POST | `/api/org/settings` | admin | Update org settings |
| GET | `/api/audit` | admin | Audit log (paginated) |
| GET | `/api/superadmin/orgs` | superadmin | List all orgs |
| POST | `/api/superadmin/orgs` | superadmin | Create org |
| GET | `/api/superadmin/orgs/<org>/users` | superadmin | List org users |
| POST | `/api/superadmin/orgs/<org>/users/<user>/role` | superadmin | Change user role |

---

## Google SSO Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials
2. Create an **OAuth 2.0 Client ID** (Web application)
3. Add authorised redirect URI: `https://your-app.example.org/api/auth/google/callback`
4. Copy Client ID + Secret into `.env`
5. Set `GOOGLE_ALLOWED_DOMAINS` to restrict to your org's Google Workspace domain

---

## Data Storage

All data is stored as JSON files — no database required.

```
reports/auth/
├── users.json          ← user accounts
├── sessions.json       ← active sessions
├── reset_tokens.json   ← OTP tokens (auto-purged)
├── invites.json        ← pending + accepted invites
├── audit.jsonl         ← append-only audit log
└── org_settings/
    ├── PIBD.json
    └── UNDP.json
```

---

## Audit Log Format

Each line in `audit.jsonl`:

```json
{
  "timestamp": "2026-05-14T08:23:11.045123",
  "user": "admin",
  "org": "PIBD",
  "action": "analyze",
  "detail": "Rural Road Network Phase II",
  "ip": "192.168.1.10"
}
```

Standard actions: `login` · `logout` · `register` · `analyze` · `bulk_import` · `pdf_export` · `xlsx_export` · `invite_sent` · `invite_accepted` · `org_settings_update` · `role_change` · `password_reset_requested` · `password_reset_confirmed` · `superadmin_create_org`

---

## Production Deployment Notes

- Set `BIM_SECRET_KEY` to a long random string
- Use a proper WSGI server: `gunicorn app:app`
- Put behind nginx or a reverse proxy
- Set up HTTPS before enabling Google SSO
- Change all demo user passwords immediately
- Consider moving `reports/` to object storage for scale

---

## Contributing

This project is developed under NagorikBD's civic tech initiative. For contribution inquiries contact insightsbangladesh@gmail.com.

---

## License

© 2025–2026 Nafiul Ahmad Rafi. See [LICENSE](./LICENSE) for full terms.

Developed as part of civic technology and digital governance infrastructure for Bangladesh, under the Insights Bangladesh, NagorikBD, and AtlasAii initiatives.
