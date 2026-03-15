# main.py
from fastmcp import FastMCP
from fastmcp.server.auth.providers.github import GitHubProvider  # built-in in FastMCP 3.x
from fastmcp.server.dependencies import get_access_token          # correct 3.x import path
import os
import sqlite3
import aiosqlite

# ---------------------------------------------------------------------------
# PATHS & CONFIG
# ---------------------------------------------------------------------------

DB_PATH         = os.environ.get("DB_PATH", "/tmp/expenses.db")
CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

# ---------------------------------------------------------------------------
# AUTH — GitHubProvider handles the entire OAuth dance automatically.
# It redirects users to GitHub login, exchanges the code for a token,
# validates the token on every request, and populates token.claims with
# the authenticated user's GitHub profile data.
# ---------------------------------------------------------------------------

auth_provider = GitHubProvider(
    client_id     = os.environ["GITHUB_CLIENT_ID"],
    client_secret = os.environ["GITHUB_CLIENT_SECRET"],
    base_url      = os.environ["SERVER_BASE_URL"],   # e.g. https://accessible-tomato-meerkat.fastmcp.app
    # JWT_SECRET is used internally by GitHubProvider to sign session tokens.
    # Pass it via jwt_signing_key so sessions survive server restarts.
    jwt_signing_key = os.environ["JWT_SECRET"],
)

mcp = FastMCP("ExpenseTracker", auth=auth_provider)


# ---------------------------------------------------------------------------
# IDENTITY HELPER
# ---------------------------------------------------------------------------

def get_current_user_id() -> str:
    """
    Returns the authenticated user's GitHub numeric ID as a string.

    GitHubProvider stores the full GitHub user profile in token.claims after
    a successful login. The 'id' claim is the stable numeric GitHub user ID —
    safe to use as a database key because it never changes even if the user
    renames their account.

    token.claims keys available: id, login, name, email, avatar_url, company,
    location, bio, public_repos, followers, following.
    """
    token = get_access_token()
    if token is None:
        raise RuntimeError("No authenticated user in current request context")
    # GitHub numeric user ID — stable, unique, never changes
    return str(token.claims.get("id") or token.client_id)


# ---------------------------------------------------------------------------
# DB INITIALISATION  — sync, runs at module load
# ---------------------------------------------------------------------------

def init_db():
    """Create tables with user_id column for multi-user isolation."""
    with sqlite3.connect(DB_PATH) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute('''
            CREATE TABLE IF NOT EXISTS expenses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL,
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
                user_id TEXT    NOT NULL,
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
    Only edits expenses that belong to the authenticated user.

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
    Delete expense entries. Only deletes expenses owned by the authenticated user.

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
    Retrieve the authenticated user's expenses within a date range.

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
    Summarise the authenticated user's spending by category (or subcategory).

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
    Record a credit / income entry for the authenticated user.

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
    Retrieve the authenticated user's income entries within a date range.

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
    Compare the authenticated user's actual spending against a budget.

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