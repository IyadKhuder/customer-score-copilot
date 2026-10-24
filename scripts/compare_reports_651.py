"""Print an offline comparison to stdout; never write input reports."""
import argparse
import json
from pathlib import Path
from src.report_comparison import compare_reports, render_markdown


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    path = Path(args.config).resolve()
    def read(relative):
        return json.loads((path.parent / relative).read_text(encoding='utf-8'))
    config = json.loads(path.read_text(encoding='utf-8'))
    entries = [{'label': e['label'], 'run': read(e['run']), 'scores': read(e['scores']),
                'condition': e.get('condition', {})} for e in config['runs']]
    result = compare_reports(entries, benchmark=read(config['benchmark']), fixtures=read(config['fixtures']))
    print(json.dumps(result, indent=2, allow_nan=False) if args.json else render_markdown(result), end='\n')


if __name__ == '__main__':
    main()
