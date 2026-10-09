from aiogram.fsm.state import State, StatesGroup


class CheckoutStates(StatesGroup):
    waiting_name = State()
    waiting_pickup = State()
    waiting_payment = State()
    review = State()
