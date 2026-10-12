"""User-facing interfaces: the parts of a user's life Attune can act on.

An *interface* is a capability Attune presents to the user (the Calendar, for
example). An interface is a package here. Its `interface.py` declares it (see
`descriptor.py`), and its `chat.py` is what it adds to chat: its artifact types
and the tools that read it and draft them. Beside the packages, `registry.py`
catalogs the interfaces, records which a user has on, and is the API for turning
them on and off.
"""
