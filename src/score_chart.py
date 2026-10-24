"""Bounded chart data and in-memory PNG rendering; no DB, provider or Slack calls."""
from datetime import date
from io import BytesIO
from threading import Lock

_RENDER_LOCK = Lock()
_KEYS = {'schema_version', 'account_id', 'as_of_period', 'periods', 'scores', 'quality_review'}


def _month(period):
    if not isinstance(period, str):
        raise ValueError('Invalid chart period')
    parsed = date.fromisoformat(period)
    if parsed.day != 1 or parsed.isoformat() != period:
        raise ValueError('Invalid chart period')
    return parsed.year * 12 + parsed.month - 1


def _period(index):
    year, month = divmod(index, 12)
    return f'{year:04d}-{month+1:02d}-01'


def validate_chart(chart):
    """Reject malformed or off-scale data instead of silently clipping it."""
    if type(chart) is not dict or set(chart) != _KEYS:
        raise ValueError('Invalid chart data')
    if (type(chart['schema_version']) is not int or chart['schema_version'] != 1
            or type(chart['account_id']) is not int or not 1 <= chart['account_id'] <= 2**63-1
            or type(chart['quality_review']) is not bool):
        raise ValueError('Invalid chart metadata')
    end = _month(chart['as_of_period'])
    if end < 12:
        raise ValueError('Invalid chart calendar')
    if chart['periods'] != [_period(i) for i in range(end-11, end+1)]:
        raise ValueError('Chart must contain twelve consecutive calendar months')
    scores = chart['scores']
    if (type(scores) is not list or len(scores) != 12
            or any(s is not None and (type(s) is not int or not 0 <= s <= 449) for s in scores)
            or all(s is None for s in scores)):
        raise ValueError('Chart requires observed demonstration scores in range 0..449')
    return chart


def prepare_chart(result):
    """Project one validated account snapshot into twelve monthly display values.

    None means no eligible recent observations. No synthetic scenario label is read.
    The source scan has already validated account isolation, dates and market.
    """
    if (result['scan_outcome'] not in ('attention_required', 'no_signal_in_scope', 'insufficient_data')
            or result.get('market') != 1 or 'ACCOUNT_NOT_FOUND' in result['reason_codes']):
        return None
    end = _month(result['as_of_period'])
    periods = [_period(i) for i in range(end-11, end+1)]
    observed = {}
    for row in result['scan_evidence']:
        index = _month(row['period'])
        if end-11 <= index <= end:
            if row['market'] != 1 or row['period'] in observed:
                raise ValueError('Invalid chart observations')
            observed[row['period']] = row['score']
    if not observed:
        return None
    chart = dict(schema_version=1, account_id=result['account_id'],
                 as_of_period=result['as_of_period'], periods=periods,
                 scores=[observed.get(period) for period in periods],
                 quality_review='DATA_QUALITY_REVIEW_REQUIRED' in result['reason_codes'])
    return validate_chart(chart)


def render_chart(chart):
    """Render one fixed-style PNG as bytes, without pyplot or temporary files.

    Same observations/style on each run; pixels can vary with platform/font builds.
    Agg is explicitly used, so neither a desktop nor a GUI backend is required.
    """
    validate_chart(chart)
    # Lazy imports keep text-only mode usable if plotting is unavailable.
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    with _RENDER_LOCK:
        fig = Figure(figsize=(10, 5), dpi=140, facecolor='white')
        FigureCanvasAgg(fig)
        try:
            ax = fig.add_subplot(111)
            scores = [float('nan') if s is None else s for s in chart['scores']]
            ax.plot(range(12), scores, color='#24578A', marker='o', linewidth=2.2, markersize=5)
            for i, score in enumerate(chart['scores']):
                if score is None:
                    ax.axvspan(i-.35, i+.35, color='#eeeeee', zorder=0)
                    ax.text(i, 12, 'Missing', rotation=90, ha='center', va='bottom',
                            color='#666666', fontsize=8)
            ax.set_xlim(-.5, 11.5)
            ax.set_ylim(0, 449)
            ax.set_yticks([0, 100, 200, 300, 400, 449])
            ax.set_xticks(range(12), [p[:7] for p in chart['periods']], rotation=45, ha='right')
            ax.set_ylabel('Recorded score (0–449)', fontsize=11)
            ax.set_title(f"Account {chart['account_id']} | as of {chart['as_of_period'][:7]}",
                         fontsize=14, loc='left', pad=28)
            ax.text(0, 1.025, 'Synthetic demonstration data | last 12 calendar months',
                    transform=ax.transAxes, fontsize=10, color='#555555')
            ax.grid(axis='y', color='#dddddd', linewidth=.7)
            ax.set_axisbelow(True)
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            caption = ('Data-quality review required: movements may reflect suspect observations.'
                       if chart['quality_review'] else
                       'Recorded observations; this chart does not establish causes or authorize credit decisions.')
            fig.text(.10, .035, caption, fontsize=9, color='#555555')
            fig.subplots_adjust(left=.10, right=.97, top=.79, bottom=.24)
            with BytesIO() as output:
                fig.savefig(output, format='png', dpi=140, metadata={'Software': 'Score Copilot'})
                return output.getvalue()
        finally:
            fig.clear()
