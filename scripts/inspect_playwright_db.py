import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'playwright-demo-20260704.db')
DB_PATH = os.path.normpath(DB_PATH)
print('DB path:', DB_PATH)
print('exists:', os.path.exists(DB_PATH))
with sqlite3.connect(DB_PATH) as conn:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    print('tables:', [row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")])
    try:
        rows = cur.execute('SELECT reference_id,bill_id,fill_id,category,error,occurrences,disposition,first_seen_at,last_seen_at,payload_json FROM execution_fill_quarantine ORDER BY last_seen_at DESC LIMIT 100').fetchall()
        print('quarantine count', len(rows))
        for r in rows:
            payload = json.loads(r['payload_json'] or '{}')
            print(r['reference_id'], r['bill_id'], r['fill_id'], r['category'], r['error'], r['occurrences'], r['disposition'], r['last_seen_at'])
    except Exception as e:
        print('no quarantine or error', e)
    rows = cur.execute("SELECT id,type,source,created_at,payload_json FROM audit_events WHERE type='execution.fill_conflict' ORDER BY created_at DESC LIMIT 200").fetchall()
    print('fill_conflict events', len(rows))
    for r in rows:
        print(r['created_at'], json.loads(r['payload_json'] or '{}'))
    rows = cur.execute("SELECT id,type,source,created_at,payload_json FROM audit_events WHERE type='execution.fill_rejected' ORDER BY created_at DESC LIMIT 200").fetchall()
    print('fill_rejected events', len(rows))
    for r in rows:
        print(r['created_at'], json.loads(r['payload_json'] or '{}'))
