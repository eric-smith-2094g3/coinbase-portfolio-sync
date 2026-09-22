import argparse
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import httpx
except ImportError as _exc:
    sys.exit(f"missing dependency '{_exc.name}'. run: pip install -r requirements.txt")

API_BASE = "https://api.coinbase.com"

FIXTURE_ACCOUNTS = [
    {
        "currency": "BTC",
        "available_balance": {"value": "0.24500000", "currency": "BTC"},
        "hold": {"value": "0.00000000", "currency": "BTC"},
    },
    {
        "currency": "ETH",
        "available_balance": {"value": "3.50000000", "currency": "ETH"},
        "hold": {"value": "0.25000000", "currency": "ETH"},
    },
    {
        "currency": "SOL",
        "available_balance": {"value": "45.00000000", "currency": "SOL"},
        "hold": {"value": "0.00000000", "currency": "SOL"},
    },
    {
        "currency": "USD",
        "available_balance": {"value": "1247.50", "currency": "USD"},
        "hold": {"value": "0.00", "currency": "USD"},
    },
    {
        "currency": "USDC",
        "available_balance": {"value": "5000.00", "currency": "USDC"},
        "hold": {"value": "0.00", "currency": "USDC"},
    },
]

FIXTURE_PRICES = {
    "BTC": "67423.15",
    "ETH": "3456.78",
    "SOL": "142.33",
    "USDC": "1.00",
}


@dataclass
class Balance:
    currency: str
    amount: float
    usd_value: float = 0.0


class CoinbaseClient:
    def __init__(self, key_name: str, key_secret: str):
        self.key_name = key_name
        self.key_secret = key_secret

    def _sign(self, method: str, path: str, body: str = "") -> dict:
        timestamp = str(int(time.time()))
        message = timestamp + method.upper() + path + body
        signature = hmac.new(
            self.key_secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        return {
            "CB-ACCESS-KEY": self.key_name,
            "CB-ACCESS-SIGN": signature,
            "CB-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, body: str = "") -> dict:
        url = f"{API_BASE}{path}"
        headers = self._sign(method, path, body)
        resp = httpx.request(method, url, headers=headers, content=body or None, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_accounts(self) -> list:
        resp = self.request("GET", "/api/v3/brokerage/accounts")
        return resp.get("accounts", [])

    def get_spot_price(self, currency: str) -> float:
        path = f"/api/v3/brokerage/products/{currency}-USD"
        resp = self.request("GET", path)
        price = resp.get("price", "0")
        return float(price)


def init_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapped_at TEXT NOT NULL,
            currency TEXT NOT NULL,
            amount REAL NOT NULL,
            usd_value REAL NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def save_snapshot(db_path: Path, balances: list, timestamp: str) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    for b in balances:
        cur.execute(
            "INSERT INTO snapshots (snapped_at, currency, amount, usd_value) VALUES (?, ?, ?, ?)",
            (timestamp, b.currency, b.amount, b.usd_value),
        )
    conn.commit()
    conn.close()


def query_latest(db_path: Path) -> list:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT snapped_at, currency, amount, usd_value FROM snapshots
        WHERE snapped_at = (SELECT MAX(snapped_at) FROM snapshots)
        ORDER BY usd_value DESC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def query_history(db_path: Path, currency: str, since: str = None) -> list:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    if since:
        cur.execute(
            """
            SELECT snapped_at, amount, usd_value FROM snapshots
            WHERE currency = ? AND snapped_at >= ? ORDER BY snapped_at DESC
            """,
            (currency, since),
        )
    else:
        cur.execute(
            """
            SELECT snapped_at, amount, usd_value FROM snapshots
            WHERE currency = ? ORDER BY snapped_at DESC
            """,
            (currency,),
        )
    rows = cur.fetchall()
    conn.close()
    return rows


def query_avg_cost_basis(db_path: Path, currency: str) -> float:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT AVG(usd_value / NULLIF(amount, 0)) FROM snapshots
        WHERE currency = ? AND amount > 0
        """,
        (currency,),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] else 0.0


def parse_balances_from_accounts(accounts: list, price_lookup: dict) -> list:
    balances = []
    for acc in accounts:
        currency = acc.get("currency", "")
        available = float(acc.get("available_balance", {}).get("value", "0"))
        hold = float(acc.get("hold", {}).get("value", "0"))
        total = available + hold
        if total <= 0:
            continue
        usd_value = 0.0
        if currency == "USD":
            usd_value = total
        elif currency in price_lookup:
            usd_value = total * float(price_lookup[currency])
        else:
            usd_value = 0.0
        balances.append(Balance(currency=currency, amount=total, usd_value=usd_value))
    return balances


def fetch_balances(client: CoinbaseClient) -> list:
    accounts = client.get_accounts()
    balances = []
    for acc in accounts:
        currency = acc.get("currency", "")
        available = float(acc.get("available_balance", {}).get("value", "0"))
        hold = float(acc.get("hold", {}).get("value", "0"))
        total = available + hold
        if total <= 0:
            continue
        usd_value = 0.0
        if currency == "USD":
            usd_value = total
        else:
            try:
                price = client.get_spot_price(currency)
                usd_value = total * price
            except Exception:
                pass
        balances.append(Balance(currency=currency, amount=total, usd_value=usd_value))
    return balances


def format_currency(val: float) -> str:
    if val >= 1_000_000:
        return f"${val:,.0f}"
    if val >= 1000:
        return f"${val:,.2f}"
    return f"${val:,.4f}"


def run_sync(db_path: Path, api_key: str, api_secret: str) -> None:
    client = CoinbaseClient(api_key, api_secret)
    balances = fetch_balances(client)
    timestamp = datetime.now(timezone.utc).isoformat()
    save_snapshot(db_path, balances, timestamp)
    total = sum(b.usd_value for b in balances)
    print(f"saved snapshot at {timestamp}")
    print(f"total usd value: {format_currency(total)}")
    for b in sorted(balances, key=lambda x: x.usd_value, reverse=True):
        print(f"  {b.currency}: {b.amount:,.6f} ({format_currency(b.usd_value)})")


def run_cached_scan() -> None:
    balances = parse_balances_from_accounts(FIXTURE_ACCOUNTS, FIXTURE_PRICES)
    total = sum(b.usd_value for b in balances)
    print(f"portfolio snapshot ({datetime.now(timezone.utc).isoformat()})")
    print(f"total usd value: {format_currency(total)}")
    for b in sorted(balances, key=lambda x: x.usd_value, reverse=True):
        print(f"  {b.currency}: {b.amount:,.6f} ({format_currency(b.usd_value)})")


def run_query(db_path: Path, currency: str = None, since: str = None) -> None:
    if currency:
        rows = query_history(db_path, currency, since)
        if not rows:
            print(f"no history for {currency}")
            return
        avg_cost = query_avg_cost_basis(db_path, currency)
        print(f"history for {currency}:")
        if avg_cost:
            print(f"  avg cost basis: {format_currency(avg_cost)}")
        for snapped_at, amount, usd_value in rows:
            print(f"  {snapped_at}: {amount:,.6f} ({format_currency(usd_value)})")
    else:
        rows = query_latest(db_path)
        if not rows:
            print("no snapshots found")
            return
        print(f"latest snapshot ({rows[0][0]}):")
        for snapped_at, currency, amount, usd_value in rows:
            print(f"  {currency}: {amount:,.6f} ({format_currency(usd_value)})")


def main():
    parser = argparse.ArgumentParser(
        description="sync coinbase portfolio to local sqlite",
        usage="python cb_sync.py [--sync|--query] [--currency CUR] [--since YYYY-MM-DD] [--db PATH]",
    )
    parser.add_argument("--sync", action="store_true", help="fetch and save current balances")
    parser.add_argument("--query", action="store_true", help="show latest or currency history")
    parser.add_argument("--currency", default="", help="filter query to one currency")
    parser.add_argument("--since", default="", help="only show records since date (YYYY-MM-DD)")
    parser.add_argument("--db", default="cb_sync.db", help="sqlite db path")
    args = parser.parse_args()

    db_path = Path(args.db)
    init_db(db_path)

    if args.sync:
        api_key = os.environ.get("COINBASE_API_KEY")
        api_secret = os.environ.get("COINBASE_API_SECRET")
        if not api_key or not api_secret:
            print("error: set COINBASE_API_KEY and COINBASE_API_SECRET", file=sys.stderr)
            sys.exit(1)
        run_sync(db_path, api_key, api_secret)
    elif args.query:
        run_query(db_path, args.currency or None, args.since or None)
    else:
        run_cached_scan()

if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as _exc:
        if os.environ.get("DEBUG"):
            raise
        _prog = os.path.basename(sys.argv[0])
        sys.exit(f"{_prog}: {type(_exc).__name__}: {_exc}")
