"""Local sports card database: daily scrape of manufacturer release pages.

Layout:
- models.py   Release / Card dataclasses (the adapter contract)
- db.py       SQLite storage (server/data/sportsdb.sqlite by default)
- http.py     polite fetcher: browser UA, robots.txt, per-host delay, retries
- sources/    one adapter per manufacturer (topps, panini, upperdeck, leaf,
              onyx, wildcard)
- sync.py     one run: releases -> checklists -> runs row, per source
- __main__.py CLI: python -m app.sportsdb sync|stats|search
"""
