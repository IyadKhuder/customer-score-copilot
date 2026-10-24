"""Bounded diagnostics for failed assisted cases. Never changes gates or prompts."""
import argparse
import ast
import json
from pathlib import Path

from src.experiment_metadata import build_experiment_metadata
from src.request_budget import RequestBudget


def diagnostic_summary(result, root):
    """Allowlisted audit facts; never copy draft prose, histories or limitations text."""
    known_reasons=set()
    for name in ('zero_decline_narrative.py','grace_decline_narrative.py','narrative_composer.py',
                 'temporal_narrative.py','combined_temporal_narrative.py','quality_hold_narrative.py',
                 'narrative_findings.py','temporal_policy_mapping.py'):
        path=Path(root)/'src'/name
        if not path.exists():continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            if (isinstance(node,ast.Raise) and isinstance(node.exc,ast.Call) and node.exc.args
                and isinstance(node.exc.args[0],ast.Constant) and isinstance(node.exc.args[0].value,str)):
                known_reasons.add(node.exc.args[0].value)
    audit=result.get('verification') or {}
    rendering=audit.get('rendering') or {}
    reason=rendering.get('reason')
    statuses={'passed','failed','incomplete','not_applicable'}
    safe_status=lambda v:v if v in statuses else None
    count=lambda v:len(v) if isinstance(v,list) else None
    allowed_codes={'COMPOSITION_UNSUPPORTED','POLICY_EVIDENCE_INSUFFICIENT','POLICY_MISMATCH',
                   'NUMERIC_MISMATCH','SCHEMA_INVALID','VERIFICATION_FAILED'}
    return {
        'publishable':result.get('publishable') is True,
        'reason_codes':[c for c in result.get('reason_codes',[]) if c in allowed_codes],
        'next_action':result.get('next_action') if result.get('next_action') in ('publish','revise_response','gather_evidence') else None,
        'audit_status':safe_status(audit.get('status')),
        'section_statuses':{k:safe_status((audit.get(k) or {}).get('status')) for k in ('schema','numeric','policy','rendering')},
        'limitations_count':count(audit.get('limitations')),
        'audit_issues_count':count(audit.get('issues')),
        'numeric_checks_count':count((audit.get('numeric') or {}).get('checks')),
        'policy_checks_count':count((audit.get('policy') or {}).get('checks')),
        'composer_reason':reason if reason in known_reasons else ('unrecognized_reason_omitted' if reason else None),
        'helper_called':any(isinstance(e,dict) and e.get('tool_name')=='evaluate_policy_signals' for e in result.get('tool_evidence',[])),
    }


def main(argv=None,*,client_factory=None,sleeper=None,clock=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--provider',choices=('anthropic','gemini'),default='anthropic')
    p.add_argument('--cases',nargs='+',choices=('CF-002','CF-005','CF-006'),default=['CF-002','CF-005','CF-006'])
    p.add_argument('--daily-requests-remaining',type=int,required=True)
    p.add_argument('--max-api-requests',type=int,default=12)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--confirm-live-api',action='store_true')
    a=p.parse_args(argv)
    if not a.confirm_live_api:p.error('Live diagnostic requires --confirm-live-api')
    if a.output_dir.exists():p.error('Output exists; choose a new directory')
    if len(set(a.cases))!=len(a.cases):p.error('Cases must be distinct')
    root=Path(__file__).resolve().parents[1]
    opts={}
    if sleeper is not None:opts['sleeper']=sleeper
    if clock is not None:opts['clock']=clock
    budget=RequestBudget(daily_remaining=a.daily_requests_remaining,max_requests=a.max_api_requests,**opts)
    if budget.limit<1:p.error('No request allowance remains')
    plan=build_experiment_metadata(root,provider=a.provider,between_case_seconds=0,max_rate_limit_retries=0)
    from src.evaluation_dataset import load_benchmark
    from src.evaluation_runner import load_fixtures
    from src.live_evaluation import run_live_case
    from src.llm_client import get_llm_client,AnthropicClient,OpenAICompatibleClient
    benchmark=load_benchmark();fixtures=load_fixtures(benchmark=benchmark)
    by_id={c['id']:c for c in benchmark['cases']};by_scenario={f['scenario_id']:f for f in fixtures['fixtures']}
    a.output_dir.mkdir()
    def save(name,data):
        with (a.output_dir/name).open('x',encoding='utf-8') as f:json.dump(data,f,indent=2);f.write('\n')
    save('plan.json',plan)
    injected=client_factory is not None
    client=(client_factory or get_llm_client)(a.provider)
    expected=AnthropicClient if a.provider=='anthropic' else OpenAICompatibleClient
    if type(client) is not expected or client.model!=plan['provider_configuration']['configured_model']:
        raise ValueError('Adapter/model differs from configuration')
    budget.wrap_adapter(client,plan['provider_configuration']['adapter'])
    budget.attach_log(a.output_dir/'requests.jsonl')
    rows=[]
    try:
        for cid in a.cases:
            case=by_id[cid]
            try:
                result=run_live_case(case,by_scenario[case['scenario_id']],client,
                    policy_passage_variant='clarified_v1',policy_tool_suite=True,max_tool_rounds=4)
                row=dict(case_id=cid,diagnostic=diagnostic_summary(result,root),error_type=None)
            except Exception as exc:row=dict(case_id=cid,diagnostic=None,error_type=type(exc).__name__)
            rows.append(row)
            save(cid+'.json',row)
            print(json.dumps(row))
    finally:budget.close()
    save('summary.json',dict(record_kind='injected_diagnostic' if injected else 'provider_diagnostic',
        provider=a.provider,cases=rows,request_budget=budget.summary(),
        limits='Fresh diagnostic samples, not reconstruction of previous failed responses. Not a six-case benchmark.'))
    print(f'Diagnostic bundle: {a.output_dir}; requests: {budget.started}/{budget.limit}')
    return 1 if any(r['error_type'] for r in rows) else 0


if __name__=='__main__':raise SystemExit(main())
