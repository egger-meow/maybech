import shutil
import sqlite3
import json
import os
from datetime import datetime

DB = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'data', 'playwright-demo-20260704.db'))
BAK = DB + '.bak'
print('DB:', DB)
print('exists:', os.path.exists(DB))
print('Backing up DB to', BAK)
shutil.copy2(DB, BAK)
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
# show counts before
before = cur.execute("SELECT COUNT(*) as c FROM execution_fill_quarantine WHERE category='conflict'").fetchone()['c']
print('conflict rows before:', before)
# update dispositions to 'resolved'
now = datetime.now().isoformat()
cur.execute("UPDATE execution_fill_quarantine SET disposition='resolved', last_seen_at=? WHERE category='conflict'", (now,))
conn.commit()
after = cur.execute("SELECT COUNT(*) as c FROM execution_fill_quarantine WHERE category='conflict'").fetchone()['c']
resolved = cur.execute("SELECT COUNT(*) as c FROM execution_fill_quarantine WHERE disposition='resolved'").fetchone()['c']
print('conflict rows after (still category=conflict):', after)
print('resolved rows count:', resolved)
# show remaining rows
rows = cur.execute("SELECT reference_id,bill_id,fill_id,category,error,occurrences,disposition,last_seen_at FROM execution_fill_quarantine ORDER BY last_seen_at DESC LIMIT 20").fetchall()
for r in rows:
    print(dict(r))
conn.close()
print('Done')
