"""No funds or network calls: real shared ledger with adversarial RPC fixtures."""
import asyncio
from dataclasses import replace
import json
import time
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from jeet_analyzer_api.app import create_app
from jeet_analyzer_api.sol_payments import PaymentConfig, GENESIS, b58encode
from jeet_analyzer_api.telegram.adapter import normalize_update
from jeet_analyzer_api.telegram.payments import payments_enabled
from tests.test_beta_api import beta_config, FakeBetaAdapter
from tests.test_sol_payments import RECIPIENT, SOURCE, SIGNATURE, receipt
from tests.test_telegram import config, update, Sender
from tests.test_ui_api import API_MINT, API_WALLET


@pytest.fixture
def rig(tmp_path,monkeypatch):
    monkeypatch.setenv('JEET_TELEGRAM_SOL_PAYMENTS_ENABLED','true')
    monkeypatch.setenv('JEET_TELEGRAM_SOL_PAYMENTS_STOP_FILE',str(tmp_path/'PAYMENTS.STOP'))
    monkeypatch.setenv('JEET_TELEGRAM_PROVIDER_READY','1')
    sender=Sender()
    app=create_app(engine_adapter=FakeBetaAdapter(),beta_config=beta_config(tmp_path),
        payment_config=PaymentConfig(enabled=True,recipient=RECIPIENT,price_lamports=10000000,days=30,credits=1200),
        telegram_config=config(tmp_path),telegram_sender=sender)
    with TestClient(app) as client:
        r=app.state.telegram
        r.store.confirm_pair(r.store.pairing(123),'tester-01')
        r.gateway.payments.rpc=Mock(side_effect=AssertionError('Unexpected RPC'))
        yield r,client,sender


def setup_rpc(core,tx,network='devnet'):
    def rpc(method,params):
        if method=='getGenesisHash': return GENESIS[network]
        if method=='getSignaturesForAddress':
            assert params[1]['commitment']=='finalized' and params[1]['limit']==5
            return [{'signature':SIGNATURE,'err':None}]
        assert method=='getTransaction' and params[1]['commitment']=='finalized'
        return tx
    core.rpc=Mock(side_effect=rpc)


def session(r):
    result=r.adapter.payments.access(123)
    return r.gateway.payments.invoice_for_owner('tester-01',result.invoice_id)


@pytest.mark.parametrize('command',['access','pay','payment'])
def test_aliases_reuse_web_invoice(rig,command):
    r,_,_=rig; core=r.gateway.payments
    invoice=core.create_invoice('tester-01')
    result=r.adapter.payments.access(123,command)
    assert result.invoice_id==invoice['id']
    assert invoice['recipient'] in result.text and invoice['reference'] in result.text
    assert invoice['amount_sol'] in result.text and 'devnet' in result.text
    assert core.pending_invoice('tester-01')['id']==result.invoice_id
    core.rpc.assert_not_called()
    assert core.membership('tester-01') is None


def test_exact_finalized_and_repeated_confirmation(rig):
    r,_,_=rig; core=r.gateway.payments; invoice=session(r)
    setup_rpc(core,receipt(invoice))
    result=r.adapter.payments.access(123,'checkpayment',invoice['id'])
    assert 'ACTIVE' in result.text
    member=core.membership('tester-01'); calls=core.rpc.call_count
    assert member and core.effective_config('tester-01').max_estimated_credits_per_run==1200
    assert 'ACTIVE' in r.adapter.payments.access(123,'checkpayment',invoice['id']).text
    assert core.membership('tester-01')==member and core.rpc.call_count==calls
    events=[json.loads(e['details']) for e in r.store.summary()['events'] if e['event']=='payment']
    assert events[0]['payment_state']=='settled_reused'
    settled=next(e for e in events if e['payment_state']=='settled')
    assert settled['signature']==SIGNATURE and settled['confirmed_at'] and settled['entitlement_granted']
    assert settled['account_id']=='tester-01' and settled['invoice_id']==invoice['id']


@pytest.mark.parametrize('case',['under','over','destination','token','network','late','failed','unconfirmed','old','reference'])
def test_ambiguous_or_invalid_payment_never_grants(rig,case):
    r,_,_=rig; core=r.gateway.payments; invoice=session(r); tx=receipt(invoice)
    msg=tx['transaction']['message']
    if case in {'under','over'}:
        amount=invoice['lamports']+(-1 if case=='under' else 1)
        msg['instructions'][0]['data']=b58encode((2).to_bytes(4,'little')+amount.to_bytes(8,'little'))
    if case=='destination': msg['accountKeys'][1]=SOURCE
    if case=='token': msg['accountKeys'][2]='TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
    if case=='late': tx['blockTime']=invoice['expires']+1
    if case=='failed': tx['meta']['err']={'InstructionError':[0,'Custom']}
    if case=='unconfirmed': tx=None
    if case=='old': tx['blockTime']=invoice['created']-1
    if case=='reference': msg['instructions'][0]['accounts']=[0,1]
    setup_rpc(core,tx,'mainnet-beta' if case=='network' else 'devnet')
    result=r.adapter.payments.access(123,'checkpayment')
    assert 'Status: ACTIVE' not in result.text
    assert core.membership('tester-01') is None
    assert core.effective_config('tester-01').max_estimated_credits_per_run==650


@pytest.mark.parametrize('failure',[HTTPException(503,'private-provider-detail'),RuntimeError('private-provider-detail')])
def test_provider_failure_sanitized(rig,failure):
    r,_,_=rig; core=r.gateway.payments; session(r)
    core.rpc=Mock(side_effect=failure)
    result=r.adapter.payments.access(123,'checkpayment')
    assert 'temporarily unavailable' in result.text and 'Do not pay again' in result.text
    assert 'private-provider-detail' not in json.dumps(r.store.summary())+result.text
    assert core.membership('tester-01') is None


def test_other_account_session_no_lookup_no_details(rig):
    r,_,_=rig; core=r.gateway.payments
    other=core.create_invoice('other-account')
    result=r.adapter.payments.access(123,'checkpayment',other['id'])
    assert 'unavailable for this account' in result.text
    assert other['reference'] not in result.text+json.dumps(r.store.summary())
    core.rpc.assert_not_called()


def test_signature_cannot_redeem_twice_even_with_forged_provider_receipt(rig):
    r,_,_=rig; core=r.gateway.payments
    first=session(r); other=core.create_invoice('other-account')
    setup_rpc(core,receipt(first)); core.verify('tester-01',first['id'])
    setup_rpc(core,receipt(other))
    with pytest.raises(HTTPException) as error: core.verify('other-account',other['id'])
    assert error.value.status_code==409 and core.membership('other-account') is None


def test_expired_session_not_silently_replaced_and_timely_receipt_can_recover(rig):
    r,_,_=rig; core=r.gateway.payments; invoice=session(r)
    with core.connection() as db:
        db.execute('UPDATE sol_invoices SET created=?,expires=? WHERE id=?',(int(time.time())-120,int(time.time())-60,invoice['id']))
    result=r.adapter.payments.access(123)
    assert 'expired' in result.text and RECIPIENT not in result.text
    assert core.pending_invoice('tester-01')['id']==invoice['id']
    old=core.invoice_for_owner('tester-01',invoice['id'])
    setup_rpc(core,receipt(old))
    assert 'ACTIVE' in r.adapter.payments.access(123,'checkpayment').text


@pytest.mark.parametrize('disabled',['false','0','garbage'])
def test_flag_off_no_invoices_or_rpc(rig,monkeypatch,disabled):
    r,_,_=rig; core=r.gateway.payments
    monkeypatch.setenv('JEET_TELEGRAM_SOL_PAYMENTS_ENABLED',disabled)
    for command in ['access','pay','payment','checkpayment']:
        result=r.adapter.payments.access(123,command)
        assert RECIPIENT not in result.text and result.markup is None and result.invoice_id is None
    assert core.pending_invoice('tester-01') is None
    core.rpc.assert_not_called()


def test_disable_redacts_messages_but_preserves_entitlement_and_jobs(rig,monkeypatch,tmp_path):
    r,_,sender=rig; core=r.gateway.payments
    asyncio.run(r.adapter.handle(normalize_update(update('/access'),r.config.username)))
    assert RECIPIENT in sender.sent[-1][2] and r.store.payment_messages()
    invoice=core.pending_invoice('tester-01'); setup_rpc(core,receipt(invoice)); core.verify('tester-01',invoice['id'])
    stop=tmp_path/'PAYMENTS.STOP'; stop.touch()
    assert not payments_enabled(r.config)
    asyncio.run(r.adapter.hide_payment_messages())
    assert sender.edited[-1][3] is None and RECIPIENT not in sender.edited[-1][2]
    assert not r.store.payment_messages()
    assert 'ACTIVE' in r.adapter.payments.access(123).text
    record,_=r.gateway.investigate('tester-01',API_MINT,API_WALLET)
    assert r.gateway.status('tester-01',record['id'])
    assert core.membership('tester-01')
    assert core.invoice_for_owner('tester-01',invoice['id'])['signature']==SIGNATURE


def test_disabled_surface_linking_and_group_privacy(rig,monkeypatch):
    r,_,sender=rig
    monkeypatch.setenv('JEET_TELEGRAM_SOL_PAYMENTS_ENABLED','false')
    text=r.adapter.payments.access(456)
    assert 'confirm your Telegram numeric ID' in text.text and 'challenge=' in text.markup
    for command in ['access','pay','payment','checkpayment']:
        asyncio.run(r.adapter.handle(normalize_update(update('/'+command,cid=-999,kind='group'),r.config.username)))
        assert sender.sent[-1][3] is None and RECIPIENT not in sender.sent[-1][2]
    assert r.gateway.payments.pending_invoice('tester-01') is None


def test_callback_identity_and_replay_through_webhook(rig):
    r,client,sender=rig; core=r.gateway.payments; invoice=session(r)
    setup_rpc(core,receipt(invoice))
    source=update()['message']; source['from']={'id':999,'is_bot':True}
    value={'update_id':44,'callback_query':{'id':'callback-1','from':{'id':123,'is_bot':False},
        'message':source,'data':'checkpay:'+invoice['id']}}
    p=normalize_update(value,r.config.username)
    assert p['user_id']==123 and p['invoice_id']==invoice['id']
    headers={'X-Telegram-Bot-Api-Secret-Token':r.config.webhook_secret}
    assert client.post('/api/beta/telegram/webhook',json=value,headers=headers).json()['state']=='queued'
    assert client.post('/api/beta/telegram/webhook',json=value,headers=headers).json()['state']=='duplicate'
    deadline=time.time()+4
    while not core.membership('tester-01') and time.time()<deadline: time.sleep(.02)
    assert core.membership('tester-01')


@pytest.mark.parametrize('identifier',['../other','f'*31,'f'*33,'paid '+SIGNATURE])
def test_invalid_session_input_never_verified(rig,identifier):
    r,_,sender=rig
    p=normalize_update(update('/checkpayment '+identifier),r.config.username)
    assert p['invalid']
    asyncio.run(r.adapter.handle(p))
    r.gateway.payments.rpc.assert_not_called()


def test_disabled_help_has_no_payment_instructions(rig,monkeypatch):
    r,_,sender=rig
    monkeypatch.setenv('JEET_TELEGRAM_SOL_PAYMENTS_ENABLED','false')
    asyncio.run(r.adapter.handle(normalize_update(update('/help'),r.config.username)))
    assert '\n/pay' not in sender.sent[-1][2] and 'SOL access' not in sender.sent[-1][2]


def test_markup_is_real_check_button(rig):
    pytest.importorskip('telegram')
    from jeet_analyzer_api.telegram.runtime import BotSender
    r,_,_=rig
    result=r.adapter.payments.access(123)
    markup=BotSender.markup(result.markup)
    assert markup.inline_keyboard[0][0].text=='Check Payment'
    assert markup.inline_keyboard[0][0].callback_data=='checkpay:'+result.invoice_id


def test_redaction_failure_retained_for_operator_and_retry(rig,monkeypatch):
    from unittest.mock import AsyncMock
    r,_,sender=rig
    r.store.payment_message(123,42)
    monkeypatch.setenv('JEET_TELEGRAM_SOL_PAYMENTS_ENABLED','false')
    sender.edit=AsyncMock(side_effect=RuntimeError('delivery failed'))
    asyncio.run(r.adapter.hide_payment_messages())
    assert r.store.summary()['payment_messages_pending_redaction']==1
    asyncio.run(r.adapter.hide_payment_messages())
    assert sender.edit.call_count==1  # Retry is delayed sixty seconds, not a tight loop.


def test_unknown_and_pending_checks_do_not_create_or_repeat_rpc(rig):
    r,_,_=rig; core=r.gateway.payments
    assert 'No payment session' in r.adapter.payments.access(123,'checkpayment').text
    invoice=session(r); setup_rpc(core,None)
    assert 'No qualifying finalized' in r.adapter.payments.access(123,'checkpayment').text
    count=core.rpc.call_count
    assert 'wait' in r.adapter.payments.access(123,'checkpayment').text
    assert core.rpc.call_count==count and core.membership('tester-01') is None


def test_private_alpha_smoke_a_through_q(rig,monkeypatch,tmp_path):
    """Requested A-Q rehearsal, in-process, with fake receipts and no live funds."""
    from urllib.parse import urlparse,parse_qs
    from tests.test_beta_api import INVITE
    r,client,sender=rig; core=r.gateway.payments
    def send(text,reply=None):
        asyncio.run(r.adapter.handle(normalize_update(update(text,reply=reply),r.config.username)))
        return sender.sent[-1]
    assert '/access' in send('/start')[2]  # A
    assert '/checkpayment' in send('/help')[2]  # B
    r.store.unlink('tester-01')
    link=send('/access')[3]
    challenge=parse_qs(urlparse(link).query)['challenge'][0]
    assert client.post('/api/beta/session',json={'access_code':INVITE}).status_code==200
    assert client.post('/api/beta/telegram/link',data={'challenge':challenge},headers={'origin':'https://testserver'}).status_code==200  # C
    displayed=send('/access')
    assert 'PAYMENT REQUIRED' in displayed[2]  # D/E
    invoice=core.pending_invoice('tester-01')
    assert all(v in displayed[2] for v in [invoice['recipient'],invoice['amount_sol'],invoice['reference']])  # F
    setup_rpc(core,None)
    assert 'No qualifying finalized' in send('/checkpayment')[2]  # G
    with core.connection() as db: db.execute('UPDATE sol_invoices SET last_check=0 WHERE id=?',(invoice['id'],))
    setup_rpc(core,receipt(invoice))  # H: mock finalized devnet receipt, no transfer.
    assert 'ACTIVE' in send('/checkpayment')[2] and core.membership('tester-01')  # I
    assert 'unknown' in send('/jeet '+API_MINT)[2]  # J: honest lone-address fallback.
    assert API_MINT in send('/jeet',reply='Check this '+API_MINT)[2]  # K
    send('/jeet '+API_MINT+' '+API_WALLET)
    with r.store.db() as db: job=db.execute('SELECT job_id FROM telegram_watches LIMIT 1').fetchone()[0]
    deadline=time.time()+4
    while r.gateway.status('tester-01',job)['state']!='complete' and time.time()<deadline: time.sleep(.01)
    assert r.gateway.status('tester-01',job)['state']=='complete'  # L
    asyncio.run(r.adapter.update_jobs())
    assert client.get(r.gateway.report_url(job)).status_code==200
    client.delete('/api/beta/session')
    assert client.get(r.gateway.report_url(job)).status_code==401  # M
    limited=replace(r.config,user_per_minute=1)
    p=normalize_update(update('/help'),r.config.username)
    assert r.store.admit(500,p,limited)=='queued'
    assert r.store.admit(501,p,limited)=='rate_limited'  # N
    r.config.stop_file.touch()
    assert r.receive(update('/jeet '+API_MINT,identifier=600))=='paused'  # O
    r.config.stop_file.unlink()
    (tmp_path/'PAYMENTS.STOP').touch()  # P
    asyncio.run(r.adapter.hide_payment_messages())
    assert 'ACTIVE' in send('/access')[2] and RECIPIENT not in sender.sent[-1][2]
    assert not r.store.payment_messages() and r.gateway.status('tester-01',job)['state']=='complete'  # Q
