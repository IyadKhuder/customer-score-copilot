"""Offline acceptance tests using real temporary SQLite databases."""
import json
import sqlite3
import subprocess
import sys

import pytest
from src import batch_scan as scan


def request(ids=None):
    return dict(schema_version=1, account_ids=ids or [1], market=1, as_of_period='2025-02-01')


def history(scores=None, bins=None, account=1, market=1):
    scores = scores if scores is not None else [600]*8
    return [(account, f'{2024+(6+i)//12}-{(6+i)%12+1:02d}-01', s,
             (bins or [6]*len(scores))[i], market) for i,s in enumerate(scores)]


def database(tmp_path, rows):
    path = tmp_path / 'scores.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE scores (account_id, period, score, bin, market)')
        conn.executemany('INSERT INTO scores VALUES (?,?,?,?,?)', rows)
    return path


def result(tmp_path, rows):
    return scan.scan_batch(request(), database(tmp_path, rows))['results'][0]


@pytest.mark.parametrize('change', [
    {'account_ids': []}, {'account_ids': [1,1]}, {'account_ids': list(range(1,27))},
    {'account_ids': [True]}, {'account_ids': [1.0]}, {'account_ids': [0]},
    {'account_ids': [2**63]}, {'market': 3}, {'schema_version': True},
    {'as_of_period': '2025-02-02'}, {'as_of_period': '2025-02-30'},
    {'as_of_period': None}, {'as_of_period': '0001-01-01'}, {'extra': 1},
])
def test_invalid_request_precedes_database_access(monkeypatch, change):
    monkeypatch.setattr(scan, '_snapshot', lambda *a: pytest.fail('DB accessed'))
    with pytest.raises(scan.InvalidScanRequest):
        scan.scan_batch(dict(request(), **change))


@pytest.mark.parametrize('scores,bins,outcome,warning,committee', [
    ([600]*8,None,'no_signal_in_scope','not_triggered','not_indicated'),
    ([600]*7+[560],None,'attention_required','triggered','not_indicated'),
    ([600]*7+[561],None,'no_signal_in_scope','not_triggered','not_indicated'),
    ([600]*8,[6]*7+[4],'attention_required','triggered','not_indicated'),
    ([600]*8,[6]*7+[3],'attention_required','triggered','indicated'),
    ([600]*7+[560],[6]*7+[4],'attention_required','triggered','indicated'),
])
def test_threshold_examples(tmp_path,scores,bins,outcome,warning,committee):
    r = result(tmp_path, history(scores,bins))
    assert r['scan_outcome'] == outcome
    assert r['policy_decision']['warning_signal_status'] == warning
    assert r['policy_decision']['committee_review_account_rules'] == committee
    assert r['coverage_complete'] is True
    assert r['narrative']['publishable'] is False


def test_historic_event_survives_flat_month(tmp_path):
    r = result(tmp_path,history([600]*4+[599,598,597,597]))
    assert [(e['period'],e['criterion']) for e in r['policy_decision']['events']] == [('2025-01-01','consecutive_declines')]


@pytest.mark.parametrize('scores', [[600]*6+[449,449],[600]*6+[560,600]])
def test_quality_hold(tmp_path,scores):
    r = result(tmp_path,history(scores))
    assert r['next_action'] == 'data_quality_review'
    assert r['policy_decision']['risk_action_gate'] == 'hold_pending_data_quality'
    assert 'SIGNAL_PENDING_DATA_QUALITY' in r['reason_codes']


@pytest.mark.parametrize('score,quality', [(450,False),(449,True)])
def test_150_boundary(tmp_path,score,quality):
    r = result(tmp_path,history([600]*7+[score]))
    assert (r['policy_decision']['data_quality_review']=='required') == quality


def test_short_positive_and_negative(tmp_path):
    report = scan.scan_batch(request([2,1]),database(tmp_path,history([600]*7+[560])[-2:]+history(account=2)[-2:]))
    positive,negative = report['results']
    assert positive['scan_outcome']=='attention_required'
    assert positive['coverage_complete'] is False
    assert 'INCOMPLETE_BASELINE' in positive['reason_codes']
    assert negative['scan_outcome']=='insufficient_data'
    assert sum(report['summary']['outcomes'].values())==2


def test_missing_and_unknown(tmp_path):
    report = scan.scan_batch(request([1,2]),database(tmp_path,history()[:-1]))
    assert [r['reason_codes'] for r in report['results']]==[['MISSING_AS_OF_OBSERVATION'],['ACCOUNT_NOT_FOUND']]


def test_future_and_account_order_invariance(tmp_path):
    path = database(tmp_path,history()+history(account=2))
    first = scan.scan_batch(request([2,1]),path)
    with sqlite3.connect(path) as conn:
        conn.execute('INSERT INTO scores VALUES (1,"2025-03-01","bad future score",1,3)')
    second = scan.scan_batch(request([1,2]),path)
    assert first['results']==second['results']
    assert first['source']['input_sha256']==second['source']['input_sha256']
    assert [r['account_id'] for r in second['results']]==[1,2]


def test_gap_does_not_bridge_streak(tmp_path):
    rows = history([600,600,600,600,599,598,597,596]); del rows[5]
    r = result(tmp_path,rows)
    assert r['coverage_complete'] is False
    assert not any(e['criterion']=='consecutive_declines' for e in r['policy_decision']['events'])


@pytest.mark.parametrize('bad_rows',[
    history()+[(1,'2025-02-02',600,6,1)],
    history()[:-1]+[(1,'2025-02-01',600.5,6,1)],
    history()[:-1]+[(1,'2025-02-01','600',6,1)],
    history()[:-1]+[(1,'2025-02-01',600,10,1)],
    history()[:-1]+[(1,'2025-02-01',600,6,3)],
    history()+[(1,'bad-date',600,6,1)],
    history()[:-1]+[(1,'2025-02-01',b'private bytes',6,1)],
])
def test_bad_account_isolation(tmp_path,bad_rows):
    report = scan.scan_batch(request([1,2]),database(tmp_path,bad_rows+history(account=2)))
    assert report['run_status']=='completed_with_errors'
    assert report['results'][0]['reason_codes']==['INVALID_HISTORY']
    assert report['results'][1]['scan_outcome']=='no_signal_in_scope'
    json.dumps(report,allow_nan=False)


def test_unsupported(tmp_path):
    r = result(tmp_path,history(market=3))
    assert r['scan_outcome']=='unsupported'
    assert r['policy_decision'] is None


def test_original_dates_preserved(tmp_path):
    r = result(tmp_path,[(a,p[:8]+'15',s,b,m) for a,p,s,b,m in history()])
    assert r['scan_outcome']=='no_signal_in_scope'
    assert r['scan_evidence'][-1]['period']=='2025-02-15'


def test_missing_db_not_created(tmp_path):
    path = tmp_path/'missing.db'
    report = scan.scan_batch(request(),path)
    assert report['run_status']=='failed' and report['results']==[]
    assert not path.exists()


def test_read_only(tmp_path):
    path = database(tmp_path,history()); before = path.read_bytes()
    scan.scan_batch(request(),path)
    assert path.read_bytes()==before


def test_unexpected_account_exception(tmp_path,monkeypatch):
    original = scan.evaluate_policy_decision
    def evaluate(rows,period):
        if rows[-1]['score']==560:
            raise RuntimeError('sensitive content')
        return original(rows,period)
    monkeypatch.setattr(scan,'evaluate_policy_decision',evaluate)
    report = scan.scan_batch(request([1,2]),database(tmp_path,history([600]*7+[560])+history(account=2)))
    assert report['results'][0]['reason_codes']==['ACCOUNT_SCAN_FAILED']
    assert report['results'][1]['scan_outcome']=='no_signal_in_scope'
    assert 'sensitive content' not in json.dumps(report)


def test_exclusive_report(tmp_path):
    path=tmp_path/'report.json'
    scan.save_scan_report({'ok':True},path); before=path.read_bytes()
    with pytest.raises(FileExistsError):
        scan.save_scan_report({'ok':False},path)
    assert path.read_bytes()==before


def test_fresh_process_without_providers_or_network(tmp_path):
    path=database(tmp_path,history())
    code='''
import sys,socket
socket.socket=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network'))
from src.batch_scan import scan_batch
assert scan_batch({'schema_version':1,'account_ids':[1],'market':1,'as_of_period':'2025-02-01'},sys.argv[1])['run_status']=='completed'
assert not set(('src.config','src.llm_client','openai','anthropic','dotenv')) & set(sys.modules)
'''
    subprocess.run([sys.executable,'-I','-c','import sys; sys.path.insert(0,'+repr(str(scan.ROOT))+');'+code,str(path)],check=True)


def test_cli(tmp_path):
    path=database(tmp_path,history()); output=tmp_path/'report.json'
    args=[sys.executable,'-m','scripts.scan_batch_712','--accounts','1','--as-of','2025-02-01','--db',str(path),'--output',str(output)]
    first=subprocess.run(args,cwd=scan.ROOT,capture_output=True,text=True)
    assert first.returncode==0, first.stderr
    assert json.loads(output.read_text())['run_status']=='completed'
    before=output.read_bytes()
    assert subprocess.run(args,cwd=scan.ROOT,capture_output=True).returncode==2
    assert output.read_bytes()==before


@pytest.mark.parametrize('amplitude,triggered', [(35,False),(36,True)])
def test_volatility_strict_boundary(tmp_path,amplitude,triggered):
    # Constant six-period offset ensures each six-period window has exact pstdev amplitude.
    scores=[600-amplitude,600+amplitude]*4
    r=result(tmp_path,history(scores))
    assert any(e['criterion']=='volatility' for e in r['policy_decision']['events']) == triggered


def test_distinct_criteria_across_months(tmp_path):
    r=result(tmp_path,history([600]*5+[560,555,555],[6]*6+[4,4]))
    d=r['policy_decision']
    assert d['analyst_referral']=='indicated'
    assert d['committee_review_account_rules']=='not_indicated'
    assert r['next_action']=='analyst_review'


def test_repeated_score_drop_is_not_two_criteria(tmp_path):
    # Short history intentionally avoids a volatility window or three-decline streak.
    rows=[(1,'2024-12-01',600,6,1),(1,'2025-01-01',560,6,1),(1,'2025-02-01',520,6,1)]
    r=result(tmp_path,rows)
    assert r['policy_decision']['analyst_referral']=='insufficient_evidence'
    assert 'ANALYST_REFERRAL_INDICATED' not in r['reason_codes']


def test_event_ending_before_window_is_excluded(tmp_path):
    # November loss outside December–February window, with no remaining volatility signal.
    r=result(tmp_path,history([600,600,600,560,560,560,560,560]))
    assert r['scan_outcome']=='no_signal_in_scope'
    assert r['policy_decision']['events']==[]


def test_one_snapshot_connection_with_transaction(tmp_path,monkeypatch):
    path=database(tmp_path,history()+history(account=2))
    original=scan.sqlite3.connect
    statements=[]; calls=[]
    def connect(*args,**kwargs):
        calls.append((args,kwargs))
        conn=original(*args,**kwargs)
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(scan.sqlite3,'connect',connect)
    assert scan.scan_batch(request([1,2]),path)['run_status']=='completed'
    assert len(calls)==1
    assert '?mode=ro' in calls[0][0][0]
    assert statements.index('BEGIN') < next(i for i,s in enumerate(statements) if s.startswith('SELECT'))
