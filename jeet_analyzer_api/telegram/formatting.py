"""Conservative projection of structured Jeet results, never a risk engine."""
from html import escape

STATUSES = frozenset({'VERIFIED_OUT','NOT_OUT','RE_ENTERED','INSUFFICIENT_DATA','UNRESOLVED','UNKNOWN','PARTIALLY_OUT','NO_EXIT_PROVEN'})


def format_result(record):
    state=record['state']
    job=escape(record['id'])
    if state in {'queued','running'}:
        return f'<b>JEET ANALYZER</b>\nJob <code>{job}</code>\nStatus: {state}\nRequested: {escape(record["created_at"])}'
    if state!='complete':
        return f'<b>JEET ANALYZER</b>\nJob <code>{job}</code>\nInvestigation stopped without a new verified conclusion.\nUse the website for the authorized receipt.'
    result=record.get('result') or {}
    safe=record.get('safe_result') or {}
    # Group-safe projection only. No provider errors, URLs, wallet relationships,
    # raw transactions, free-form assertions, usernames, or account identifiers.
    verdict=safe.get('verdict') or {}
    lines=['<b>JEET ANALYZER</b>',f'Job <code>{job}</code>',f'Updated: {escape(record["updated_at"])}',
           'Observed facts: see the authorized evidence receipt.']
    for key,label in [('historical_exit_status','Historical exit'),('current_wallet_status','Current wallet'),('target_cluster_status','Bounded cluster')]:
        value=verdict.get(key)
        if value in STATUSES:
            lines.append(f'Engine assessment — {label}: {value}')
    coverage=result.get('provider_coverage')
    if isinstance(coverage,dict) and isinstance(coverage.get('complete'),bool):
        lines.append('Coverage: '+('complete within the recorded scope' if coverage['complete'] else 'INCOMPLETE'))
    lines.extend(['Claim verification: unsupported in this version.', 'Risk: not assessed by Telegram.', 'Ownership / common control: NOT_PROVEN.'])
    return '\n'.join(lines)
