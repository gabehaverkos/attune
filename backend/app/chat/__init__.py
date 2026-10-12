"""Chat: conversations, and the one agent that replies in them.

- `threads.py`: conversations and their messages.
- `runs.py`: the run behind each reply, and its contract. A message is accepted
  as a run, claimed under a lease, fenced while it is worked, settled, and
  replayed to anyone watching as its event log.
- `worker.py`: claims runs and drives each to an outcome with the one agent.
- `context.py`: what the agent is told: its instructions and the bounded context
  of one turn.
- `tools.py`: everything the agent can call.
- `artifacts.py`: the proposal cards a tool call leaves in the conversation, which
  the user edits and approves.
- `router.py`: the API for all of it.

What an interface adds to chat lives with the interface
(`app/interfaces/<key>/chat.py`).
"""
