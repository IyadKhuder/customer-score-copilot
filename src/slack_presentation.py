"""Slack Block Kit presentation of already-approved display text.

No scoring, policy evaluation, provider access or factual rewriting. Text is
carried in native literal text elements, never concatenated into mrkdwn.
"""
import re

_ACCOUNT = re.compile(r'Account [0-9]+ \| review month: [0-9]{4}-[0-9]{2} \| market 1\Z')
_MONTH = re.compile(r'[0-9]{4}-[0-9]{2}: [0-9]+\Z')
_DETAIL = ('Observed drop:', 'Subsequent recovery:', 'Observed rise:', 'Subsequent decline:',
           'Net change across those observations:')
_LABELS = ('Score pattern:', 'Descriptive coverage:', 'Data available for the required policy review period:',
           'Missing descriptive months:', 'Missing months:', 'Next step:', 'Source:', 'LLM requests used:')
_ITALIC = ('Descriptive window:', 'This trajectory description', 'Observed score recovery',
           'An upward trend alone', 'This describes recorded', 'This review describes',
           'This review explains', 'These are retrieved policy excerpts', 'General policy information only.',
           'Verified score, bin,', 'Only the stated verified claim scope')


def _text(value, **style):
    item = {'type':'text', 'text':value}
    if style:
        item['style'] = style
    return item


def _section(line):
    if line.startswith(('Yes,', 'Yes:', 'No observed recovery', 'No clear drop-and-recovery',
                        'I cannot reliably determine', 'I cannot establish a clear recovery',
                        'There is not enough monthly data', 'Review outcome:')):
        elements = [_text(line, bold=True)]
    elif line.startswith(_ITALIC):
        elements = [_text(line, italic=True)]
    elif line.startswith(('Use:', 'Example:')):
        label, content = line.split(':', 1)
        elements = [_text(label+': ', bold=True), _text(content.lstrip(), code=True)]
    elif line.startswith(_LABELS + _DETAIL):
        label, content = line.split(':', 1)
        elements = [_text(label+':', bold=True), _text(content)]
    elif line == 'Reported scores (12-month descriptive window):':
        elements = [_text(line, bold=True)]
    else:
        elements = [_text(line)]
    return {'type':'rich_text_section', 'elements':elements}


def presentation_blocks(text):
    """Return bounded native blocks or None to retain plain-text delivery.

    Header limits and a conservative element cap are enforced before sending.
    No user/URL/channel/broadcast elements are ever created. Consequently text
    that resembles mentions, markdown or code fences remains literal data.
    """
    if not isinstance(text, str) or not text or len(text)>3200:
        return None
    lines = text.splitlines()
    if len(lines)>100:
        return None
    blocks, elements = [], []

    def flush():
        if elements:
            blocks.append({'type':'rich_text', 'elements':list(elements)})
            elements.clear()

    i=0
    while i<len(lines):
        line=lines[i]
        if (_ACCOUNT.fullmatch(line) or line == 'Verified note'
                or re.fullmatch(r'Demo policy reference \| v1\.[01]',line)) and len(line)<=150:
            flush()
            blocks.append({'type':'header','text':{'type':'plain_text','text':line,'emoji':False}})
        elif _MONTH.fullmatch(line):
            months=[]
            while i<len(lines) and _MONTH.fullmatch(lines[i]):
                months.append(lines[i]);i+=1
            elements.append({'type':'rich_text_preformatted','elements':[_text('\n'.join(months))]})
            continue
        elif line.startswith(_DETAIL):
            items=[]
            while i<len(lines) and lines[i].startswith(_DETAIL):
                items.append(_section(lines[i]));i+=1
            elements.append({'type':'rich_text_list','style':'bullet','elements':items})
            continue
        elif line:
            section = _section(line)
            section['elements'].append(_text('\n'))
            elements.append(section)
        else:
            elements.append(_section('\n'))
        i+=1
    flush()
    if len(blocks)>40 or sum(len(b.get('elements',[])) for b in blocks)>80:
        return None
    return blocks or None
