import sqlite3, json, sys

DB='data/trades.db'
try:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute('SELECT reference_id, bill_id, fill_id, category, error, occurrences, disposition, first_seen_at, last_seen_at, payload_json FROM execution_fill_quarantine ORDER BY last_seen_at DESC').fetchall()
    if not rows:
        print('No quarantined fill rows found.')
        sys.exit(0)
    for r in rows:
        print('REF:', r['reference_id'])
        print('  bill_id:', r['bill_id'])
        print('  fill_id:', r['fill_id'])
        print('  category:', r['category'])
        print('  error:', r['error'])
        print('  occurrences:', r['occurrences'])
        print('  disposition:', r['disposition'])
        print('  first_seen_at:', r['first_seen_at'])
        print('  last_seen_at:', r['last_seen_at'])
        try:
            payload = json.loads(r['payload_json']) if r['payload_json'] else {}
        except Exception:
            payload = r['payload_json']
        print('  payload:', json.dumps(payload, ensure_ascii=False))
        print('---')
finally:
    conn.close()
