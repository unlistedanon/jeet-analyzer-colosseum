"""Optional python-telegram-bot transport, durable bounded inbox, no auto-deploy."""
import asyncio
import contextlib
import hmac
import json
import re
from html import escape
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .adapter import TelegramAdapter, normalize_update
from .gateway import JeetGateway
from .store import TelegramStore


class BotSender:
    def __init__(self, bot): self.bot=bot

    @staticmethod
    def markup(link):
        if not link: return None
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        if isinstance(link,dict):
            return InlineKeyboardMarkup([
                [InlineKeyboardButton('Check Payment',callback_data='checkpay:'+link['payment_check'])],
                [InlineKeyboardButton('Wallet link / QR',url=link['url'])],
            ])
        return InlineKeyboardMarkup([[InlineKeyboardButton('Open Jeet',url=link)]])

    async def answer_callback(self, identifier):
        await self.bot.answer_callback_query(identifier)

    async def send(self, chat_id, reply_to, text, link=None):
        from telegram import LinkPreviewOptions, ReplyParameters
        message=await self.bot.send_message(chat_id=chat_id,text=text,parse_mode='HTML',
                    reply_parameters=ReplyParameters(message_id=reply_to,allow_sending_without_reply=False),
                    link_preview_options=LinkPreviewOptions(is_disabled=True),reply_markup=self.markup(link))
        return message.message_id

    async def edit(self, chat_id, message_id, text, link=None):
        from telegram import LinkPreviewOptions
        await self.bot.edit_message_text(chat_id=chat_id,message_id=message_id,text=text,parse_mode='HTML',
                    link_preview_options=LinkPreviewOptions(is_disabled=True),reply_markup=self.markup(link))


class TelegramRuntime:
    def __init__(self, config, beta, jobs, payments, sender=None):
        config.validate()
        if not beta.configured:
            raise ValueError('Telegram requires configured Jeet authentication')
        if beta.public_origin and beta.public_origin.rstrip('/') != config.public_base_url.rstrip('/'):
            raise ValueError('Telegram links must use the configured Jeet public origin')
        self.config,self.beta=config,beta
        self.store=TelegramStore(beta.database_path,beta.session_secret)
        self.gateway=JeetGateway(config,beta,self.store,jobs,payments)
        self.sender=sender
        self.bot=None
        self.tasks=[]
        self.adapter=None
        self.ready=False

    def receive(self, update):
        if self.config.stop_file.exists(): return 'paused'
        try:
            payload=normalize_update(update,self.config.username)
        except (ValueError,TypeError,AttributeError):
            return 'ignored'
        if payload is None: return 'ignored'
        return self.store.admit(update['update_id'],payload,self.config)

    async def start(self):
        if self.sender is None:
            from telegram import Bot
            self.bot=Bot(self.config.token)
            try:
                await self.bot.initialize()
                if self.bot.username.lower()!=self.config.username.lower():
                    raise ValueError('Bot username does not match configured bot')
                info=await self.bot.get_webhook_info()
                if self.config.mode=='polling' and info.url:
                    raise ValueError('Polling requires the operator to remove the existing webhook first')
            except Exception:
                await self.bot.shutdown()
                raise RuntimeError('Telegram startup failed; check private configuration and webhook mode') from None
            self.sender=BotSender(self.bot)
        self.adapter=TelegramAdapter(self.config,self.store,self.gateway,self.sender)
        self.store.recover()
        self.ready=True
        self.tasks=[asyncio.create_task(self.work()),asyncio.create_task(self.maintenance())]
        if self.bot and self.config.mode=='polling':
            self.tasks.append(asyncio.create_task(self.poll()))
        for task in self.tasks:
            task.add_done_callback(self.worker_finished)

    def worker_finished(self, task):
        # Fail admission closed if a worker exits unexpectedly.
        if not task.cancelled():
            self.ready=False
            task.exception()

    async def stop(self):
        self.ready=False
        for task in self.tasks: task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError): await task
        if self.bot: await self.bot.shutdown()

    async def work(self):
        while True:
            if not self.config.stop_file.exists():
                row=self.store.next_update()
                if row:
                    identifier,payload=row
                    try:
                        await self.adapter.handle(payload)
                        self.store.finish(identifier)
                    except Exception:
                        self.store.finish(identifier,'delivery_unknown')
                        self.store.event(payload['user_id'],payload['chat_id'],'delivery_unknown')
            await asyncio.sleep(1)

    async def maintenance(self):
        while True:
            await self.adapter.hide_payment_messages()
            await self.adapter.update_jobs()
            await asyncio.sleep(1)

    async def poll(self):
        offset=None
        while True:
            if self.config.stop_file.exists():
                await asyncio.sleep(2); continue
            try:
                updates=await self.bot.get_updates(offset=offset,timeout=20,limit=20,allowed_updates=['message','callback_query'])
                for update in updates:
                    decision=self.receive(update.to_dict())
                    if decision=='full': break
                    offset=update.update_id+1
            except Exception:
                # Never log a Bot API URL, token or Telegram message contents.
                await asyncio.sleep(5)

    def router(self, beta_subject, admin_subject):
        router=APIRouter(prefix='/api/beta/telegram')

        @router.post('/webhook')
        async def webhook(request:Request):
            if self.config.mode!='webhook': raise HTTPException(404,'Not found')
            supplied=request.headers.get('X-Telegram-Bot-Api-Secret-Token','')
            if not supplied.isascii() or not hmac.compare_digest(supplied,self.config.webhook_secret): raise HTTPException(403,'Invalid webhook secret')
            if not self.ready: raise HTTPException(503,'Telegram worker unavailable')
            try: body=await request.json()
            except (ValueError,UnicodeDecodeError): raise HTTPException(400,'Invalid update') from None
            state=self.receive(body)
            if state=='full': raise HTTPException(503,'Inbox full')
            return {'accepted':True,'state':state}

        @router.get('/link',response_class=HTMLResponse)
        def link_page(request:Request,challenge:str):
            beta_subject(request)
            info=self.store.pair_info(challenge)
            if not info: raise HTTPException(410,'Link expired or already used')
            return HTMLResponse('<h1>Link Telegram to Jeet</h1><p>Only confirm if this is your Telegram numeric user ID: <strong>'+str(info['user_id'])+'</strong>.</p><p>This shares your existing Jeet access with that Telegram identity.</p><form method="post"><input type="hidden" name="challenge" value="'+escape(challenge,quote=True)+'"><button>Confirm account link</button></form>',headers={'Cache-Control':'no-store'})

        @router.post('/link',response_class=HTMLResponse)
        async def confirm_link(request:Request):
            owner=beta_subject(request)
            if request.headers.get('origin','').rstrip('/')!=self.config.public_base_url.rstrip('/'):
                raise HTTPException(403,'Same-origin confirmation required')
            form=parse_qs((await request.body()).decode('utf-8',errors='replace'))
            token=form.get('challenge',[''])[0]
            if not re.fullmatch('[A-Za-z0-9_-]{43}',token): raise HTTPException(422,'Invalid challenge')
            try: self.store.confirm_pair(token,owner)
            except ValueError as exc: raise HTTPException(409,str(exc)) from None
            return HTMLResponse('<h1>Telegram linked</h1><p>Return to the bot and send /access.</p>',headers={'Cache-Control':'no-store'})

        @router.delete('/link')
        def unlink(request:Request):
            owner=beta_subject(request)
            if request.headers.get('origin','').rstrip('/')!=self.config.public_base_url.rstrip('/'):
                raise HTTPException(403,'Same-origin confirmation required')
            self.store.unlink(owner)
            return {'unlinked':True}

        @router.get('/admin')
        def audit(request:Request):
            admin_subject(request)
            return self.store.summary()
        return router
