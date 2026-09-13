"""Explicit operator-only Bot API configuration. Never called by app startup."""
import argparse
import asyncio
import logging

from jeet_analyzer_api.telegram.config import TelegramConfig


async def configure(action):
    from telegram import Bot
    config=TelegramConfig.from_env()
    if not config.enabled:
        raise ValueError('Telegram must be explicitly enabled in this shell')
    config.validate()
    async with Bot(config.token) as bot:
        if bot.username.lower()!=config.username.lower():
            raise ValueError('Configured bot username does not match')
        if action=='set-webhook':
            if config.mode!='webhook': raise ValueError('Configure webhook mode first')
            await bot.set_webhook(config.public_base_url.rstrip('/')+'/api/beta/telegram/webhook',
                secret_token=config.webhook_secret,allowed_updates=['message','callback_query'],drop_pending_updates=False,
                max_connections=1)
        elif action=='delete-webhook':
            await bot.delete_webhook(drop_pending_updates=False)
        info=await bot.get_webhook_info()
        # No URL, error text, credentials or account content printed.
        print('WEBHOOK_CONFIGURED='+str(bool(info.url)))
        print('PENDING_UPDATES='+str(info.pending_update_count))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['status','set-webhook','delete-webhook'])
    args=parser.parse_args()
    logging.getLogger('httpx').setLevel(logging.CRITICAL)
    logging.getLogger('httpcore').setLevel(logging.CRITICAL)
    try:
        asyncio.run(configure(args.action))
    except Exception:
        print('Telegram configuration failed. Check private credentials, mode, username and network; no secrets were logged.')
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
