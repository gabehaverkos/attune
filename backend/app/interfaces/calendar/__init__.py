"""Calendar interface.

`interface.py` declares it and holds what it tells the assistant; `routines.py`,
`events.py` and `schedule.py` are its objects, `chat.py` what it adds to chat,
and `router.py` its API.

`interface.py` is what the registry imports to build the catalog, so it must not
import `app.interfaces.registry` at module level; it reaches it inside a function
instead.
"""
