from aiogram.fsm.state import State, StatesGroup


class CheckoutStates(StatesGroup):
    waiting_name = State()
    waiting_pickup = State()
    review = State()
