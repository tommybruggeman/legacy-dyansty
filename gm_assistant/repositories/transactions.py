from __future__ import annotations

from typing import Any

from gm_assistant.repositories.common import RepositoryResult, require_scoped_context, result, rows
from gm_assistant.request_context import AssistantRequestContext


class TransactionRepository:
    """Read-only, league-scoped transaction history access."""

    TABLES = ("transaction_ledger", "transactions_enriched")

    def __init__(self, sb: Any):
        self.sb = sb

    def get_league_transactions(self, context: AssistantRequestContext, *, limit: int = 1000) -> RepositoryResult:
        require_scoped_context(context)
        loaded: list[dict[str, Any]] = []
        for table_name in self.TABLES:
            try:
                table_rows = rows(
                    self.sb.table(table_name)
                    .select("*")
                    .eq("league_id", context.league_id)
                    .limit(max(1, min(int(limit or 1000), 2000)))
                )
            except Exception:
                continue
            loaded.extend([{**row, "_source_name": table_name} for row in table_rows])
        return result(domain="transactions", source_name="transaction_ledger/transactions_enriched", context=context, rows=loaded, scope="league")
