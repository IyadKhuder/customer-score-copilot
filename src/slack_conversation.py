"""Bounded English request interpretation and extractive policy answers.

No model, database, conversation memory or side effects during interpretation.
Only canonical review parameters reach the existing review service. Policy
answers quote selected-corpus passages; they are not model-generated notes.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class PreparedMessage:
    kind: str
    command: str = ''
    message: str = ''
    topic: str = ''
    query: str = ''


MONTHS = {name: index for index, names in enumerate((
    ('jan', 'january'), ('feb', 'february'), ('mar', 'march'), ('apr', 'april'),
    ('may',), ('jun', 'june'), ('jul', 'july'), ('aug', 'august'),
    ('sep', 'sept', 'september'), ('oct', 'october'), ('nov', 'november'),
    ('dec', 'december')), 1) for name in names}
MONTH_PATTERN = r'\b(' + '|'.join(sorted(MONTHS, key=len, reverse=True)) + r')\.?\s+([0-9]{4})\b'
TOPICS = (
    ('POL-006', r'\brecover\w*\b|\breinstat\w*\b'),
    ('POL-005', r'\bvolatil\w*\b|\bdata.quality\b|\bmissing\b'),
    ('POL-007', r'\bcommittee\b|\bescalat\w*\b'),
    ('POL-004', r'\bexception\w*\b|\bonboard\w*\b'),
    ('POL-003', r'\blimits?\b|\brecommended actions?\b|\brisk bands?\b'),
    ('POL-002', r'\bwarnings?\b|\bconsecutive\b|\bthresholds?\b'),
    ('POL-001', r'\bbins?\b|\bprime\b|\bstandard\b'),
)


def _reply(message):
    return PreparedMessage('message', message=message)


def prepare_message(text):
    """Recognize common paraphrases; ask rather than guess unsupported semantics."""
    from src.slack_review_service import parse_request, USAGE
    scope = ("I can review customer score histories in this demo's database and look up its policy rules. "
             "I can't answer general questions.\n\n" + USAGE)
    if not isinstance(text, str) or len(text) > 2000:
        return _reply('Please send a short account-review or demo-policy question.\n\n' + USAGE)
    text = text.strip()
    lower = text.lower()
    if lower in ('', 'help', 'hello', 'hi', 'what can you do?', 'what can you do'):
        return _reply(USAGE)
    # Retain the exact original command grammar and numeric validation.
    try:
        request = parse_request(text)
    except ValueError:
        request = None
    if request is not None:
        return PreparedMessage('review', command=text)
    if re.search(r'<[@!#]|https?://|\b(ignore|bypass|override|approve|execute|delete|password|secrets?)\b|\bdrop\s+table\b', lower):
        return _reply('I can explain recorded scores and demo policies, but cannot carry out other instructions or change decisions.\n\n' + USAGE)
    if re.search(r'\b(do not|don.t|never|stop)\s+(?:review|scan|check)\b', lower):
        return _reply('No review was started. Send an account and assessment month when you want a review.\n\n' + USAGE)
    if re.search(r'\b(weather|capital|poems?|jokes?|stories|songs?|recipes?|football|stocks?|news|translate)\b', lower):
        return _reply(scope)
    # A list of explicit accounts is detected, never silently reduced to one.
    groups = re.findall(r'\b(?:accounts?|customers?|ids?)\s*(?:id\s*)?[#:=]?\s*(-?[0-9]+(?:\s*(?:,|and|&)\s*#?[0-9]+)*)', lower)
    accounts = [number for group in groups for number in re.findall(r'-?[0-9]+', group)]
    if not accounts:
        accounts = re.findall(r'(?<![0-9-])[0-9]{7,19}(?![0-9-])', lower)
    explicit_account = bool(re.search(r'\b(accounts?|customers?|ids?)\b', lower))
    iso = re.findall(r'\b[0-9]{4}-[0-9]{2}(?:-[0-9]{2})?\b', lower)
    named = re.findall(MONTH_PATTERN, lower)
    periods = iso + [f'{int(year):04d}-{MONTHS[month]:02d}' for month, year in named]
    topic_match = re.findall(r'\bpol-00[1-7]\b', lower)
    if not accounts and not periods and re.search(r'\b(policy|policies|rules?|definition|mean|work)\b', lower):
        explicit_account = False
    # Account requests take precedence over general policy definitions.
    if accounts or explicit_account or periods:
        if len(accounts) > 1 or re.search(r'\b(compare|versus|vs\.?|between)\b', lower):
            return _reply('I can review one account at a time. Please send each account in a separate request with its assessment month.\n\n' + USAGE)
        if not accounts:
            return _reply('Which account ID should I review? Please include the assessment month as well, for example December 1991.\n\n' + USAGE)
        if len(periods) != 1:
            return _reply('Which single assessment month should I use? Please specify YYYY-MM or a month and year, such as December 1991. Each request is independent.\n\n' + USAGE)
        if re.search(r'\b(through|until|from|during|since)\b', lower):
            return _reply('I currently review one assessment cutoff, rather than an arbitrary date range. Please ask for an account as of a specific month.\n\n' + USAGE)
        if re.search(r'\b(cause|caused|responsible|reason behind)\b', lower):
            return _reply("I can explain recorded score movements and which demo rules apply, but the data does not establish what caused a score change. Please ask for a review at a specific month.\n\n" + USAGE)
        if re.search(r'\b(eligible|eligibility|reinstate|reinstated|clear|cleared|approve|approved)\b', lower):
            return _reply('I can describe score changes and look up general recovery rules, but cannot establish account reinstatement eligibility or operational warning clearance.\n\n' + USAGE)
        markets = re.findall(r'\bmarkets?\s+([a-z0-9]+)\b', lower)
        if re.search(r'\bmarkets?\b', lower) and (len(markets) != 1 or markets[0] not in ('1', 'one')):
            return _reply('Account reviews currently support market 1 only. I cannot review other markets through this interface.\n\n' + USAGE)
        if re.search(r'\b(predict\w*|forecast\w*|tomorrow)\b|next (?:month|year)', lower):
            return _reply('I can review recorded observations up to a month you specify, but cannot predict future scores.\n\n' + USAGE)
        if accounts and not re.search(r'\b(review|check|show|summar\w*|history|scores?|risk|change\w*|trend\w*|happened|perform\w*|declin\w*|improv\w*|recover\w*|warnings?|drop\w*|rise|look|assess\w*|explain|report)\b|tell me|what about|how about', lower):
            return _reply('I can review recorded score movements for an account, but could not identify a supported review question. Please ask about its score history or warning rules.\n\n' + USAGE)
        remaining = lower
        for group in groups:
            remaining = remaining.replace(group, '', 1)
        if not groups:
            remaining = re.sub(r'(?<![0-9-])[0-9]{7,19}(?![0-9-])', '', remaining)
        remaining = re.sub(r'\b[0-9]{4}-[0-9]{2}(?:-[0-9]{2})?\b', '', remaining)
        remaining = re.sub(MONTH_PATTERN, '', remaining)
        remaining = re.sub(r'\bmarkets?\s+(?:1|one)\b', '', remaining)
        if re.search(r'[0-9]', remaining):
            return _reply('The additional numbers make this request ambiguous. Please specify one account ID and one assessment month.\n\n' + USAGE)
        command = f'review account {accounts[0]} as of {periods[0]}'
        try:
            parse_request(command)
        except ValueError:
            return _reply("I couldn't recognize a valid account ID and assessment month. Use a positive account ID and YYYY-MM, with a year from 0002 onwards.\n\n" + USAGE)
        # Natural questions are requests for the supported bounded review, not
        # authorization for new tools or interpretations of unseen history.
        focus = ''
        if re.search(r'\brecover\w*\b', lower):
            focus = 'This review describes observed score changes; it does not establish formal recovery or reinstatement eligibility.'
        elif re.search(r'\bwhy\b', lower):
            focus = 'This review explains selected rule indications from recorded movements, rather than the underlying cause of score changes.'
        return PreparedMessage('review', command=command, message=focus)
    # Pronouns do not silently inherit another user's account or cutoff.
    if re.search(r'\b(?:did|has|is|was|will|can|does)\s+it\b|\bits\s+(?:score|warning)\b', lower):
        return _reply('Please include the account ID and assessment month. I do not carry account context between messages yet.\n\n' + USAGE)
    topics = list(dict.fromkeys(value.upper() for value in topic_match))
    if not topics:
        topics = [code for code, pattern in TOPICS if re.search(pattern, lower)]
    if topics and not (topic_match or re.search(r'\b(policy|policies|risk|score|bin|bins|warnings?|recovery|reinstatement|volatility|committee|escalation|exceptions?|onboard\w*)\b|data.quality|recommended actions?', lower)):
        return _reply('Are you asking about this demo’s policies? Please name the policy topic, such as score bins or warning thresholds.\n\n' + USAGE)
    if topics:
        # Prefer the specific subject over the word "policy" itself. Multiple
        # topics are clarified rather than conflated into a policy conclusion.
        if len(set(topics)) > 1:
            return _reply('Please ask about one policy topic at a time: bins, warnings, recovery, volatility, exceptions, actions or committee review.\n\n' + USAGE)
        return PreparedMessage('policy', topic=topics[0], query=text)
    if re.search(r'\bpolic(?:y|ies)\b|\brules?\b', lower):
        return _reply('Which demo policy topic would you like: score bins, warning thresholds, recovery, data quality, exceptions, recommended actions or committee review?\n\n' + USAGE)
    return _reply(scope)


def policy_answer(prepared, *, source_root=None):
    """Retrieve exact, attributed passages from one selected demo memo.

    No provider calls, account scans or account-specific conclusions. Text and
    headings come from the local corpus, never the incoming question.
    """
    if prepared.kind != 'policy' or prepared.topic not in {code for code, _ in TOPICS}:
        raise ValueError('Unsupported policy topic')
    version = os.getenv('POLICY_VERSION', 'v1.0').strip()
    root = Path(source_root) if source_root is not None else ROOT
    directories = {'v1.0': root/'policy_memos', 'v1.1': root/'experiments'/'policies'/'v1_1'}
    if version not in directories:
        raise ValueError('Unsupported policy version')
    directory = directories[version]
    # The path is selected by application code, never from user/model text.
    paths = sorted(directory.glob(prepared.topic + '_*.md'))
    if len(paths) != 1 or paths[0].is_symlink():
        raise ValueError('Selected memo unavailable')
    from src.retriever import _chunk_markdown, build_index, retrieve
    chunks = _chunk_markdown(paths[0].read_text(encoding='utf-8'), paths[0].name)
    if not chunks:
        raise ValueError('Empty policy memo')
    vectorizer, matrix = build_index(chunks)
    hits = retrieve(prepared.query, chunks, vectorizer, matrix, k=2)
    if not hits:
        return 'I could not find a matching passage in the selected demo policy. Please rephrase the policy question.'
    lines = [f'Demo policy reference | {version}',
             'These are retrieved policy excerpts, not an account-specific eligibility decision.']
    for hit in hits:
        # Require a verbatim match to a chunk in the selected source snapshot.
        if not any(all(hit.get(key) == chunk[key] for key in ('source', 'heading', 'text')) for chunk in chunks):
            raise ValueError('Unattributed policy passage')
        excerpt = hit['text'][:1800]
        suffix = ' [excerpt truncated]' if len(hit['text']) > 1800 else ''
        lines.extend(['', f"Source: {hit['source']} | {hit['heading']}", excerpt + suffix])
    lines.extend(['', 'General policy information only. No account was reviewed and no LLM was called.'])
    return '\n'.join(lines)
