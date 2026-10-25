"""Invented showcase shapes: compatibility, verified loading and cutoff semantics."""
import csv
from dataclasses import replace
import sqlite3

import pytest

from src.synthetic_dataset import (DatasetConfig, OUTPUT_NAMES, SHOWCASE_SHAPES,
                                   build_account, generate_dataset, profile_config)
from src.synthetic_database import build_database
from src.slack_review_service import ReviewService
from src.score_chart import render_chart


def test_showcase_preserves_all_existing_accounts():
    default, showcase = DatasetConfig(), profile_config('showcase')
    for market in (1, 2, 3):
        for ordinal in range(1, 25):
            assert build_account(default, market, ordinal) == build_account(showcase, market, ordinal)


@pytest.mark.parametrize('ordinal', [25, 26])
def test_shapes_and_extended_calendar(ordinal):
    config = profile_config('showcase')
    for market in (1, 2, 3):
        rows, metadata = build_account(config, market, ordinal)
        family, scores = SHOWCASE_SHAPES[ordinal]
        assert tuple(r.score for r in rows[-12:]) == scores
        assert all(r.bin == 1 + r.score // 50 for r in rows)
        assert metadata['scenario_id'] == family and len(metadata['tail_periods']) == 12
        assert metadata['kind'] == 'showcase' and not metadata['omitted_periods']
    rows, _ = build_account(replace(config, months=36, seed=42), 1, ordinal)
    assert tuple(r.score for r in rows[-12:]) == SHOWCASE_SHAPES[ordinal][1]
    assert rows[-1].period == '1992-12-01'


@pytest.mark.parametrize('overrides', [{'showcase':1}, {'accounts_per_market':25}, {'new_market_months':8}])
def test_invalid_showcase_configuration(overrides):
    with pytest.raises(ValueError):
        replace(profile_config('showcase'), **overrides)


@pytest.fixture
def showcase_db(tmp_path):
    inputs, output = tmp_path/'inputs', tmp_path/'db'
    manifest = generate_dataset(profile_config('showcase'), inputs)
    receipt = build_database(inputs, output)
    assert (manifest['accounts'], manifest['rows'], manifest['omitted_rows']) == (78, 1500, 60)
    assert (receipt['accounts'], receipt['rows']) == (78, 1500)
    assert receipt['verification'].startswith('all three input files exactly reproduced')
    return output/'scores.db'


def test_showcase_reproducibility_and_preserved_default_csv(tmp_path):
    config = profile_config('showcase')
    a, b = tmp_path/'a', tmp_path/'b'
    generate_dataset(config, a);generate_dataset(config, b)
    assert all((a/name).read_bytes() == (b/name).read_bytes() for name in OUTPUT_NAMES)
    default=tmp_path/'default';generate_dataset(DatasetConfig(), default)
    with (a/'scores_extended.csv').open() as stream:
        existing=[r for r in csv.DictReader(stream, delimiter=';') if int(r['account_id']) % 1000000 <= 24]
    with (default/'scores_extended.csv').open() as stream:
        assert existing == list(csv.DictReader(stream, delimiter=';'))


@pytest.mark.parametrize('account,month,status,action', [
    (1000025, '1991-05', 'attention_required', 'committee_review'),
    (1000025, '1991-12', 'no_signal_in_scope', 'none'),
    (1000026, '1991-12', 'attention_required', 'review_warning_signal'),
])
def test_actual_policy_outcomes_and_chart(showcase_db, account, month, status, action):
    result=ReviewService(showcase_db).review(f'review account {account} as of {month}')
    assert result['status'] == status and result['next_action'] == action
    assert result['coverage_complete'] is True
    assert result['chart']['periods'][-1] == month+'-01'
    assert render_chart(result['chart']).startswith(b'\x89PNG')
    assert result['note']['publishable'] is False


@pytest.mark.parametrize('account', [1000025, 1000026])
def test_later_history_cannot_change_earlier_review(showcase_db, account):
    command=f'review account {account} as of 1991-05'
    service=ReviewService(showcase_db);before=service.review(command)
    with sqlite3.connect(showcase_db) as conn:
        conn.execute("UPDATE scores SET score=400, bin=9 WHERE account_id=? AND period>'1991-05-01'", (account,))
    assert service.review(command) == before
