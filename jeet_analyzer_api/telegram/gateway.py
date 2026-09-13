"""Application boundary: existing jobs, account identities and payment ledger."""
import os
from jeet_analyzer.targeting import classify_target
from ..beta_store import BetaLimitError


class JeetGateway:
    def __init__(self, config, beta, store, jobs, payments):
        self.config,self.beta,self.store,self.jobs,self.payments=config,beta,store,jobs,payments

    def valid_owner(self, owner):
        return owner in self.beta.code_hashes or (self.beta.public_access and owner.startswith('visitor-') and len(owner)==40)

    def authorize(self, user_id):
        owner=self.store.linked(user_id)
        if not owner or not self.valid_owner(owner):
            raise PermissionError('Website account link required')
        return owner

    def gate(self):
        if not self.config.enabled or self.config.stop_file.exists() or not self.beta.enabled:
            raise BetaLimitError('TELEGRAM_PAUSED','Investigations paused',503)
        # Explicit operational interlock after the observed Helius exhaustion.
        # No provider probe outside Jeet's reserved investigation budget.
        if os.getenv('JEET_TELEGRAM_PROVIDER_READY','0')!='1':
            raise BetaLimitError('PROVIDER_UNAVAILABLE','Provider admission is closed',503)

    def investigate(self, owner, mint, wallet):
        self.gate()
        return self.jobs.submit(code_id=owner,mint=mint,wallet=wallet)

    def scan(self, owner, mint):
        self.gate()
        return self.jobs.submit_scan(code_id=owner,mint=mint)

    def status(self, owner, identifier):
        return self.jobs.storage.get(identifier,code_id=owner) if identifier else None

    def classify(self, address):
        # No unbudgeted lookup. This contract can consume engine-owned account
        # receipts when a general target investigation is implemented.
        return classify_target(address)

    def report_url(self, identifier):
        return self.config.public_base_url.rstrip('/')+'/api/beta/investigations/'+identifier

    def access(self, user_id):
        try:
            owner=self.authorize(user_id)
        except PermissionError:
            token=self.store.pairing(user_id)
            return ('Link your existing Jeet website account. Sign in on the website first, then open this five-minute link and confirm your Telegram numeric ID.',
                    self.config.public_base_url.rstrip('/')+'/api/beta/telegram/link?challenge='+token)
        member=self.payments.membership(owner)
        pending=self.payments.pending_invoice(owner)
        if member:
            usage=self.payments.daily_usage(owner)
            from datetime import datetime, timezone
            expires=datetime.fromtimestamp(member['expires'],timezone.utc).isoformat()
            text=f'Membership active until {expires}. '
            if usage: text+=f'{usage["remaining"]} estimated credits remaining today. '
            text+='No daily scan-count limit.' if member['runs_daily']==0 else f'{member["runs_daily"]} scans/day.'
        else:
            text='Linked. Free access; no settled membership is active.'
            if pending:
                import time
                state='expired' if pending['expires']<=time.time() else 'requested / awaiting finalized verification'
                text+=' Payment: '+state+'.'
        text+=' Shared provider budgets and availability still apply. This bot reads your existing website entitlement.'
        return text,self.config.public_base_url.rstrip('/')+'/'
