"""Find pressure and payment phrases that scam and copycat sites tend to use."""
import re

# (label shown in the app, pattern). Patterns are matched case-insensitively against the page text.
PATTERNS = (
    ('UPI only', r'(pay(ment)?s?\s+(by|via|through)\s+upi\s+only|upi\s+only|accepted\s+only\s+through\s+upi|only\s+through\s+upi)'),
    ('No cash on delivery', r'(cash on delivery[^.]{0,30}not accepted|no\s+cod\b)'),
    ('Huge discount', r'\b([6-9]\d|100)\s?%\s?off\b'),
    ('Countdown pressure', r'(offer ends in|expires in [\d:]+|reserved for [\d:]+ ?minutes?|today only|hurry)'),
    ('Scarcity pressure', r'(only \d+ (left|pieces left|prizes left)|stock clearance|limited stock)'),
    ('Prize or reward', r'(congratulations|you won|you are a winner|claim (my|your) prize|free\b[^.]{0,20}worth)'),
    ('Fee to claim', r'(pay (a small )?₹\s?\d+[^.]{0,40}(fee|shipping)|non-refundable)'),
    ('No returns', r'(no returns|non-returnable)'),
    ('WhatsApp-only contact', r'(whatsapp only|contact:? whatsapp)'),
    ('Claims to be official', r'(official (sale|store|partner)|100% original|retail partner|authori[sz]ed)'),
)


# The most telling phrase first, for the one-line summary on the Phrases tab card.
PRIORITY = ('UPI only', 'Fee to claim', 'Prize or reward', 'Huge discount', 'WhatsApp-only contact', 'No returns',
            'Countdown pressure', 'Scarcity pressure', 'No cash on delivery', 'Claims to be official')


def headline(found):
    labels = [label for label, *_ in found]
    return next((label for label in PRIORITY if label in labels), None)


def find_phrases(text, context=48):
    """Return [(label, before, match, after)] for every pattern found in `text`, one per label, in page order."""
    found = []
    for label, pattern in PATTERNS:
        hit = re.search(pattern, text or '', flags=re.IGNORECASE)
        if hit:
            start, end = hit.span()
            before = text[max(0, start - context):start]
            after = text[end:end + context]
            found.append((hit.start(), label, ('…' if start > context else '') + before, hit.group(0),
                          after + ('…' if end + context < len(text) else '')))
    return [item[1:] for item in sorted(found)]
