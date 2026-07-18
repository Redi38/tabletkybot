"""
Prescriptions feature handlers, split by sub-feature.

This package replaces what used to be a single 986-line handlers/prescriptions.py.
The public interface is unchanged: `from handlers import prescriptions` and
`prescriptions.router` still work exactly as before — main.py needs no changes.

Module map:
  states.py    — FSM state groups (AddPrescription, BuyPrescription, EditPrescription,
                  RestorePrescription, AddPurchaseToStock)
  keyboards.py — inline keyboards shared across the flows below
  utils.py     — small helpers shared across the flows below
  menu.py      — top-level prescriptions menu navigation
  add.py       — "add a new prescription" FSM flow
  listing.py   — listing active prescriptions
  edit.py      — editing an existing prescription's fields
  buy.py       — marking a purchase against a prescription
  stock.py     — adding a purchased quantity to a medicine's stock,
                 plus the finish-archive/keep-active follow-up prompts
  archive.py   — manual archiving (with confirmation), archive list, deletion
  restore.py   — restoring an archived prescription with new dates/quantity
"""

from aiogram import Router

from . import add, archive, buy, edit, listing, menu, restore, stock

router = Router()
router.include_router(menu.router)
router.include_router(add.router)
router.include_router(listing.router)
router.include_router(edit.router)
router.include_router(buy.router)
router.include_router(stock.router)
router.include_router(archive.router)
router.include_router(restore.router)

__all__ = ["router"]
