"""Read-only queue query; filter before pagination without changing package code hashes."""
from contextlib import closing


def queue_ids(store, limit, offset, workflow_status=None):
    where = ''
    params = []
    if workflow_status is not None:
        where = "WHERE json_extract(c.body, '$.workflow_status') = ? "
        params.append(workflow_status)
    with closing(store.connect()) as db:
        rows = db.execute(
            'SELECT r.case_id FROM api_requests r JOIN review_cases c ON c.case_id=r.case_id '
            + where + 'ORDER BY r.rowid DESC LIMIT ? OFFSET ?',
            (*params, limit + 1, offset)).fetchall()
    return [r[0] for r in rows[:limit]], len(rows) > limit
