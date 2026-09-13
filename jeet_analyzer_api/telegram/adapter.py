"""Telegram parsing/routing only. The application gateway owns access and jobs."""
import re
import time
import asyncio
from html import escape

from jeet_analyzer.targeting import extract_addresses
from .formatting import format_result
from .payments import TelegramSolSurface, payments_enabled

COMMANDS={'jeet','scan','wallet','status','access','pay','payment','checkpayment','help','start'}
HELP=('Reply /jeet to a message containing an address, or send /jeet ADDRESS.\n'
      '/scan MINT — existing token seller scan\n/wallet WALLET MINT — existing bounded wallet/cluster investigation\n'
      '/jeet MINT WALLET — explicit pair investigation\n/status JOB-ID\n/access — account access in DM\n'
      'A lone wallet/payment address does not yet support a general fund-flow investigation. No scam verdicts.')


def normalize_update(update, username):
    """Retain only routing fields and validated targets, never message bodies."""
    if not isinstance(update,dict) or type(update.get('update_id')) is not int or update['update_id']<0:
        return None
    callback_id=None
    callback=update.get('callback_query')
    m=update.get('message')
    if isinstance(callback,dict):
        data=callback.get('data','')
        if not isinstance(data,str) or not re.fullmatch(r'checkpay:[0-9a-f]{32}',data): return None
        callback_id=callback.get('id')
        if not isinstance(callback_id,str) or not 1<=len(callback_id)<=128: return None
        source=callback.get('message')
        if not isinstance(source,dict): return None
        m={**source,'from':callback.get('from'),'text':'/checkpayment '+data.split(':')[1],'date':int(time.time())}
    if not isinstance(m,dict):
        return None
    sent_at=m.get('date')
    if type(sent_at) is not int or not time.time()-86400 <= sent_at <= time.time()+300:
        return None
    sender=m.get('from') or {}; chat=m.get('chat') or {}
    uid=sender.get('id'); cid=chat.get('id'); mid=m.get('message_id')
    if sender.get('is_bot') or m.get('sender_chat') or type(uid) is not int or not 0<uid<2**52 or type(cid) is not int or not 0<abs(cid)<2**52 or type(mid) is not int or not 0<mid<2**52 or update['update_id']>=2**52:
        return None
    kind=chat.get('type')
    if kind not in {'private','group','supergroup'} or (kind=='private' and cid!=uid):
        return None
    text=m.get('text',m.get('caption',''))
    if not isinstance(text,str) or len(text)>8192:
        return None
    command=re.match(r'^/([a-z]+)(?:@([A-Za-z0-9_]+))?(?:\s+(.*))?$',text.strip(),re.S)
    args=''; name='jeet'
    if command:
        name,mention,args=command.groups(); args=(args or '').strip()
        if name not in COMMANDS or (mention and mention.lower()!=username.lower()):
            return None
    elif kind!='private' or not extract_addresses(text) or text.strip() not in extract_addresses(text):
        return None
    else:
        args=text.strip()
    payload={'user_id':uid,'chat_id':cid,'message_id':mid,'chat_type':kind,'command':name,'addresses':[]}
    if callback_id: payload['callback_id']=callback_id
    if name=='checkpayment':
        if args:
            if re.fullmatch(r'[0-9a-f]{32}',args): payload['invoice_id']=args
            else: payload['invalid']=True
        return payload
    if name=='status':
        if re.fullmatch(r'[0-9a-fA-F-]{36}',args): payload['job_id']=args.lower()
        return payload
    if name in {'access','pay','payment','help','start'}:
        return payload
    if name=='jeet' and re.fullmatch(r'[1-8]',args):
        payload['selection']=int(args); return payload
    target_text=args
    if not args and name=='jeet':
        reply=m.get('reply_to_message') or {}
        target_text=reply.get('text',reply.get('caption',''))
    try:
        payload['addresses']=extract_addresses(target_text)
    except (ValueError,TypeError):
        payload['invalid']=True
    # An explicit pair is accepted only as two bare addresses, never inferred
    # from two unrelated destinations found in a replied-to message.
    payload['explicit_pair']=len(args.split())==2 and args.split()==payload['addresses']
    return payload


class TelegramAdapter:
    def __init__(self, config, store, gateway, sender):
        self.config,self.store,self.gateway,self.sender=config,store,gateway,sender
        self.payments=TelegramSolSurface(gateway,store,config)

    async def reply(self,p,text,link=None):
        return await self.sender.send(p['chat_id'],p['message_id'],text,link)

    async def handle(self,p):
        if self.config.stop_file.exists():
            return
        command=p['command']; uid=p['user_id']; cid=p['chat_id']
        if p.get('callback_id') and hasattr(self.sender,'answer_callback'):
            try: await self.sender.answer_callback(p['callback_id'])
            except Exception: pass  # An expired spinner cannot authorize or reject payment.
        if command in {'help','start'}:
            help_text=HELP
            if p['chat_type']=='private' and payments_enabled(self.config):
                help_text+='\n/pay, /payment — SOL access\n/checkpayment — verify your existing payment session'
            await self.reply(p,escape(help_text)); return
        if command in {'access','pay','payment','checkpayment'}:
            if p['chat_type']!='private':
                await self.reply(p,'Open a private chat with this bot and send /access. Account details are private.'); return
            if p.get('invalid'):
                await self.reply(p,'Invalid payment session. Use /checkpayment without arguments to check your own session.'); return
            result=await asyncio.to_thread(self.payments.access,uid,command,p.get('invoice_id'))
            # Recheck link and flag after slow RPC. Never send a stale account response.
            if result.owner and self.store.linked(uid)!=result.owner:
                await self.reply(p,'Account link changed. Send /access again.'); return
            if result.invoice_id and not payments_enabled(self.config):
                await self.reply(p,'SOL payment controls are disabled here. Existing Jeet access is unchanged.'); return
            mid=await self.reply(p,escape(result.text),result.markup)
            if result.invoice_id:
                self.store.payment_message(cid,mid)
            return
        try:
            owner=self.gateway.authorize(uid)
        except PermissionError:
            self.store.event(uid,cid,'access_denied',chat_type=p['chat_type'])
            await self.reply(p,'Link your Jeet account first: send /access in a private chat with this bot.'); return
        if command=='status':
            job=p.get('job_id')
            record=self.gateway.status(owner,job) if job else None
            if record is None or (p['chat_type']!='private' and not self.store.visible_job(job,uid,cid)):
                await self.reply(p,'Job unavailable here. Use /status JOB-ID in your private chat.'); return
            await self.reply(p,format_result(record),self.gateway.report_url(job)); return
        addresses=p['addresses']
        if 'selection' in p:
            choices=self.store.choices(uid,cid)
            index=p['selection']-1
            addresses=choices[index:index+1]
        if not addresses:
            await self.reply(p,'No usable Solana address found. Reply /jeet to an address, or send /jeet ADDRESS.'); return
        if len(addresses)>1 and not (p.get('explicit_pair') and command in {'jeet','wallet'}):
            self.store.choices(uid,cid,addresses)
            await self.reply(p,'Choose a target within 5 minutes using /jeet NUMBER:\n'+'\n'.join(f'{n}. <code>{a}</code>' for n,a in enumerate(addresses,1))); return
        try:
            if command=='scan' and len(addresses)==1:
                record,duplicate=self.gateway.scan(owner,addresses[0])
            elif p.get('explicit_pair') and len(addresses)==2:
                mint,wallet=addresses if command=='jeet' else addresses[::-1]
                record,duplicate=self.gateway.investigate(owner,mint,wallet)
            else:
                target=self.gateway.classify(addresses[0])
                self.store.event(uid,cid,'context_required',target=target.address,classification=target.kind)
                await self.reply(p,f'Target: <code>{target.address}</code>\nType: {escape(target.kind)}\n{escape(target.limitation)}\nFor a token use /scan MINT. For a wallet use /wallet WALLET MINT.'); return
        except Exception as exc:
            # Do not transport provider exceptions or account/payment internals.
            reason=getattr(exc,'kind','UNAVAILABLE')
            self.store.event(uid,cid,'job_rejected',reason=reason if re.fullmatch('[A-Z_]{1,80}',str(reason)) else 'UNAVAILABLE')
            await self.reply(p,'Jeet cannot admit this investigation now. Access, pause state, provider readiness, or budget limits may apply. No new conclusion was made.'); return
        self.store.event(uid,cid,'job_admitted',job_id=record['id'],target=addresses[0],estimated_credits=record.get('estimated_credits_reserved',0),chat_type=p['chat_type'])
        mid=await self.reply(p,format_result(record)+('\nReused the active job; not a fresh scan.' if duplicate else ''))
        self.store.watch(record['id'],uid,owner,cid,mid)

    async def update_jobs(self):
        if self.config.stop_file.exists(): return
        import time
        for w in self.store.watches():
            if not self.store.watch_authorized(w) or not self.gateway.valid_owner(w['code_id']):
                self.store.update_watch(w,'revoked','revoked'); continue
            record=self.gateway.status(w['code_id'],w['job_id'])
            if record is None:
                self.store.update_watch(w,'missing','missing'); continue
            timed_out=time.time()-w['started']>self.config.job_timeout_seconds and record['state'] in {'queued','running'}
            status='timeout' if timed_out else record['state']
            if status==w['last_status']: continue
            text=('Still running beyond Telegram’s update window. Use /status '+w['job_id']+'. The engine remains subject to its own budgets.' if timed_out else format_result(record))
            try:
                await self.sender.edit(w['chat_id'],w['message_id'],text,self.gateway.report_url(w['job_id']) if record['state']=='complete' else None)
                self.store.update_watch(w,status,'done' if timed_out or status not in {'queued','running'} else 'active')
            except Exception:
                # Edits are idempotent, but a blocked/deleted destination should
                # not become an unbounded retry loop. /status remains available.
                self.store.update_watch(w,status,'delivery_failed')

    async def hide_payment_messages(self):
        if payments_enabled(self.config): return
        for row in self.store.payment_messages():
            try:
                await self.sender.edit(row['chat_id'],row['message_id'],
                    'SOL payment controls are disabled here. Existing Jeet access is unchanged.',None)
                self.store.payment_message_redacted(row,True)
            except Exception:
                self.store.payment_message_redacted(row,False)
