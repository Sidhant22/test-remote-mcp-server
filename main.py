from fastmcp import FastMCP
import os
import sqlite3    # sync — only used for init_db()
import aiosqlite  # async — used for all tool handlers

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------

# /tmp is always writable on cloud runtimes; override via DB_PATH env var
# if a persistent volume is available.
DB_PATH        = os.environ.get("DB_PATH", "/tmp/expenses.db")
CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

# Creating the instance
mcp = FastMCP("ExpenseTracker")


# ---------------------------------------------------------------------------
# DB INITIALISATION  — sync, runs at module load so it works whether the
# platform calls  `python main.py`  OR imports the module directly.
# ---------------------------------------------------------------------------

def init_db():
    """Create tables for expenses and income if they don't already exist."""
    with sqlite3.connect(DB_PATH) as c:
        c.execute("PRAGMA journal_mode=WAL")   # safer for concurrent access
        c.execute('''
            CREATE TABLE IF NOT EXISTS expenses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
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
                date    TEXT    NOT NULL,
                source  TEXT    DEFAULT '',
                amount  REAL    NOT NULL,
                note    TEXT    DEFAULT ''
            )
        ''')
        c.commit()

# Run immediately on import — guaranteed to execute regardless of entry point
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
        category:    Top-level category (e.g. 'food', 'transport'). See the
                     expense://categories resource for valid values.
        subcategory: Sub-category within the chosen category (e.g. 'groceries').
        note:        Optional free-text description.

    Returns:
        {"status": "success", "id": <new row id>}
    """
    if amount <= 0:
        return {"status": "error", "message": "amount must be a positive number"}

    async with aiosqlite.connect(DB_PATH) as c:
        cur = await c.execute(
            'INSERT INTO expenses (date, category, subcategory, amount, note) VALUES (?, ?, ?, ?, ?)',
            (date, category.lower(), subcategory.lower(), amount, note)
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
    Update one or more fields of an existing expense entry.

    Supply only the fields you want to change — unchanged fields keep their
    current values.

    Args:
        id:          The numeric ID of the expense to edit (returned by add_expense
                     or visible in list_expenses).
        date:        New date in YYYY-MM-DD format (optional).
        amount:      New amount (optional, must be positive).
        category:    New top-level category (optional).
        subcategory: New sub-category (optional).
        note:        New note text (optional).

    Returns:
        {"status": "success", "rows_updated": 1} or an error dict.
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

    set_clause = ', '.join(f'{col} = ?' for col in updates)
    values     = list(updates.values()) + [id]

    async with aiosqlite.connect(DB_PATH) as c:
        cur = await c.execute(f'UPDATE expenses SET {set_clause} WHERE id = ?', values)
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
    Delete expense entries by ID, or in bulk across a date range.

    Two modes:
      • Single delete — provide `id` (ignores date params).
      • Bulk delete   — provide `start_date` + `end_date`; optionally narrow
                        by `category` (e.g. delete all 'food' in January).

    Args:
        id:         ID of a specific expense to delete.
        start_date: Start of date range (YYYY-MM-DD), inclusive.
        end_date:   End of date range (YYYY-MM-DD), inclusive.
        category:   Optional category filter for bulk deletes.

    Returns:
        {"status": "success", "rows_deleted": <n>} or an error dict.
    """
    async with aiosqlite.connect(DB_PATH) as c:
        if id is not None:
            cur = await c.execute('DELETE FROM expenses WHERE id = ?', (id,))
            await c.commit()
            if cur.rowcount == 0:
                return {"status": "error", "message": f"No expense found with id={id}"}
            return {"status": "success", "rows_deleted": cur.rowcount}

        if start_date and end_date:
            if category:
                cur = await c.execute(
                    'DELETE FROM expenses WHERE date BETWEEN ? AND ? AND category = ?',
                    (start_date, end_date, category.lower())
                )
            else:
                cur = await c.execute(
                    'DELETE FROM expenses WHERE date BETWEEN ? AND ?',
                    (start_date, end_date)
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
    Retrieve expense entries within a date range.

    Args:
        start_date: Start date (YYYY-MM-DD), inclusive.
        end_date:   End date (YYYY-MM-DD), inclusive.
        category:   Optional — filter results to a single top-level category.

    Returns:
        List of expense dicts: {id, date, category, subcategory, amount, note}.
    """
    async with aiosqlite.connect(DB_PATH) as c:
        c.row_factory = aiosqlite.Row
        if category:
            cur = await c.execute(
                '''SELECT id, date, category, subcategory, amount, note
                   FROM expenses
                   WHERE date BETWEEN ? AND ? AND category = ?
                   ORDER BY date ASC, id ASC''',
                (start_date, end_date, category.lower())
            )
        else:
            cur = await c.execute(
                '''SELECT id, date, category, subcategory, amount, note
                   FROM expenses
                   WHERE date BETWEEN ? AND ?
                   ORDER BY date ASC, id ASC''',
                (start_date, end_date)
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


@mcp.tool()
async def summarize_expenses(
    start_date: str,
    end_date: str,
    category: str = None
) -> list:
    """
    Summarise total spending by category (and sub-category when a category is
    specified) within an inclusive date range.

    Args:
        start_date: Start date (YYYY-MM-DD), inclusive.
        end_date:   End date (YYYY-MM-DD), inclusive.
        category:   Optional — when provided, breaks down totals by subcategory
                    within that category instead of by top-level category.

    Returns:
        List of dicts with grouping key(s) and total_amount, sorted highest first.
    """
    async with aiosqlite.connect(DB_PATH) as c:
        c.row_factory = aiosqlite.Row
        if category:
            cur = await c.execute(
                '''SELECT subcategory, SUM(amount) AS total_amount
                   FROM expenses
                   WHERE date BETWEEN ? AND ? AND category = ?
                   GROUP BY subcategory
                   ORDER BY total_amount DESC''',
                (start_date, end_date, category.lower())
            )
        else:
            cur = await c.execute(
                '''SELECT category, SUM(amount) AS total_amount
                   FROM expenses
                   WHERE date BETWEEN ? AND ?
                   GROUP BY category
                   ORDER BY total_amount DESC''',
                (start_date, end_date)
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# INCOME / CREDIT TOOLS
# ---------------------------------------------------------------------------

@mcp.tool()
async def add_income(
    date: str,
    amount: float,
    source: str = '',
    note: str = ''
) -> dict:
    """
    Record a credit / income entry.

    Args:
        date:   Date of the income in YYYY-MM-DD format.
        amount: Income amount (positive number).
        source: Where the money came from (e.g. 'salary', 'freelance', 'dividends').
        note:   Optional free-text description.

    Returns:
        {"status": "success", "id": <new row id>}
    """
    if amount <= 0:
        return {"status": "error", "message": "amount must be a positive number"}

    async with aiosqlite.connect(DB_PATH) as c:
        cur = await c.execute(
            'INSERT INTO income (date, source, amount, note) VALUES (?, ?, ?, ?)',
            (date, source.lower(), amount, note)
        )
        await c.commit()
        return {"status": "success", "id": cur.lastrowid}


@mcp.tool()
async def list_income(start_date: str, end_date: str) -> list:
    """
    Retrieve income entries within a date range.

    Args:
        start_date: Start date (YYYY-MM-DD), inclusive.
        end_date:   End date (YYYY-MM-DD), inclusive.

    Returns:
        List of income dicts: {id, date, source, amount, note}.
    """
    async with aiosqlite.connect(DB_PATH) as c:
        c.row_factory = aiosqlite.Row
        cur = await c.execute(
            '''SELECT id, date, source, amount, note
               FROM income
               WHERE date BETWEEN ? AND ?
               ORDER BY date ASC, id ASC''',
            (start_date, end_date)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


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
    Compare actual spending against a budget for each category in a date range.

    Args:
        start_date: Start date (YYYY-MM-DD), inclusive.
        end_date:   End date (YYYY-MM-DD), inclusive.
        budgets:    A dict mapping category names to their budget limits, e.g.
                    {"food": 5000, "transport": 2000, "entertainment": 1500}.
                    Categories not listed are treated as having no set budget.

    Returns:
        A dict with:
          • "period"       : {"start": ..., "end": ...}
          • "total_income" : total credits recorded in the period
          • "total_spent"  : total expenses in the period
          • "net"          : total_income - total_spent
          • "by_category"  : list of per-category breakdowns:
                             {category, spent, budget?, variance?, status}
            status is one of: "over_budget" | "under_budget" | "no_budget"
    """
    async with aiosqlite.connect(DB_PATH) as c:
        cur = await c.execute(
            '''SELECT category, SUM(amount) AS spent
               FROM expenses
               WHERE date BETWEEN ? AND ?
               GROUP BY category''',
            (start_date, end_date)
        )
        spend_map = {row[0]: row[1] for row in await cur.fetchall()}

        cur2 = await c.execute(
            'SELECT COALESCE(SUM(amount), 0) FROM income WHERE date BETWEEN ? AND ?',
            (start_date, end_date)
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
    """Return the full categories taxonomy. Read fresh each call so file edits
    take effect without restarting the server."""
    with open(CATEGORIES_PATH, 'r', encoding='utf-8') as f:
        return f.read()


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # transport="http" puts FastMCP into streamable-HTTP (remote) mode.
    # host="0.0.0.0" makes the server reachable outside the container/VM.
    mcp.run(transport="http", host="0.0.0.0", port=8080)