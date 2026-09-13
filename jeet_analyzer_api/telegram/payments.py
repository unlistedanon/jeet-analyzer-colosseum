"""DM payment presentation only. SolPayments alone verifies and grants access."""
from dataclasses import dataclass
import os
from pathlib import Path
import time

from fastapi import HTTPException


def payments_enabled(config):
    """Environment gate plus instant operator override, independent of forensics."""
    stop = Path(os.getenv('JEET_TELEGRAM_SOL_PAYMENTS_STOP_FILE', str(config.stop_file.with_name('telegram-sol-payments.STOP'))))
    return os.getenv('JEET_TELEGRAM_SOL_PAYMENTS_ENABLED', 'false').lower() in {'true', '1'} and not stop.exists()


@dataclass
class PaymentReply:
    text: str
    markup: object = None
    invoice_id: str | None = None
    owner: str | None = None


class TelegramSolSurface:
    def __init__(self, gateway, store, config):
        self.gateway, self.store, self.config = gateway, store, config

    def audit(self, uid, owner, state, invoice=None, reason=None):
        fields = {'account_id':owner, 'payment_state':state, 'payment_flag':payments_enabled(self.config)}
        if reason: fields['reason']=reason
        if invoice:
            fields.update(invoice_id=invoice['id'], amount_required=invoice['lamports'], recipient=invoice['recipient'],
                          signature=invoice.get('signature'), confirmed_at=invoice.get('paid_at'),
                          entitlement_granted=bool(invoice.get('signature')))
        self.store.event(uid,uid,'payment',**fields)

    def access(self, uid, command='access', identifier=None):
        try:
            owner=self.gateway.authorize(uid)
        except PermissionError:
            text,link=self.gateway.access(uid)
            return PaymentReply(text,link)
        core=self.gateway.payments
        if not payments_enabled(self.config):
            self.audit(uid,owner,'surface_disabled')
            if core.membership(owner):
                return PaymentReply('JEET ACCESS\nStatus: ACTIVE\nSOL payment controls are disabled here.',owner=owner)
            return PaymentReply('JEET ACCESS\nStatus: INACTIVE\nSOL payment controls are disabled here. Existing Jeet functionality remains available.',owner=owner)
        invoice=None
        try:
            if command=='checkpayment':
                invoice=core.invoice_for_owner(owner,identifier) if identifier else core.pending_invoice(owner)
                if invoice:
                    # Owner is always derived from the authenticated numeric identity.
                    was_settled=bool(invoice.get('signature'))
                    verified=core.verify(owner,invoice['id'])
                    self.audit(uid,owner,'settled_reused' if was_settled else 'settled',verified)
                    if not core.membership(owner):
                        return PaymentReply('Payment receipt confirmed. Membership has expired; no new entitlement was granted.')
                    return PaymentReply('Payment confirmed.\nJEET ACCESS\nStatus: ACTIVE',owner=owner)
                if core.membership(owner):
                    return PaymentReply('JEET ACCESS\nStatus: ACTIVE',owner=owner)
                return PaymentReply('No payment session found. Send /access first.')
            if core.membership(owner):
                self.audit(uid,owner,'active')
                return PaymentReply('JEET ACCESS\nStatus: ACTIVE',owner=owner)
            if not core.config.enabled or not core.beta.enabled:
                self.audit(uid,owner,'checkout_paused')
                return PaymentReply('JEET ACCESS\nStatus: INACTIVE\nNew SOL payments are paused.',owner=owner)
            invoice=core.pending_invoice(owner)
            if invoice is None:
                invoice=core.create_invoice(owner)
            if invoice['expires']<=time.time():
                self.audit(uid,owner,'expired',invoice)
                return PaymentReply('JEET ACCESS\nPayment session expired.\nUse /checkpayment to check a payment already sent. Do not pay again.',owner=owner)
            self.audit(uid,owner,'awaiting_payment',invoice)
            # A regular transfer without the instruction-bound reference cannot qualify.
            guidance=('Use the website wallet link/QR to include the reference; an ordinary send will not qualify.'
                      if invoice['payment_url'] else 'Devnet test session only. Use a reference-aware devnet test client; mainnet wallet links are disabled.')
            text=(f'JEET ACCESS\nStatus: PAYMENT REQUIRED\nRequired: {invoice["amount_sol"]} SOL'
                  f'\nNetwork: {invoice["network"]}\nSend to:\n{invoice["recipient"]}'
                  f'\nReference:\n{invoice["reference"]}\nSession: {invoice["id"]}'
                  f'\n{guidance}'
                  '\nAfter sending: /checkpayment')
            return PaymentReply(text,{'payment_check':invoice['id'],'url':self.config.public_base_url.rstrip('/')+'/'},invoice['id'],owner)
        except HTTPException as exc:
            state={404:'unavailable',422:'rejected',429:'throttled',503:'provider_unavailable',409:'awaiting_payment'}.get(exc.status_code,'failed')
            if getattr(exc,'payment_state',None)=='replay_rejected': state='replay_rejected'
            if invoice and invoice['expires']<=time.time() and state=='awaiting_payment': state='expired'
            self.audit(uid,owner,state,invoice,reason=str(exc.status_code))
            text={
                'unavailable':'Payment session unavailable for this account.',
                'rejected':'Payment could not be verified. No access granted.',
                'replay_rejected':'This transaction was already credited. No additional access was granted.',
                'throttled':'Please wait before checking payment again.',
                'provider_unavailable':'Payment service temporarily unavailable. Do not pay again.',
                'awaiting_payment':'No qualifying finalized payment detected yet. Wait and check again; do not pay again.',
                'expired':'Payment session expired; no qualifying finalized payment detected. Do not pay again.',
            }.get(state,'Payment verification unavailable. Do not pay again.')
            return PaymentReply(text,owner=owner)
        except Exception:
            self.audit(uid,owner,'failed',invoice,reason='UNAVAILABLE')
            return PaymentReply('Payment service temporarily unavailable. Do not pay again.',owner=owner)
