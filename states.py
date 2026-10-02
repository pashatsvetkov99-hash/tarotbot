from aiogram.fsm.state import StatesGroup, State


class TarotStates(StatesGroup):
    waiting_situation = State()
    waiting_sphere = State()