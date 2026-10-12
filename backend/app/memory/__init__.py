"""Memory: what Attune learns about the user, and what it gives back.

- `public.py`: the only door the rest of the application uses, and the panel's API.
- `graph.py`: what is known about the user, how it is read and how it is searched.
- `sources.py`: what memory learns from, how a stretch of conversation is shown
  to a model, and the one place memory reads chat.
- `learning.py`: the gate, placement, summaries, and what the user tells memory
  directly.
- `review.py`: the look over the whole graph, about once a day.
- `work.py`: the queue kinds and the worker that run all of it.
- `recall.py`: what a turn reads, and what the panel shows.
"""
