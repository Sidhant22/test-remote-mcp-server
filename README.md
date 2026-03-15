# Expense Tracker MCP Server

A personal finance tracker built as a remote MCP server. Instead of opening an app, you tell Claude: *"Add ₹500 for lunch today"* or *"How much did I spend on food last month?"* — and it works.

Built with [FastMCP](https://gofastmcp.com) (Python), SQLite, and deployed on [Horizon](https://horizon.prefect.io).

**Live server URL:** `https://accessible-tomato-meerkat.fastmcp.app/mcp`

---

## What it does

The server exposes 8 tools and 1 resource to any MCP-compatible client:

| Tool | Description |
|---|---|
| `add_expense` | Log a new expense (date, amount, category, subcategory, note) |
| `edit_expense` | Update any field of an existing entry by ID |
| `delete_expense` | Delete by ID, or bulk-delete across a date range |
| `list_expenses` | List entries filtered by date range and optional category |
| `summarize_expenses` | Totals by category (or subcategory drill-down) |
| `add_income` | Record a credit/income entry |
| `list_income` | List income entries by date range |
| `get_budget_summary` | Compare actual spend against a budget dict, includes net income |

**Resource:** `expense://categories` — a JSON taxonomy of 21 categories and their subcategories. Claude reads this before every write call to ensure consistent categorisation.

---

## Architecture

```
You (natural language)
    │
    ▼
Claude Desktop (MCP client)
    │  HTTPS
    ▼
Horizon Gateway (port 8080) ── auth + routing
    │  internal
    ▼
FastMCP Server (port 8081) ── your tools
    │
    ▼
SQLite DB (/tmp/expenses.db)
```

Horizon handles authentication at the gateway layer. The FastMCP server receives only pre-authenticated requests and does not implement its own auth provider.

---

## Tech stack

- **FastMCP 3.x** — MCP server framework
- **aiosqlite** — async SQLite for non-blocking DB operations
- **SQLite** — embedded database (ephemeral on Horizon free tier)
- **Horizon (Prefect)** — managed MCP hosting with built-in auth

---

## Local setup

### Prerequisites

- Python 3.11 or higher
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Clone and install

```bash
git clone https://github.com/Sidhant22/test-remote-mcp-server
cd test-remote-mcp-server
```

**Using uv (recommended):**
```bash
uv venv
uv sync
```

**Using pip:**
```bash
python -m venv .venv
pip install fastmcp aiosqlite
```

### Activate the virtual environment

**Windows (Command Prompt):**
```cmd
.venv\Scripts\activate
```

**Windows (PowerShell):**
```powershell
.venv\Scripts\Activate.ps1
```

**macOS / Linux:**
```bash
source .venv/bin/activate
```

### Run locally

```bash
python main.py
```

The server starts on `http://localhost:8080/mcp` using stdio transport by default. To run in HTTP mode (same as the deployed version):

```bash
fastmcp run main.py --transport http --host 127.0.0.1 --port 8080
```

---

## Connect to Claude Desktop

### Option A — Use the live remote server

1. Open Claude Desktop → **Settings** → **Connectors**
2. Click **Add custom connector**
3. Paste: `https://accessible-tomato-meerkat.fastmcp.app/mcp`
4. Name it (e.g. *SID Expense Tracker*)
5. Close and reopen Claude Desktop
6. Start a new chat and try: *"Add an expense of ₹500 for lunch today"*

### Option B — Connect to your local server

Add the following to your Claude Desktop config file:

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "expense-tracker": {
      "command": "python",
      "args": ["C:\\path\\to\\test-remote-mcp-server\\main.py"],
      "env": {}
    }
  }
}
```

> Replace `C:\\path\\to\\` with the actual path to your cloned repo. On Windows, use double backslashes.

After editing the config, fully restart Claude Desktop (use Task Manager on Windows to end all Claude processes, then reopen).

---

## Project structure

```
test-remote-mcp-server/
├── main.py              # FastMCP server — all tools and resource
├── categories.json      # Category taxonomy resource
├── pyproject.toml       # Dependencies
└── README.md
```

---

## Environment variables

These are only needed when deploying to Horizon or another cloud platform. For local use, no environment variables are required.

| Variable | Description |
|---|---|
| `DB_PATH` | Override the SQLite file path (default: `/tmp/expenses.db`) |

---

## Deploying to Horizon

1. Push your repo to GitHub
2. Sign in at [horizon.prefect.io](https://horizon.prefect.io)
3. Connect your GitHub account and select this repo
4. Set entrypoint to `main.py`
5. Horizon auto-detects `pyproject.toml` and installs dependencies
6. Your server is live at `https://<your-server-name>.fastmcp.app/mcp`

> **Note:** The free tier uses `/tmp` for SQLite storage. Data resets on every redeploy. For persistence, set `DB_PATH` to a mounted volume path or migrate to a managed database like [Turso](https://turso.tech).

---

## Sample prompts

Once connected in Claude Desktop:

```
Add an expense of ₹450 on 2025-06-03 under food > groceries, note: "Weekly vegetables"
List all my expenses between 2025-06-01 and 2025-06-30
Summarize my food expenses for June 2025
Edit expense with id 3 — change the amount to ₹350
Delete all transport expenses between 2025-06-01 and 2025-06-30
Add income of ₹55000 on 2025-06-01, source: salary, note: "June salary"
Give me a budget summary for June 2025 with these limits — food: ₹2000, transport: ₹1500, shopping: ₹2000
```

---

## Known limitations

- **Ephemeral storage** — SQLite in `/tmp` is wiped on every Horizon redeploy
- **Single-region** — no geographic redundancy on the free tier
- **No recurring expenses** — manual entry only for now

---

## Roadmap

- [ ] Persistent database (Turso / PostgreSQL)
- [ ] Recurring expense tool
- [ ] CSV export via file attachment tool
- [ ] Persisted budget limits (stored in DB, not passed per call)
- [ ] Multi-currency support

---

## Related article

Full write-up covering the architecture, key engineering challenges, and lessons learned:


---

## License

MIT
