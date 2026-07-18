"""
Medicines feature handlers, split by sub-feature.

This package replaces what used to be a single 932-line handlers/medicines.py.
The public interface is unchanged: `from handlers import medicines` and
`medicines.router` still work exactly as before — main.py needs no changes.

Module map:
  states.py    — FSM state groups (AddMedicine, EditMedicine, ExtendMedicine, RestockMedicine)
  keyboards.py — inline keyboards shared across the flows below
  utils.py     — small helpers shared across the flows below
  menu.py      — top-level medicines menu navigation
  add.py       — "add a new medicine" FSM flow
  listing.py   — listing active medicines + per-medicine stats
  archive.py   — archive list, archiving, deleting
  extend.py    — extending/restoring a finished course
  edit.py      — editing an existing medicine's fields
  intake.py    — take/skip reminder button handlers
  restock.py   — restocking after running low/empty
"""

from aiogram import Router

from . import add, archive, edit, extend, intake, listing, menu, restock

router = Router()
router.include_router(menu.router)
router.include_router(add.router)
router.include_router(listing.router)
router.include_router(archive.router)
router.include_router(extend.router)
router.include_router(edit.router)
router.include_router(intake.router)
router.include_router(restock.router)

__all__ = ["router"]
