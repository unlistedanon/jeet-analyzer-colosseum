"""Transport state in the existing beta database; no payment or quota ledger."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import secrets
import sqlite3
import time


class TelegramStore:
    def __init__(self, path, secret):
        self.path, self.secret = path, secret
        with self.db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS telegram_links(user_hash TEXT PRIMARY KEY, code_id TEXT UNIQUE NOT NULL);
              CREATE TABLE IF NOT EXISTS telegram_pairing(digest TEXT PRIMARY KEY, user_hash TEXT NOT NULL, user_id INTEGER NOT NULL, expires INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS telegram_updates(id INTEGER PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL, at INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS telegram_events(id INTEGER PRIMARY KEY, user_hash TEXT, chat_hash TEXT, event TEXT NOT NULL, details TEXT NOT NULL, at INTEGER NOT NULL);
              CREATE INDEX IF NOT EXISTS telegram_events_at ON telegram_events(at);
              CREATE TABLE IF NOT EXISTS telegram_choices(user_hash TEXT, chat_hash TEXT, addresses TEXT NOT NULL, expires INTEGER NOT NULL, PRIMARY KEY(user_hash,chat_hash));
              CREATE TABLE IF NOT EXISTS telegram_watches(job_id TEXT, user_hash TEXT, code_id TEXT NOT NULL, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, state TEXT NOT NULL, last_status TEXT, started INTEGER NOT NULL, PRIMARY KEY(job_id,user_hash,chat_id,message_id));
              CREATE TABLE IF NOT EXISTS telegram_payment_messages(chat_id INTEGER, message_id INTEGER, state TEXT NOT NULL DEFAULT 'visible', next_attempt INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(chat_id,message_id));
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def pseudonym(self, kind, identifier):
        return hmac.new(self.secret.encode(), f'telegram:{kind}:{identifier}'.encode(), hashlib.sha256).hexdigest()

    def linked(self, user_id):
        with self.db() as db:
            row = db.execute('SELECT code_id FROM telegram_links WHERE user_hash=?', (self.pseudonym('user',user_id),)).fetchone()
        return row[0] if row else None

    def pairing(self, user_id):
        token = secrets.token_urlsafe(32)
        user_hash = self.pseudonym('user',user_id)
        with self.db() as db:
            db.execute('DELETE FROM telegram_pairing WHERE user_hash=? OR expires<=?', (user_hash,int(time.time())))
            db.execute('INSERT INTO telegram_pairing VALUES(?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(),user_hash,user_id,int(time.time())+300))
        return token

    def pair_info(self, token):
        if not isinstance(token,str) or len(token)!=43:
            return None
        with self.db() as db:
            row=db.execute('SELECT * FROM telegram_pairing WHERE digest=? AND expires>?', (hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
        return dict(row) if row else None

    def confirm_pair(self, token, code_id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM telegram_pairing WHERE digest=? AND expires>?', (hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
            if not row:
                raise ValueError('Pairing request expired or already used')
            try:
                db.execute('INSERT INTO telegram_links VALUES(?,?)', (row['user_hash'],code_id))
            except sqlite3.IntegrityError:
                raise ValueError('An account is already linked; unlink from the website first') from None
            db.execute('DELETE FROM telegram_pairing WHERE digest=?',(row['digest'],))
            db.execute("INSERT INTO telegram_events(user_hash,event,details,at) VALUES(?,'account_linked','{}',?)",(row['user_hash'],int(time.time())))

    def unlink(self, code_id):
        with self.db() as db:
            db.execute('DELETE FROM telegram_links WHERE code_id=?',(code_id,))
            db.execute("UPDATE telegram_watches SET state='revoked' WHERE code_id=?",(code_id,))

    def event(self, user_id, chat_id, event, **details):
        allowed={k:v for k,v in details.items() if k in {'target','classification','job_id','status','estimated_credits','chat_type','reason','account_id','invoice_id','amount_required','recipient','signature','payment_state','confirmed_at','entitlement_granted','payment_flag'}}
        with self.db() as db:
            db.execute('INSERT INTO telegram_events(user_hash,chat_hash,event,details,at) VALUES(?,?,?,?,?)',
                       (self.pseudonym('user',user_id),self.pseudonym('chat',chat_id),event,json.dumps(allowed),int(time.time())))

    def admit(self, update_id, payload, config):
        now=int(time.time()); user=self.pseudonym('user',payload['user_id']); chat=self.pseudonym('chat',payload['chat_id'])
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM telegram_updates WHERE id=?',(update_id,)).fetchone():
                return 'duplicate'
            db.execute("DELETE FROM telegram_updates WHERE at<? AND state NOT IN ('queued','processing')",(now-7*86400,))
            db.execute("DELETE FROM telegram_events WHERE at<? AND event!='payment'",(now-7*86400,))
            db.execute("DELETE FROM telegram_watches WHERE started<? AND state!='active'",(now-7*86400,))
            limits=[('user_hash=?',(user,),config.user_per_minute),('chat_hash=?',(chat,),config.chat_per_minute),('1=1',(),config.global_per_minute)]
            for where,args,limit in limits:
                count=db.execute(f"SELECT COUNT(*) FROM telegram_events WHERE event='admitted' AND at>? AND {where}",(now-60,*args)).fetchone()[0]
                if count>=limit:
                    # Aggregate rejected traffic instead of storing attacker-sized logs.
                    recent=db.execute("SELECT 1 FROM telegram_events WHERE event='rate_limited' AND at>? LIMIT 1",(now-60,)).fetchone()
                    if not recent:
                        db.execute("INSERT INTO telegram_events(user_hash,chat_hash,event,details,at) VALUES(?,?,'rate_limited','{}',?)",(user,chat,now))
                    return 'rate_limited'
            if db.execute("SELECT COUNT(*) FROM telegram_updates WHERE state='queued'").fetchone()[0]>=config.inbox_limit:
                return 'full'
            db.execute("INSERT INTO telegram_updates VALUES(?,'queued',?,?)",(update_id,json.dumps(payload),now))
            db.execute("INSERT INTO telegram_events(user_hash,chat_hash,event,details,at) VALUES(?,?,'admitted','{}',?)",(user,chat,now))
        return 'queued'

    def next_update(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM telegram_updates WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE telegram_updates SET state='processing' WHERE id=?",(row['id'],))
                return row['id'],json.loads(row['payload'])

    def finish(self, identifier, state='done'):
        with self.db() as db:
            db.execute("UPDATE telegram_updates SET state=?,payload='{}' WHERE id=?",(state,identifier))

    def recover(self):
        with self.db() as db:
            # Never resend a message whose delivery may already have happened.
            db.execute("UPDATE telegram_updates SET state='delivery_unknown',payload='{}' WHERE state='processing'")
            db.execute('DELETE FROM telegram_choices WHERE expires<=?',(int(time.time()),))

    def choices(self, user_id, chat_id, addresses=None):
        args=(self.pseudonym('user',user_id),self.pseudonym('chat',chat_id))
        with self.db() as db:
            if addresses is not None:
                db.execute('INSERT OR REPLACE INTO telegram_choices VALUES(?,?,?,?)',(*args,json.dumps(addresses),int(time.time())+300))
                return addresses
            row=db.execute('SELECT addresses FROM telegram_choices WHERE user_hash=? AND chat_hash=? AND expires>?',(*args,int(time.time()))).fetchone()
            return json.loads(row[0]) if row else []

    def watch(self, job_id, user_id, code_id, chat_id, message_id):
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO telegram_watches VALUES(?,?,?,?,?,'active',NULL,?)",(job_id,self.pseudonym('user',user_id),code_id,chat_id,message_id,int(time.time())))

    def watches(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM telegram_watches WHERE state='active' LIMIT 100")]

    def update_watch(self, w, status, state='active'):
        with self.db() as db:
            db.execute('UPDATE telegram_watches SET last_status=?,state=? WHERE job_id=? AND user_hash=? AND chat_id=? AND message_id=?', (status,state,w['job_id'],w['user_hash'],w['chat_id'],w['message_id']))

    def watch_authorized(self, w):
        with self.db() as db:
            return bool(db.execute('SELECT 1 FROM telegram_links WHERE user_hash=? AND code_id=?',(w['user_hash'],w['code_id'])).fetchone())

    def visible_job(self, job_id, user_id, chat_id):
        with self.db() as db:
            return bool(db.execute('SELECT 1 FROM telegram_watches WHERE job_id=? AND user_hash=? AND chat_id=?',(job_id,self.pseudonym('user',user_id),chat_id)).fetchone())

    def summary(self):
        with self.db() as db:
            return {'events':[dict(r) for r in db.execute('SELECT user_hash,chat_hash,event,details,at FROM telegram_events ORDER BY id DESC LIMIT 100')],
                    'payment_messages_pending_redaction':db.execute("SELECT COUNT(*) FROM telegram_payment_messages WHERE state='visible'").fetchone()[0],
                    'linked_accounts':db.execute('SELECT COUNT(*) FROM telegram_links').fetchone()[0],
                    'updates_by_state':{r[0]:r[1] for r in db.execute('SELECT state,COUNT(*) FROM telegram_updates GROUP BY state')}}

    def payment_message(self, chat_id, message_id):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO telegram_payment_messages(chat_id,message_id) VALUES(?,?)',(chat_id,message_id))

    def payment_messages(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM telegram_payment_messages WHERE state='visible' AND next_attempt<=? LIMIT 30",(int(time.time()),))]

    def payment_message_redacted(self, row, success):
        with self.db() as db:
            if success:
                db.execute('DELETE FROM telegram_payment_messages WHERE chat_id=? AND message_id=?',(row['chat_id'],row['message_id']))
            else:
                db.execute('UPDATE telegram_payment_messages SET next_attempt=? WHERE chat_id=? AND message_id=?',(int(time.time())+60,row['chat_id'],row['message_id']))
