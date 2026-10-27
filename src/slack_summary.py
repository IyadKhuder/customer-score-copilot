"""Deterministic display descriptions, separate from policy/risk classification."""
from src.score_chart import DISPLAY_MONTHS, display_periods

# Display heuristic only: not a policy threshold or a formal eligibility test.
# Require a substantial step followed by at least two smaller moves in the
# reverse direction; equal-size oscillations are deliberately not "recovery".
DISPLAY_SHOCK_POINTS = 40


def _reversal_description(rows):
    values = [r['score'] for r in rows]
    changes = [b-a for a, b in zip(values, values[1:])]
    candidates = []
    for direction in (-1, 1):
        index = max(range(len(changes)), key=lambda i: direction*changes[i])
        shock = direction*changes[index]
        if shock < DISPLAY_SHOCK_POINTS:
            continue
        pivot = (min(range(index+1, len(values)), key=lambda i: values[i]) if direction == -1
                 else max(range(index+1, len(values)), key=lambda i: values[i]))
        # direction=-1 chooses the trough, +1 the peak.
        after = changes[pivot:]
        reversing = [c for c in after if direction*c < 0]
        setbacks = [c for c in after if direction*c > 0]
        if (len(reversing) < 2 or len(reversing) <= len(setbacks)
                or not after or max(abs(c) for c in after) > shock/2
                or direction*(values[-1]-values[pivot]) >= 0):
            continue
        candidates.append((shock, index, pivot, direction))
    if not candidates:
        return None
    _, index, pivot, direction = max(candidates)
    baseline, turning, latest = values[index], values[pivot], values[-1]
    before_month, pivot_month, end_month = (rows[i]['period'][:7] for i in (index, pivot, len(rows)-1))
    if direction == -1:
        label = ('drop followed by partial recovery' if latest < baseline
                 else 'drop followed by recovery to or above the pre-drop score')
        difference = baseline-latest
        position = (f'still {difference} points below the pre-drop score' if difference > 0
                    else 'back at the pre-drop score' if difference == 0
                    else f'{-difference} points above the pre-drop score')
        details = [f'Observed drop: {baseline} in {before_month} to {turning} in {pivot_month} ({turning-baseline:+d} points).',
                   f'Subsequent recovery: {latest-turning:+d} points to {latest} in {end_month}; {position}.',
                   'Observed score recovery does not establish formal reinstatement eligibility.']
    else:
        label = 'rise followed by gradual decline'
        difference = latest-baseline
        position = (f'still {difference} points above the pre-rise score' if difference > 0
                    else 'back at the pre-rise score' if difference == 0
                    else f'{-difference} points below the pre-rise score')
        details = [f'Observed rise: {baseline} in {before_month} to {turning} in {pivot_month} ({turning-baseline:+d} points).',
                   f'Subsequent decline: {latest-turning:+d} points to {latest} in {end_month}; {position}.']
    kind = ('partial_recovery' if latest < baseline else 'recovery') if direction == -1 else 'rise_decline'
    return label, details, kind


def describe_scan(result):
    """Return bounded display strings from the validated scan snapshot only.

    No scenario metadata, account-ID lookup, provider or additional DB read.
    Twelve calendar months match the chart; policy windows remain independent.
    """
    if result['scan_outcome'] not in ('attention_required', 'no_signal_in_scope', 'insufficient_data'):
        return []
    if 'ACCOUNT_NOT_FOUND' in result['reason_codes']:
        return []
    periods = display_periods(result['as_of_period'])
    rows = sorted((r for r in result['scan_evidence'] if r['period'] in periods), key=lambda r: r['period'])
    decision = result.get('policy_decision') or {}
    flags = decision.get('data_quality_flags', [])
    complete = len(rows) == DISPLAY_MONTHS and {r['period'] for r in rows} == set(periods)
    lines = [f'Descriptive window: {periods[0][:7]} to {periods[-1][:7]} (12 calendar months, matching the chart).']
    lines.append('This trajectory description covers only that window; it does not describe earlier events. Policy checks use their own required evaluation periods.')
    lines.append(f'Descriptive coverage: {len(rows)} of {DISPLAY_MONTHS} monthly observations available.')
    details = []
    if flags:
        lines.append('Score pattern: possible data-quality issue; check the recorded movements before drawing a risk conclusion.')
        if any(f['kind'] == 'exact_reversal_candidate' for f in flags):
            lines.append('A score movement returned exactly to an earlier value. Check whether this reversal reflects valid observations or a data correction.')
        if any(f['kind'] == 'single_change_over_150' for f in flags):
            lines.append('A month-to-month change of more than 150 points was flagged for checking.')
    elif not complete:
        lines.append('Score pattern: not enough monthly data to describe the full twelve-month trend reliably.')
    else:
        values = [r['score'] for r in rows]
        changes = [b-a for a,b in zip(values,values[1:])]
        severe = any(e['review_state'] == 'eligible' for e in decision.get('severe_downward_migrations', []))
        sudden = any(e['criterion'] == 'score_drop' and e['review_state'] == 'eligible'
                     for e in decision.get('events', []))
        reversal = _reversal_description(rows)
        if reversal:
            label, details, _ = reversal
        elif severe:
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
        elif (values[-1]-values[0] >= DISPLAY_SHOCK_POINTS
              and sum(c > 0 for c in changes) > sum(c < 0 for c in changes)
              and max((-c for c in changes if c < 0), default=0) <= (values[-1]-values[0])/4):
            label = 'overall improving, with smaller intervening decreases'
        elif (values[0]-values[-1] >= DISPLAY_SHOCK_POINTS
              and sum(c < 0 for c in changes) > sum(c > 0 for c in changes)
              and max((c for c in changes if c > 0), default=0) <= (values[0]-values[-1])/4):
            label = 'overall declining, with smaller intervening increases'
        else:
            label = 'mixed; the score moved both up and down'
        lines.append('Score pattern: ' + label + '.')
        lines.extend(details)
    missing = [p[:7] for p in periods if p not in {r['period'] for r in rows}]
    if missing:
        lines.append('Missing descriptive months: ' + ', '.join(missing) + '. No values were filled or interpolated.')
    if rows:
        lines.append('Reported scores (12-month descriptive window):')
        lines.extend(f"{r['period'][:7]}: {r['score']}" for r in rows)
        if len(rows) > 1:
            delta = rows[-1]['score'] - rows[0]['score']
            lines.append(f"Net change across those observations: {delta:+d} points.")
    if complete and not flags and all(c >= 0 for c in changes) and any(c > 0 for c in changes):
        lines.append('An upward trend alone does not establish recovery or reinstatement eligibility.')
    if any(e['criterion'] == 'volatility' for e in decision.get('events', [])):
        lines.append('The review rules also detected large score swings (volatility).')
    return lines


def describe_recovery(result):
    """Answer the observed-recovery question from the same validated snapshot.

    Three-way result: supported yes/no, or an explicit limitation. No prose is
    reinterpreted to recover facts and no policy eligibility decision is made.
    """
    if (result['scan_outcome'] not in ('attention_required', 'no_signal_in_scope', 'insufficient_data')
            or 'ACCOUNT_NOT_FOUND' in result['reason_codes']):
        return None
    periods = display_periods(result['as_of_period'])
    rows = sorted((r for r in result['scan_evidence'] if r['period'] in periods), key=lambda r:r['period'])
    if (result.get('policy_decision') or {}).get('data_quality_flags'):
        return 'I cannot reliably determine observed recovery in this 12-month window until the flagged data-quality issues are checked.'
    if len(rows) != DISPLAY_MONTHS or {r['period'] for r in rows} != set(periods):
        return 'There is not enough monthly data to determine a recovery pattern across this full 12-month window.'
    reversal = _reversal_description(rows)
    if reversal:
        kind = reversal[2]
        if kind == 'partial_recovery':
            return 'Yes, partially: the 12-month history shows recovery after a drop, but the latest score remains below its pre-drop level.'
        if kind == 'recovery':
            return 'Yes: the 12-month history shows recovery after a drop, with the latest score back at or above its pre-drop level.'
        return 'No clear drop-and-recovery pattern is identified in this 12-month window; it shows a rise followed by decline.'
    changes = [b['score']-a['score'] for a,b in zip(rows,rows[1:])]
    if all(c <= 0 for c in changes):
        return 'No observed recovery is shown within this 12-month window; the score was unchanged or declined.'
    if all(c >= 0 for c in changes):
        return 'The score improved within this 12-month window, but no preceding drop is visible, so recovery from a drop cannot be established here.'
    return 'I cannot establish a clear recovery pattern within this 12-month window; the recorded movements are mixed.'
