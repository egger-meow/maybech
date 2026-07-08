import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trades.db")
DB_PATH = os.path.normpath(DB_PATH)
print("DB path:", DB_PATH)
print("exists:", os.path.exists(DB_PATH))
with sqlite3.connect(DB_PATH) as conn:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    print("tables:", [row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")])
    print("schema_migrations:", [dict(row) for row in cur.execute("SELECT component, version FROM schema_migrations ORDER BY component, version")])
    for typ in ["execution.fill_conflict", "execution.fill_rejected"]:
        rows = cur.execute(
            "SELECT id, type, source, created_at, payload_json FROM audit_events WHERE type = ? ORDER BY created_at DESC LIMIT 20",
            (typ,),
        ).fetchall()
        print("---", typ, "count", len(rows))
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            print({
                "id": row["id"],
                "type": row["type"],
                "source": row["source"],
                "created_at": row["created_at"],
                "payload": payload,
            })
    try:
        rows = cur.execute(
            "SELECT reference_id, bill_id, fill_id, category, error, occurrences, disposition, first_seen_at, last_seen_at, payload_json FROM execution_fill_quarantine ORDER BY last_seen_at DESC LIMIT 20"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        print("execution_fill_quarantine absent:", exc)
    else:
        print("--- quarantine count", len(rows))
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            print({
                "reference_id": row["reference_id"],
                "bill_id": row["bill_id"],
                "fill_id": row["fill_id"],
                "category": row["category"],
                "error": row["error"],
                "occurrences": row["occurrences"],
                "disposition": row["disposition"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "payload": payload,
            })
