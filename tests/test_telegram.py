"""Offline transport and real shared-ledger integration tests; no Telegram sends."""
import asyncio
from dataclasses import replace
import json
import time

import pytest
from fastapi.testclient import TestClient

from jeet_analyzer.targeting import extract_addresses, classify_target, TOKEN_PROGRAMS, SYSTEM_PROGRAM
from jeet_analyzer_api.app import create_app
from jeet_analyzer_api.telegram.adapter import normalize_update, TelegramAdapter
from jeet_analyzer_api.telegram.config import TelegramConfig
from jeet_analyzer_api.telegram.formatting import format_result
from jeet_analyzer_api.telegram.store import TelegramStore
from tests.test_beta_api import beta_config, FakeBetaAdapter, INVITE
from tests.test_ui_api import API_MINT, API_WALLET


def config(tmp_path, **changes):
    return replace(TelegramConfig(enabled=True, token='12345:'+'x'*32,
        webhook_secret='s'*40, username='jeet_test_bot', public_base_url='https://testserver',
        stop_file=tmp_path/'STOP'), **changes)


def update(text='/help', uid=123, cid=None, kind='private', reply=None, identifier=1):
    message={'message_id':42,'date':int(time.time()),'from':{'id':uid,'is_bot':False},
             'chat':{'id':uid if cid is None else cid,'type':kind},'text':text}
    if reply is not None: message['reply_to_message']={'text':reply}
    return {'update_id':identifier,'message':message}


class Sender:
    def __init__(self): self.sent=[]; self.edited=[]
    async def send(self,*args): self.sent.append(args); return len(self.sent)+100
    async def edit(self,*args): self.edited.append(args)


@pytest.mark.parametrize('text', [f'/jeet {API_MINT}', API_MINT, f'/scan {API_MINT}', f'/wallet {API_MINT}'])
def test_direct_address(text):
    assert normalize_update(update(text),'jeet_test_bot')['addresses']==[API_MINT]


def test_reply_dedup_and_no_body_retained():
    payload=normalize_update(update('/jeet',reply=f'PRIVATE CLAIM {API_MINT} then {API_MINT}'),'jeet_test_bot')
    assert payload['addresses']==[API_MINT]
    assert 'PRIVATE CLAIM' not in json.dumps(payload)


@pytest.mark.parametrize('mutation', ['bot','anonymous','edited','channel','other_bot','big_id','private_mismatch'])
def test_untrusted_updates_ignored(mutation):
    value=update()
    if mutation=='bot': value['message']['from']['is_bot']=True
    if mutation=='anonymous': value['message']['sender_chat']={'id':-1}
    if mutation=='edited': value['edited_message']=value.pop('message')
    if mutation=='channel': value['message']['chat']['type']='channel'
    if mutation=='other_bot': value['message']['text']='/help@different_bot'
    if mutation=='big_id': value['message']['from']['id']=2**80
    if mutation=='private_mismatch': value['message']['chat']['id']=456
    assert normalize_update(value,'jeet_test_bot') is None


def test_group_only_explicit_commands():
    assert normalize_update(update(API_MINT,cid=-123,kind='group'),'jeet_test_bot') is None
    assert normalize_update(update('/jeet',cid=-123,kind='group',reply=API_MINT),'jeet_test_bot')['addresses']==[API_MINT]


def test_multi_reply_never_infers_pair():
    p=normalize_update(update('/jeet',reply=f'{API_MINT} {API_WALLET}'),'jeet_test_bot')
    assert len(p['addresses'])==2 and not p['explicit_pair']
    assert normalize_update(update(f'/jeet {API_MINT} {API_WALLET}'),'jeet_test_bot')['explicit_pair']


def test_extraction_limits():
    assert extract_addresses('0'*44)==[]
    with pytest.raises(ValueError): extract_addresses('x'*8193)
    with pytest.raises(ValueError): extract_addresses(f'{API_MINT} {API_WALLET}',limit=1)


@pytest.mark.parametrize('parsed,expected',[('mint','mint'),('account','token_account'),('other','unknown')])
def test_account_classification(parsed,expected):
    account={'owner':next(iter(TOKEN_PROGRAMS)), 'executable':False, 'data':{'parsed':{'type':parsed}}}
    assert classify_target(API_MINT,account,observed_at='2026-09-12T00:00:00Z').kind==expected
    assert classify_target(API_MINT,account).kind=='unknown'


def test_unknown_and_wallet_do_not_prove_ownership():
    assert classify_target(API_MINT).kind=='unknown'
    c=classify_target(API_WALLET,{'owner':SYSTEM_PROGRAM,'space':0,'executable':False},observed_at='now')
    assert c.kind=='wallet_account' and 'NOT_PROVEN' in c.limitation


def test_pairing_single_use_conflicts_and_unlink(tmp_path):
    s=TelegramStore(tmp_path/'db','s'*40)
    token=s.pairing(123)
    assert s.pair_info(token)['user_id']==123
    with s.db() as db:
        assert token not in str([tuple(r) for r in db.execute('SELECT * FROM telegram_pairing')])
    s.confirm_pair(token,'owner')
    assert s.linked(123)=='owner'
    with pytest.raises(ValueError): s.confirm_pair(token,'attacker')
    with pytest.raises(ValueError): s.confirm_pair(s.pairing(456),'owner')
    s.watch('job',123,'owner',123,1)
    s.unlink('owner')
    assert s.linked(123) is None and not s.watches()


def test_pair_expiry(tmp_path):
    s=TelegramStore(tmp_path/'db','s'*40); token=s.pairing(123)
    with s.db() as db: db.execute('UPDATE telegram_pairing SET expires=0')
    assert s.pair_info(token) is None
    with pytest.raises(ValueError): s.confirm_pair(token,'owner')


@pytest.mark.parametrize('limit,second', [('user_per_minute',update(identifier=2)),
    ('chat_per_minute',update(uid=456,cid=-123,kind='group',identifier=2)),
    ('global_per_minute',update(uid=456,identifier=2))])
def test_atomic_rate_limits(tmp_path,limit,second):
    s=TelegramStore(tmp_path/'db','s'*40); c=config(tmp_path,**{limit:1})
    first=update(cid=-123,kind='group') if limit=='chat_per_minute' else update()
    assert s.admit(1,normalize_update(first,c.username),c)=='queued'
    assert s.admit(1,normalize_update(first,c.username),c)=='duplicate'
    assert s.admit(2,normalize_update(second,c.username),c)=='rate_limited'


def test_inbox_recovery_and_choices_isolation(tmp_path):
    s=TelegramStore(tmp_path/'db','s'*40); c=config(tmp_path,inbox_limit=1)
    p=normalize_update(update(),c.username)
    assert s.admit(1,p,c)=='queued' and s.admit(2,p,c)=='full'
    assert s.next_update()[0]==1
    s.recover()
    assert s.next_update() is None
    with s.db() as db:
        r=db.execute('SELECT state,payload FROM telegram_updates').fetchone()
        assert tuple(r)==('delivery_unknown','{}')
    s.choices(123,-1,[API_MINT,API_WALLET])
    assert len(s.choices(123,-1))==2
    assert s.choices(456,-1)==[] and s.choices(123,-2)==[]


def test_format_does_not_echo_untrusted_results():
    r={'id':'job','state':'complete','updated_at':'now','result':{'error':'SECRET'},
       'safe_result':{'verdict':{'historical_exit_status':'<b>SCAMMER</b>'}}}
    text=format_result(r)
    assert 'SECRET' not in text and 'SCAMMER' not in text and 'NOT_PROVEN' in text


def test_disabled_requires_no_telegram_configuration(tmp_path,monkeypatch):
    monkeypatch.setenv('JEET_TELEGRAM_ENABLED','0')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','invalid')
    with TestClient(create_app(engine_adapter=FakeBetaAdapter(),beta_config=beta_config(tmp_path))) as client:
        assert client.post('/api/beta/telegram/webhook',json={}).status_code==404


def test_webhook_link_and_shared_job_lifecycle(tmp_path,monkeypatch):
    monkeypatch.setenv('JEET_TELEGRAM_PROVIDER_READY','1')
    c=config(tmp_path); sender=Sender(); engine=FakeBetaAdapter()
    app=create_app(engine_adapter=engine,beta_config=beta_config(tmp_path),telegram_config=c,telegram_sender=sender)
    with TestClient(app,base_url='https://testserver') as client:
        runtime=app.state.telegram
        assert client.post('/api/beta/telegram/webhook',json=update()).status_code==403
        assert client.get('/api/beta/telegram/admin').status_code==401
        token=runtime.store.pairing(123)
        assert client.get('/api/beta/telegram/link',params={'challenge':token}).status_code==401
        assert client.post('/api/beta/session',json={'access_code':INVITE}).status_code==200
        assert client.get('/api/beta/telegram/link',params={'challenge':token}).status_code==200
        assert client.post('/api/beta/telegram/link',data={'challenge':token}).status_code==403
        headers={'origin':'https://testserver'}
        assert client.post('/api/beta/telegram/link',data={'challenge':token},headers=headers).status_code==200
        assert client.post('/api/beta/telegram/link',data={'challenge':token},headers=headers).status_code==409
        payload=update(f'/jeet {API_MINT} {API_WALLET}')
        wh={'X-Telegram-Bot-Api-Secret-Token':c.webhook_secret}
        assert client.post('/api/beta/telegram/webhook',json=payload,headers=wh).json()['state']=='queued'
        assert client.post('/api/beta/telegram/webhook',json=payload,headers=wh).json()['state']=='duplicate'
        deadline=time.time()+5
        while not any('NOT_PROVEN' in item[2] for item in sender.edited) and time.time()<deadline: time.sleep(.05)
        assert len(engine.calls)==1 and sender.sent and sender.edited
        assert 'NOT_PROVEN' in sender.edited[-1][2]
        with runtime.store.db() as db:
            job=db.execute('SELECT job_id FROM telegram_watches').fetchone()[0]
        assert runtime.gateway.status('wrong-owner',job) is None
        report=runtime.gateway.report_url(job)
        assert client.get(report).status_code==200
        client.delete('/api/beta/session')
        assert client.get(report).status_code==401
        c.stop_file.touch()
        assert runtime.receive(update(identifier=2))=='paused'


def test_provider_latch_and_scan_use_shared_budget(tmp_path,monkeypatch):
    c=config(tmp_path); sender=Sender(); engine=FakeBetaAdapter()
    app=create_app(engine_adapter=engine,beta_config=beta_config(tmp_path,max_runs_per_code_per_day=1),telegram_config=c,telegram_sender=sender)
    with TestClient(app):
        g=app.state.telegram.gateway
        monkeypatch.setenv('JEET_TELEGRAM_PROVIDER_READY','0')
        with pytest.raises(Exception,match='Provider admission'): g.scan('tester-01',API_MINT)
        monkeypatch.setenv('JEET_TELEGRAM_PROVIDER_READY','1')
        record,_=g.scan('tester-01',API_MINT)
        deadline=time.time()+3
        while g.status('tester-01',record['id'])['state'] not in {'complete','failed'} and time.time()<deadline: time.sleep(.01)
        assert engine.calls[0][0].value=='seller-scan'
        with pytest.raises(Exception): g.investigate('tester-01',API_MINT,API_WALLET)


def test_group_access_and_selection_no_job_for_unknown(tmp_path):
    c=config(tmp_path); sender=Sender()
    app=create_app(engine_adapter=FakeBetaAdapter(),beta_config=beta_config(tmp_path),telegram_config=c,telegram_sender=sender)
    with TestClient(app):
        r=app.state.telegram; r.store.confirm_pair(r.store.pairing(123),'tester-01')
        asyncio.run(r.adapter.handle(normalize_update(update('/access',cid=-1,kind='group'),c.username)))
        assert sender.sent[-1][3] is None and 'private' in sender.sent[-1][2]
        asyncio.run(r.adapter.handle(normalize_update(update('/jeet',reply=f'{API_MINT} {API_WALLET}'),c.username)))
        assert 'Choose a target' in sender.sent[-1][2]
        asyncio.run(r.adapter.handle(normalize_update(update('/jeet 2'),c.username)))
        assert 'unknown' in sender.sent[-1][2] and API_WALLET in sender.sent[-1][2]


def test_pending_and_settled_access_use_same_payment_ledger(tmp_path):
    from jeet_analyzer_api.sol_payments import SolPayments, PaymentConfig
    from tests.test_sol_payments import RECIPIENT, receipt, mock_rpc, SIGNATURE
    c=config(tmp_path)
    app=create_app(engine_adapter=FakeBetaAdapter(),beta_config=beta_config(tmp_path),telegram_config=c,telegram_sender=Sender())
    with TestClient(app):
        r=app.state.telegram
        payments=SolPayments(r.beta,PaymentConfig(enabled=True,recipient=RECIPIENT,price_lamports=10000000,days=30,credits=1200))
        r.gateway.payments=payments
        r.gateway.jobs.member_config=payments.effective_config
        r.store.confirm_pair(r.store.pairing(123),'tester-01')
        invoice=payments.create_invoice('tester-01')
        assert 'awaiting finalized' in r.gateway.access(123)[0]
        assert payments.effective_config('tester-01').max_estimated_credits_per_run==650
        # Text claiming payment has no effect and is discarded by normalization.
        p=normalize_update(update('/access paid '+SIGNATURE),c.username)
        assert SIGNATURE not in json.dumps(p) and payments.membership('tester-01') is None
        mock_rpc(payments,receipt(invoice))
        payments.verify('tester-01',invoice['id'],SIGNATURE)
        assert 'Membership active' in r.gateway.access(123)[0]
        assert payments.effective_config('tester-01').max_estimated_credits_per_run==1200
        with payments.connection() as db: db.execute('UPDATE sol_memberships SET expires=0')
        assert 'no settled membership' in r.gateway.access(123)[0]
        assert payments.effective_config('tester-01').max_estimated_credits_per_run==650


def test_old_update_cannot_replay_after_retention():
    value=update(); value['message']['date']=int(time.time())-86401
    assert normalize_update(value,'jeet_test_bot') is None


def test_real_library_sender_serialization():
    pytest.importorskip('telegram')
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    from jeet_analyzer_api.telegram.runtime import BotSender
    bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=77)),edit_message_text=AsyncMock())
    sender=BotSender(bot)
    assert asyncio.run(sender.send(-123,42,'<b>result</b>','https://jeet.example/'))==77
    kwargs=bot.send_message.call_args.kwargs
    assert kwargs['reply_parameters'].message_id==42
    assert not kwargs['reply_parameters'].allow_sending_without_reply
    assert kwargs['link_preview_options'].is_disabled
    assert kwargs['reply_markup'].inline_keyboard[0][0].url=='https://jeet.example/'
    asyncio.run(sender.edit(-123,77,'updated'))
    assert bot.edit_message_text.call_args.kwargs['message_id']==77


def test_timeout_and_unlink_stop_delivery(tmp_path):
    c=config(tmp_path,job_timeout_seconds=1); sender=Sender()
    s=TelegramStore(tmp_path/'db','s'*40)
    from types import SimpleNamespace
    s.confirm_pair(s.pairing(123),'owner')
    s.watch('job',123,'owner',123,77)
    with s.db() as db: db.execute('UPDATE telegram_watches SET started=0')
    gateway=SimpleNamespace(valid_owner=lambda owner:True,status=lambda *args:{'state':'running'},report_url=lambda job:'https://jeet.example/')
    adapter=TelegramAdapter(c,s,gateway,sender)
    asyncio.run(adapter.update_jobs())
    assert 'update window' in sender.edited[0][2] and not s.watches()
    s.watch('job2',123,'owner',123,78); s.unlink('owner')
    asyncio.run(adapter.update_jobs())
    assert len(sender.edited)==1
