"""What every part of the application is built on.

The database and the tables it loads, the clock, the queue for background work, settings-driven model clients,
tracing and log events, and the account a request belongs to. Nothing here knows
about chat, memory or the interfaces; they all know about this.
"""
