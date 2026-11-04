"""The Assistant: everything MailMind asks Claude, and what it costs.

`application` holds the ports and the gateway every AI call goes through, `domain` what the gateway
knows about Claude's models and their prices, and `adapters` the ways to reach them: the Anthropic
SDK, a standard-library client for the old front door, and DisabledModel for when there is no key.
Only mailmind.bootstrap picks an adapter. Nothing here can change mail or send it.
"""
