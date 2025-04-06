import itertools
import os
from datetime import datetime
from typing import Iterable

from playwright.sync_api import Locator, Page, sync_playwright
from pydantic import BaseModel, ConfigDict, Field

FOODCOOP_USERNAME = os.getenv("FOODCOOP_USERNAME")
FOODCOOP_PASSWORD = os.getenv("FOODCOOP_PASSWORD")

FOODCOOP_USERNAME_INPUT = "Member Number or Email"
FOODCOOP_PASSWORD_INPUT = "Password"
FOODCOOP_LOGIN_BUTTON = "Log In"
FOODCOOP_NEXT_WEEK_BUTTON = "Next Week →"

FOODCOOP_LOGIN_URL = "https://members.foodcoop.com/services/login/"
FOODCOOP_SHIFT_CALENDAR_URL = "https://members.foodcoop.com/services/shifts/"


def authenticate_into_foodcoop(page: Page):
    assert FOODCOOP_USERNAME, "FOODCOOP_USERNAME is not set"
    assert FOODCOOP_PASSWORD, "FOODCOOP_PASSWORD is not set"

    page.goto(FOODCOOP_LOGIN_URL)

    page.get_by_role("textbox", name=FOODCOOP_USERNAME_INPUT).fill(FOODCOOP_USERNAME)
    page.get_by_role("textbox", name=FOODCOOP_PASSWORD_INPUT).fill(FOODCOOP_PASSWORD)
    page.get_by_role("button", name=FOODCOOP_LOGIN_BUTTON).click()


class FoodCoopShiftKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_time: datetime
    label: str


class FoodCoopShift(BaseModel):
    key: FoodCoopShiftKey
    urls: list[str] = Field(default_factory=list)


def parse_shifts_from_calendar_date_locator(
    shift_day: Locator,
) -> Iterable[FoodCoopShift]:
    date_element = shift_day.locator("p b").first
    _, date = date_element.inner_text().strip().split()

    shifts_for_key: dict[FoodCoopShiftKey, FoodCoopShift] = {}
    for shift in shift_day.locator("a.shift").all():
        url = shift.get_attribute("href")
        assert url, "Shift url is missing"

        start_time = shift.locator("b").inner_text()
        _, label = shift.inner_text().strip().split(maxsplit=1)

        key = FoodCoopShiftKey(
            start_time=datetime.strptime(f"{date} {start_time}", "%m/%d/%Y %I:%M%p"),
            label=label,
        )

        shifts_for_key.setdefault(key, FoodCoopShift(key=key)).urls.append(url.strip())

    return shifts_for_key.values()


def parse_shifts_from_calendar_page(page: Page) -> list[FoodCoopShift]:
    shift_day_locators = page.locator(".grid-container div.col").all()

    shifts = itertools.chain.from_iterable(
        parse_shifts_from_calendar_date_locator(shift_day)
        for shift_day in shift_day_locators
    )

    next_week_locator = page.get_by_role("link", name="Next Week →").first
    if next_week_locator.is_visible():
        with page.expect_navigation():
            next_week_locator.click()

        shifts = itertools.chain(shifts, parse_shifts_from_calendar_page(page))

    return list(shifts)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        authenticate_into_foodcoop(page)

        page.goto(FOODCOOP_SHIFT_CALENDAR_URL)

        _shifts = parse_shifts_from_calendar_page(page)

        browser.close()


if __name__ == "__main__":
    main()
