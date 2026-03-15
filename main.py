# main.py
#
# ARCHITECTURE NOTE:
# On Horizon, your server runs privately on port 8081 behind a gateway on 8080.
# The gateway handles ALL authentication — your server must NOT configure any
# auth provider. Adding GitHubProvider here causes a 401 because the gateway
# calls your server internally without forwarding the user's token.
#
# For multi-user data isolation, Horizon injects user identity via HTTP headers.
# We log all incoming headers on the first tool call so you can see exactly
# which header to use as your user_id key.

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
import os
import sqlite3
import aiosqlite
import logging

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("expense_tracker")

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------

DB_PATH         = os.environ.get("DB_PATH", "/tmp/expenses.db")
CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

# ---------------------------------------------------------------------------
# MCP INSTANCE — no auth= parameter, Horizon gateway handles it
# ---------------------------------------------------------------------------

mcp = FastMCP("ExpenseTracker")


# ---------------------------------------------------------------------------
# IDENTITY HELPER
# ---------------------------------------------------------------------------

def get_current_user_id() -> str:
    """
    Extract a stable user identifier from headers injected by Horizon's gateway.

    On first call, logs ALL headers so you can see what Horizon provides.
    Common candidates: x-user-id, x-forwarded-user, x-auth-user, authorization.

    Falls back to "anonymous" if no identity header is found, so the server
    keeps working while we identify the correct header name.
    """
    headers = get_http_headers()

    # Log all headers once so we can see what Horizon injects
    log.info(f"Incoming headers: {dict(headers)}")

    # Try common identity header names in order of likelihood
    user_id = (
        headers.get("x-user-id")
        or headers.get("x-forwarded-user")
        or headers.get("x-auth-user")
        or headers.get("x-authenticated-user")
        or headers.get("x-prefect-user-id")
    )

    if user_id:
        log.info(f"Identified user: {user_id}")
        return user_id

    # If no identity header found yet, log all headers at WARNING level
    # so it's easy to spot in Horizon's Logs tab
    log.warning(f"No user identity header found. All headers: {dict(headers)}")
    return "anonymous"


# ---------------------------------------------------------------------------
# DB INITIALISATION — sync, runs at module load
# ---------------------------------------------------------------------------

def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute('''
            CREATE TABLE IF NOT EXISTS expenses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL DEFAULT 'anonymous',
                date        TEXT    NOT NULL,
                category    TEXT    DEFAULT '',
                subcategory TEXT    DEFAULT '',
                amount      REAL    NOT NULL,
                note        TEXT    DEFAULT ''
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS income (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT    NOT NULL DEFAULT 'anonymous',
                date    TEXT    NOT NULL,
                source  TEXT    DEFAULT '',
                amount  REAL    NOT NULL,
                note    TEXT    DEFAULT ''
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_expenses_user ON expenses(user_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_income_user   ON income(user_id)')
        c.commit()

init_db()


# ---------------------------------------------------------------------------
# EXPENSE TOOLS
# ---------------------------------------------------------------------------

@mcp.tool()
async def add_expense(
    date: str,
    amount: float,
    category: str = '',
    subcategory: str = '',
    note: str = ''
) -> dict:
    """
    Add a new expense entry to the database.

    Args:
        date:        Date of the expense in YYYY-MM-DD format.
        amount:      Expense amount (positive number).
        category:    Top-level category (e.g. 'food', 'transport').
        subcategory: Sub-category within the chosen category.
        note:        Optional free-text description.
    """
    if amount <= 0:
        return {"status": "error", "message": "amount must be a positive number"}

    user_id = get_current_user_id()
    log.info(f"add_expense: user={user_id} date={date} amount={amount} category={category}")

    async with aiosqlite.connect(DB_PATH) as c:
        cur = await c.execute(
            '''INSERT INTO expenses (user_id, date, category, subcategory, amount, note)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (user_id, date, category.lower(), subcategory.lower(), amount, note)
        )
        await c.commit()
        return {"status": "success", "id": cur.lastrowid}


@mcp.tool()
async def edit_expense(
    id: int,
    date: str = None,
    amount: float = None,
    category: str = None,
    subcategory: str = None,
    note: str = None
) -> dict:
    """
    Update one or more fields of an existing expense.
    Only edits expenses that belong to the current user.

    Args:
        id:          ID of the expense to edit.
        date:        New date (optional).
        amount:      New amount (optional, must be positive).
        category:    New top-level category (optional).
        subcategory: New sub-category (optional).
        note:        New note text (optional).
    """
    updates = {}
    if date        is not None: updates['date']        = date
    if amount      is not None:
        if amount <= 0:
            return {"status": "error", "message": "amount must be a positive number"}
        updates['amount'] = amount
    if category    is not None: updates['category']    = category.lower()
    if subcategory is not None: updates['subcategory'] = subcategory.lower()
    if note        is not None: updates['note']        = note

    if not updates:
        return {"status": "error", "message": "No fields provided to update"}

    user_id    = get_current_user_id()
    set_clause = ', '.join(f'{col} = ?' for col in updates)
    values     = list(updates.values()) + [id, user_id]

    async with aiosqlite.connect(DB_PATH) as c:
        cur = await c.execute(
            f'UPDATE expenses SET {set_clause} WHERE id = ? AND user_id = ?', values
        )
        await c.commit()
        if cur.rowcount == 0:
            return {"status": "error", "message": f"No expense found with id={id}"}
        return {"status": "success", "rows_updated": cur.rowcount}


@mcp.tool()
async def delete_expense(
    id: int = None,
    start_date: str = None,
    end_date: str = None,
    category: str = None
) -> dict:
    """
    Delete expense entries. Only deletes expenses owned by the current user.

    Two modes:
      • Single delete — provide id.
      • Bulk delete   — provide start_date + end_date, optional category filter.
    """
    user_id = get_current_user_id()

    async with aiosqlite.connect(DB_PATH) as c:
        if id is not None:
            cur = await c.execute(
                'DELETE FROM expenses WHERE id = ? AND user_id = ?', (id, user_id)
            )
            await c.commit()
            if cur.rowcount == 0:
                return {"status": "error", "message": f"No expense found with id={id}"}
            return {"status": "success", "rows_deleted": cur.rowcount}

        if start_date and end_date:
            if category:
                cur = await c.execute(
                    'DELETE FROM expenses WHERE date BETWEEN ? AND ? AND category = ? AND user_id = ?',
                    (start_date, end_date, category.lower(), user_id)
                )
            else:
                cur = await c.execute(
                    'DELETE FROM expenses WHERE date BETWEEN ? AND ? AND user_id = ?',
                    (start_date, end_date, user_id)
                )
            await c.commit()
            return {"status": "success", "rows_deleted": cur.rowcount}

    return {"status": "error", "message": "Provide either 'id' or both 'start_date' and 'end_date'"}


@mcp.tool()
async def list_expenses(
    start_date: str,
    end_date: str,
    category: str = None
) -> list:
    """
    Retrieve the current user's expenses within a date range.

    Args:
        start_date: Start date (YYYY-MM-DD), inclusive.
        end_date:   End date (YYYY-MM-DD), inclusive.
        category:   Optional category filter.
    """
    user_id = get_current_user_id()
    async with aiosqlite.connect(DB_PATH) as c:
        c.row_factory = aiosqlite.Row
        if category:
            cur = await c.execute(
                '''SELECT id, date, category, subcategory, amount, note
                   FROM expenses
                   WHERE date BETWEEN ? AND ? AND category = ? AND user_id = ?
                   ORDER BY date ASC, id ASC''',
                (start_date, end_date, category.lower(), user_id)
            )
        else:
            cur = await c.execute(
                '''SELECT id, date, category, subcategory, amount, note
                   FROM expenses
                   WHERE date BETWEEN ? AND ? AND user_id = ?
                   ORDER BY date ASC, id ASC''',
                (start_date, end_date, user_id)
            )
        return [dict(r) for r in await cur.fetchall()]


@mcp.tool()
async def summarize_expenses(
    start_date: str,
    end_date: str,
    category: str = None
) -> list:
    """
    Summarise the current user's spending by category (or subcategory).

    Args:
        start_date: Start date (YYYY-MM-DD), inclusive.
        end_date:   End date (YYYY-MM-DD), inclusive.
        category:   Optional — drill into subcategories of this category.
    """
    user_id = get_current_user_id()
    async with aiosqlite.connect(DB_PATH) as c:
        c.row_factory = aiosqlite.Row
        if category:
            cur = await c.execute(
                '''SELECT subcategory, SUM(amount) AS total_amount
                   FROM expenses
                   WHERE date BETWEEN ? AND ? AND category = ? AND user_id = ?
                   GROUP BY subcategory ORDER BY total_amount DESC''',
                (start_date, end_date, category.lower(), user_id)
            )
        else:
            cur = await c.execute(
                '''SELECT category, SUM(amount) AS total_amount
                   FROM expenses
                   WHERE date BETWEEN ? AND ? AND user_id = ?
                   GROUP BY category ORDER BY total_amount DESC''',
                (start_date, end_date, user_id)
            )
        return [dict(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# INCOME TOOLS
# ---------------------------------------------------------------------------

@mcp.tool()
async def add_income(
    date: str,
    amount: float,
    source: str = '',
    note: str = ''
) -> dict:
    """
    Record a credit / income entry for the current user.

    Args:
        date:   Date in YYYY-MM-DD format.
        amount: Income amount (positive number).
        source: Where the money came from (e.g. 'salary', 'freelance').
        note:   Optional free-text description.
    """
    if amount <= 0:
        return {"status": "error", "message": "amount must be a positive number"}

    user_id = get_current_user_id()
    async with aiosqlite.connect(DB_PATH) as c:
        cur = await c.execute(
            'INSERT INTO income (user_id, date, source, amount, note) VALUES (?, ?, ?, ?, ?)',
            (user_id, date, source.lower(), amount, note)
        )
        await c.commit()
        return {"status": "success", "id": cur.lastrowid}


@mcp.tool()
async def list_income(start_date: str, end_date: str) -> list:
    """
    Retrieve the current user's income entries within a date range.

    Args:
        start_date: Start date (YYYY-MM-DD), inclusive.
        end_date:   End date (YYYY-MM-DD), inclusive.
    """
    user_id = get_current_user_id()
    async with aiosqlite.connect(DB_PATH) as c:
        c.row_factory = aiosqlite.Row
        cur = await c.execute(
            '''SELECT id, date, source, amount, note
               FROM income
               WHERE date BETWEEN ? AND ? AND user_id = ?
               ORDER BY date ASC, id ASC''',
            (start_date, end_date, user_id)
        )
        return [dict(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# BUDGET TOOL
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_budget_summary(
    start_date: str,
    end_date: str,
    budgets: dict
) -> dict:
    """
    Compare the current user's actual spending against a budget.

    Args:
        start_date: Start date (YYYY-MM-DD), inclusive.
        end_date:   End date (YYYY-MM-DD), inclusive.
        budgets:    Dict of category → limit, e.g. {"food": 5000, "transport": 2000}.
    """
    user_id = get_current_user_id()
    async with aiosqlite.connect(DB_PATH) as c:
        cur = await c.execute(
            '''SELECT category, SUM(amount) AS spent
               FROM expenses WHERE date BETWEEN ? AND ? AND user_id = ?
               GROUP BY category''',
            (start_date, end_date, user_id)
        )
        spend_map = {row[0]: row[1] for row in await cur.fetchall()}

        cur2 = await c.execute(
            'SELECT COALESCE(SUM(amount), 0) FROM income WHERE date BETWEEN ? AND ? AND user_id = ?',
            (start_date, end_date, user_id)
        )
        total_income = (await cur2.fetchone())[0]

    total_spent    = sum(spend_map.values())
    all_categories = set(spend_map.keys()) | set(budgets.keys())
    by_category    = []

    for cat in sorted(all_categories):
        spent  = spend_map.get(cat, 0.0)
        budget = budgets.get(cat)
        if budget is not None:
            variance = budget - spent
            by_category.append({
                "category": cat,
                "spent":    round(spent, 2),
                "budget":   round(budget, 2),
                "variance": round(variance, 2),
                "status":   "over_budget" if variance < 0 else "under_budget"
            })
        else:
            by_category.append({
                "category": cat,
                "spent":    round(spent, 2),
                "status":   "no_budget"
            })

    return {
        "period":       {"start": start_date, "end": end_date},
        "total_income": round(total_income, 2),
        "total_spent":  round(total_spent, 2),
        "net":          round(total_income - total_spent, 2),
        "by_category":  by_category
    }


# ---------------------------------------------------------------------------
# RESOURCE
# ---------------------------------------------------------------------------

@mcp.resource("expense://categories", mime_type="application/json")
def categories() -> str:
    """Return the full categories taxonomy."""
    with open(CATEGORIES_PATH, 'r', encoding='utf-8') as f:
        return f.read()


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8080)