"""Deterministic market/ticker -> isolated CDECR Runtime Registry binding."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from doxagent.cdecr_integration.contracts import RuntimeRegistryBinding

_SAFE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _scope_part(value: str, *, name: str) -> str:
    normalized = value.strip().upper()
    if not normalized or any(character not in _SAFE for character in normalized):
        raise ValueError(f"{name} must use only A-Z, 0-9, dot, underscore, or hyphen")
    if normalized in {".", ".."}:
        raise ValueError(f"invalid {name}")
    return normalized


class PerTickerRegistryResolver:
    """Own the binding metadata without changing CDECR's business tables."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def resolve(self, *, market: str, ticker: str) -> RuntimeRegistryBinding:
        normalized_market = _scope_part(market, name="market")
        normalized_ticker = _scope_part(ticker, name="ticker")
        path = (self.root / normalized_market / normalized_ticker / "runtime.sqlite3").resolve()
        if self.root not in path.parents:
            raise ValueError("resolved Runtime Registry escapes configured root")
        return RuntimeRegistryBinding(
            market=normalized_market,
            ticker=normalized_ticker,
            runtime_scope=f"cdecr:{normalized_market}:{normalized_ticker}",
            registry_path=str(path),
        )

    def bind(self, *, market: str, ticker: str) -> RuntimeRegistryBinding:
        binding = self.resolve(market=market, ticker=ticker)
        path = Path(binding.registry_path)
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            preexisting_tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            if (
                existed
                and preexisting_tables
                and "doxagent_ticker_binding" not in preexisting_tables
            ):
                raise ValueError(
                    "existing Runtime Registry is unbound; explicit legacy binding is required"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS doxagent_ticker_binding (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    market TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    runtime_scope TEXT NOT NULL UNIQUE,
                    binding_version TEXT NOT NULL
                )
                """
            )
            row = connection.execute(
                "SELECT * FROM doxagent_ticker_binding WHERE singleton=1"
            ).fetchone()
            expected = (
                binding.market,
                binding.ticker,
                binding.runtime_scope,
                binding.binding_version,
            )
            if row is None:
                connection.execute(
                    """
                    INSERT INTO doxagent_ticker_binding(
                        singleton,market,ticker,runtime_scope,binding_version
                    ) VALUES (1,?,?,?,?)
                    """,
                    expected,
                )
            elif tuple(str(item) for item in row[1:]) != expected:
                raise ValueError("Runtime Registry is already bound to a different ticker scope")
            connection.commit()
        return binding

    def verify(self, binding: RuntimeRegistryBinding) -> None:
        expected = self.resolve(market=binding.market, ticker=binding.ticker)
        if expected != binding:
            raise ValueError("Runtime Registry binding path or scope is not canonical")
        path = Path(binding.registry_path)
        if not path.exists():
            raise FileNotFoundError(path)
        try:
            with sqlite3.connect(path) as connection:
                row = connection.execute(
                    """
                    SELECT market,ticker,runtime_scope,binding_version
                    FROM doxagent_ticker_binding WHERE singleton=1
                    """
                ).fetchone()
        except sqlite3.OperationalError as exc:
            raise ValueError("Runtime Registry ticker binding is missing") from exc
        if row is None or tuple(str(item) for item in row) != (
            binding.market,
            binding.ticker,
            binding.runtime_scope,
            binding.binding_version,
        ):
            raise ValueError("Runtime Registry ticker binding is missing or mismatched")
