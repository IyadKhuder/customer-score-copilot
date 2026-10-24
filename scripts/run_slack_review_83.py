"""Local Slack review demo. Scans by default; live notes require explicit options."""
import argparse
import os
from uuid import uuid4
from pathlib import Path

from dotenv import load_dotenv

from src.slack_review_service import ReviewService
from src.slack_listener import SlackReviewListener, register_listener

ROOT = Path(__file__).resolve().parents[1]


def settings():
    load_dotenv(ROOT / '.env')
    names = ('SLACK_BOT_TOKEN', 'SLACK_APP_TOKEN', 'SLACK_ALLOWED_CHANNEL_ID')
    values = {name: os.environ.get(name, '').strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise SystemExit('Missing configuration: ' + ', '.join(missing))
    for name, prefix in (('SLACK_BOT_TOKEN', 'xoxb-'), ('SLACK_APP_TOKEN', 'xapp-')):
        if not values[name].startswith(prefix):
            raise SystemExit(f'{name} must start with {prefix}')
    return values


def parse_options(argv=None):
    parser = argparse.ArgumentParser(description='Local Slack reviews; live notes are opt-in.')
    parser.add_argument('--confirm-live', action='store_true')
    parser.add_argument('--provider', choices=['gemini', 'anthropic'])
    parser.add_argument('--daily-remaining', type=int)
    parser.add_argument('--max-requests', type=int, default=8, help='Total session SDK request cap, 1..500')
    parser.add_argument('--per-review-max-requests', type=int, default=8)
    parser.add_argument('--interval-seconds', type=float, default=5.0)
    args = parser.parse_args(argv)
    if args.confirm_live and (args.provider is None or args.daily_remaining is None):
        parser.error('--confirm-live requires --provider and --daily-remaining')
    if not args.confirm_live and (args.provider is not None or args.daily_remaining is not None):
        parser.error('Provider/allowance options require --confirm-live')
    # Validate limits before Slack connects, even in offline mode.
    from src.request_budget import RequestBudget
    try:
        RequestBudget(daily_remaining=args.daily_remaining if args.confirm_live else 0,
                      max_requests=args.max_requests, interval_seconds=args.interval_seconds)
        if not 1 <= args.per_review_max_requests <= 8:
            raise ValueError()
    except (ValueError, TypeError):
        parser.error('Require allowance 0..500, session cap 1..500, review cap 1..8, finite interval >=5')
    return args


def build_service(args):
    if not args.confirm_live:
        return ReviewService(ROOT / 'data' / 'scores.db')
    from src.config import get_provider_config
    from src.llm_client import get_llm_client
    try:
        config = get_provider_config(args.provider)
    except Exception:
        raise SystemExit('Selected provider configuration is unavailable. Check the local API key and provider settings.') from None
    # Check configuration now; create a fresh SDK adapter only for eligible reviews.
    kind = config['adapter']
    def factory():
        return get_llm_client(args.provider), kind
    log_dir = ROOT / 'results' / 'slack' / str(uuid4())
    log_dir.mkdir(parents=True, exist_ok=False)
    return ReviewService(ROOT / 'data' / 'scores.db', client_factory=factory,
                         daily_remaining=args.daily_remaining, session_max_requests=args.max_requests,
                         per_review_max_requests=args.per_review_max_requests,
                         interval_seconds=args.interval_seconds, log_dir=log_dir)


def main():
    args = parse_options()
    values = settings()
    service = build_service(args)
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    from slack_sdk import WebClient
    web = WebClient(token=values['SLACK_BOT_TOKEN'], retry_handlers=[])
    handler = None
    try:
        app = App(client=web)
        register_listener(app, SlackReviewListener(service, values['SLACK_ALLOWED_CHANNEL_ID']))
        handler = SocketModeHandler(app, values['SLACK_APP_TOKEN'])
        mode = (f'live notes enabled; provider={args.provider}; session request allowance={service.remaining}'
                if args.confirm_live else 'local scans only; live notes disabled')
        print(f'Starting Score Copilot: market 1; {mode}. Ctrl+C stops it.', flush=True)
        if service.log_dir is not None:
            print(f'Slack session ID: {service.log_dir.name}', flush=True)
            print(f'Request logs: {service.log_dir}', flush=True)
        handler.start()
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit('Slack startup or connection failed. Check local settings and connectivity; credentials were not printed.') from None
    finally:
        if handler is not None:
            try:
                handler.close()
            except Exception:
                print('Slack connection cleanup could not complete.')


if __name__ == '__main__':
    main()
