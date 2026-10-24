"""Deterministic display descriptions, separate from policy/risk classification."""
from datetime import date


def _month(period):
    d = date.fromisoformat(period)
    return d.year * 12 + d.month - 1


def describe_scan(result):
    """Return bounded display strings from the validated scan snapshot only.

    No scenario metadata, account-ID lookup, provider or additional DB read.
    Six calendar months are descriptive, not a replacement policy window.
    """
    if result['scan_outcome'] not in ('attention_required', 'no_signal_in_scope', 'insufficient_data'):
        return []
    if 'ACCOUNT_NOT_FOUND' in result['reason_codes']:
        return []
    end = _month(result['as_of_period'])
    rows = sorted((r for r in result['scan_evidence']
                   if end - 5 <= _month(r['period']) <= end), key=lambda r: r['period'])
    decision = result.get('policy_decision') or {}
    flags = decision.get('data_quality_flags', [])
    complete = len(rows) == 6 and {_month(r['period']) for r in rows} == set(range(end-5, end+1))
    lines = []
    if flags:
        lines.append('Score pattern: possible data-quality issue; check the recorded movements before drawing a risk conclusion.')
        if any(f['kind'] == 'exact_reversal_candidate' for f in flags):
            lines.append('A score movement returned exactly to an earlier value. Check whether this reversal reflects valid observations or a data correction.')
        if any(f['kind'] == 'single_change_over_150' for f in flags):
            lines.append('A month-to-month change of more than 150 points was flagged for checking.')
    elif not complete:
        lines.append('Score pattern: not enough monthly data to describe the full six-month trend reliably.')
    else:
        values = [r['score'] for r in rows]
        changes = [b-a for a,b in zip(values,values[1:])]
        severe = any(e['review_state'] == 'eligible' for e in decision.get('severe_downward_migrations', []))
        sudden = any(e['criterion'] == 'score_drop' and e['review_state'] == 'eligible'
                     for e in decision.get('events', []))
        if severe:
            label = 'severe score drop detected by the review rules'
        elif sudden:
            label = 'a sudden score drop was detected by the review rules'
        elif all(c <= 0 for c in changes) and any(c < 0 for c in changes):
            label = 'declining; the score fell without an intervening increase'
        elif all(c >= 0 for c in changes) and any(c > 0 for c in changes):
            label = 'improving; the score rose without an intervening decrease'
        elif len(set(values)) == 1:
            label = 'unchanged'
        elif len({r['bin'] for r in rows}) == 1 and decision.get('warning_signal_status') == 'not_triggered':
            label = 'broadly stable; scores fluctuated within the same score band'
        else:
            label = 'mixed; the score moved both up and down'
        lines.append('Score pattern: ' + label + '.')
    if rows:
        lines.append('Reported scores (recent months): ' + ', '.join(
            f"{r['period'][:7]}: {r['score']}" for r in rows) + '.')
        if len(rows) > 1:
            delta = rows[-1]['score'] - rows[0]['score']
            lines.append(f"Net change across those observations: {delta:+d} points.")
    if complete and not flags and all(c >= 0 for c in changes) and any(c > 0 for c in changes):
        lines.append('An upward trend alone does not establish recovery or reinstatement eligibility.')
    if any(e['criterion'] == 'volatility' for e in decision.get('events', [])):
        lines.append('The review rules also detected large score swings (volatility).')
    return lines
