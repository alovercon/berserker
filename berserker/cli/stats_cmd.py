"""Statistics CLI command for berserker - token usage and session statistics."""

from __future__ import print_function

import time
from typing import Optional, Dict, List, Any, Tuple

from berserker.storage import get_db


def _format_number(n):
    # type: (int) -> str
    """Format number with commas."""
    return "{:,}".format(n)


def _format_table(headers, rows):
    # type: (List[str], List[List[str]]) -> str
    """Format aligned text table."""
    if not rows:
        return ""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))
    fmt = " | ".join("{:<%d}" % w for w in widths)
    lines = [fmt.format(*headers), "-+-".join("-" * w for w in widths)]
    for row in rows:
        lines.append(fmt.format(*[str(c) for c in row]))
    return "\n".join(lines)


def _parse_date(date_str):
    # type: (Optional[str]) -> Optional[int]
    """Parse date string to timestamp. Returns None if invalid."""
    if date_str is None:
        return None

    # Try to parse as Unix timestamp first
    try:
        return int(date_str)
    except ValueError:
        pass

    # Try common date formats
    import datetime

    formats = [
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ]

    for fmt in formats:
        try:
            dt = datetime.datetime.strptime(date_str, fmt)
            return int(dt.timestamp())
        except ValueError:
            continue

    return None


def cmd_stats(from_date=None, to_date=None):
    # type: (Optional[str], Optional[str]) -> None
    """Display token usage statistics and session stats.

    Args:
        from_date: Optional start date filter (ISO format or Unix timestamp)
        to_date: Optional end date filter (ISO format or Unix timestamp)
    """
    db = get_db()

    # Parse date filters
    from_ts = _parse_date(from_date) if from_date else None
    to_ts = _parse_date(to_date) if to_date else None

    # Build WHERE clause for date filtering
    where_clauses = []
    params = []

    if from_ts is not None:
        where_clauses.append("created_at >= ?")
        params.append(from_ts)

    if to_ts is not None:
        where_clauses.append("created_at <= ?")
        params.append(to_ts)

    where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    # Get session statistics
    session_query = """
    SELECT 
        COUNT(*) as total_sessions,
        AVG(message_count) as avg_messages_per_session,
        AVG(total_tokens) as avg_tokens_per_session
    FROM (
        SELECT 
            s.id,
            s.created_at,
            COUNT(m.id) as message_count,
            SUM(CASE 
                WHEN m.data IS NOT NULL THEN 
                    json_extract(m.data, '$.usage.total_tokens')
                ELSE 0 
            END) as total_tokens
        FROM session s
        LEFT JOIN message m ON s.id = m.session_id
        {}
        GROUP BY s.id, s.created_at
    )
    """.format(where_clause)

    session_stats = db.fetchone(session_query, params)

    if not session_stats or session_stats["total_sessions"] == 0:
        print("No sessions found. Start a conversation to see statistics.")
        return

    total_sessions = session_stats["total_sessions"]
    avg_messages = session_stats["avg_messages_per_session"] or 0
    avg_tokens = session_stats["avg_tokens_per_session"] or 0

    # Get token usage by provider/model
    token_usage_query = """
    SELECT 
        json_extract(m.data, '$.provider') as provider,
        json_extract(m.data, '$.model') as model,
        SUM(json_extract(m.data, '$.usage.total_tokens')) as total_tokens
    FROM session s
    JOIN message m ON s.id = m.session_id
    {}
    GROUP BY provider, model
    HAVING total_tokens > 0 AND provider IS NOT NULL AND model IS NOT NULL
    ORDER BY total_tokens DESC
    """.format(where_clause)

    token_usage_rows = db.fetchall(token_usage_query, params)

    # Print session statistics
    print("=== Session Statistics ===")
    print("Total sessions: {}".format(total_sessions))
    print("Average messages/session: {:.1f}".format(avg_messages))
    print("Average tokens/session: {:.1f}".format(avg_tokens))
    print()

    # Print token usage by provider/model if available
    if token_usage_rows:
        print("=== Token Usage by Provider ===")
        table_rows = []
        for row in token_usage_rows:
            provider = row["provider"] or "unknown"
            model = row["model"] or "unknown"
            tokens = int(row["total_tokens"]) if row["total_tokens"] else 0
            if tokens > 0:
                table_rows.append([provider, model, _format_number(tokens)])

        if table_rows:
            print(_format_table(["PROVIDER", "MODEL", "TOKENS USED"], table_rows))
        else:
            print("No token usage data available.")
    else:
        print("=== Token Usage by Provider ===")
        print("No token usage data available.")
