"""The Assistant: everything MailMind asks Claude, and what it costs.

`application` holds the ports and the gateway every AI call goes through, `domain` what the gateway
knows about Claude's models and their prices, and `adapters` the way to reach them, the Anthropic
SDK, and DisabledModel for when there is no key.
Only mailmind.bootstrap picks an adapter. Nothing here can change mail or send it.
"""
