"""Session management CLI commands for berserker."""

from typing import Optional, List, Dict, Any
import json

from berserker.session.manager import session_manager


def _format_table(headers, rows):
    # type: (List[str], List[List[str]]) -> str
    """Format aligned text table."""
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


def cmd_session_list(limit=50):
    # type: (int) -> None
    """List sessions in table format sorted by created_at DESC."""
    sessions = session_manager.list_sessions(limit=limit)
    rows = []
    for s in sessions:
        rows.append(
            [
                s.get("id", "N/A")[:20],
                (s.get("title") or "Untitled")[:20],
                str(s.get("message_count", 0)),
                str(s.get("created_at", ""))[:19],
            ]
        )
    print(_format_table(["SESSION ID", "TITLE", "MESSAGES", "CREATED"], rows))


def cmd_session_delete(session_id):
    # type: (str) -> None
    """Delete a session and print confirmation."""
    session_manager.delete(session_id)
    print("Session {} deleted successfully.".format(session_id))


def cmd_export(session_id=None):
    # type: (Optional[str]) -> None
    """Export session(s) to JSON format."""
    if session_id is not None:
        data = session_manager.load(session_id)
        if data is None:
            print("Error: Session {} not found".format(session_id))
            return
        sessions_to_export = [data]
    else:
        sessions_to_export = session_manager.list_sessions(limit=500)
        for s in sessions_to_export:
            msgs = session_manager.get_messages(s["id"])
            s["messages"] = msgs

    export_data = {"sessions": []}
    for s in sessions_to_export:
        export_data["sessions"].append(
            {
                "id": s.get("id"),
                "title": s.get("title", "Untitled"),
                "messages": [
                    {
                        "role": m.get("role"),
                        "content": m.get("content"),
                        "created_at": m.get("created_at"),
                    }
                    for m in s.get("messages", [])
                ],
            }
        )
    print(json.dumps(export_data, indent=2, ensure_ascii=False))


def cmd_import(file_path):
    # type: (str) -> None
    """Import sessions from JSON file."""
    with open(file_path, "r") as f:
        import_data = json.load(f)

    sessions_imported = 0
    messages_imported = 0

    for sdata in import_data.get("sessions", []):
        sid = sdata.get("id")
        title = sdata.get("title", "Imported Session")
        messages = sdata.get("messages", [])

        if sid:
            existing = session_manager.load(sid)
            if existing is None:
                session_manager.create(session_id=sid, title=title)
        else:
            sid = session_manager.create(title=title)

        for msg in messages:
            session_manager.append_message(
                sid,
                role=msg.get("role", "user"),
                content=msg.get("content", ""),
            )
            messages_imported += 1
        sessions_imported += 1

    print("Imported {} sessions, {} messages".format(sessions_imported, messages_imported))
