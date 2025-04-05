import itertools
import os
from datetime import datetime

from playwright.sync_api import Locator, Page, sync_playwright
from pydantic import BaseModel

FOODCOOP_USERNAME = os.getenv("FOODCOOP_USERNAME")
FOODCOOP_PASSWORD = os.getenv("FOODCOOP_PASSWORD")

FOODCOOP_USERNAME_INPUT = "Member Number or Email"
FOODCOOP_PASSWORD_INPUT = "Password"
FOODCOOP_LOGIN_BUTTON = "Log In"

FOODCOOP_LOGIN_URL = "https://members.foodcoop.com/services/login/"
FOODCOOP_SHIFT_CALENDAR_URL = "https://members.foodcoop.com/services/shifts/"


class FoodCoopShift(BaseModel):
    start_time: datetime
    url: str
    label: str


def parse_shifts_from_calendar_date_locator(
    shift_day: Locator,
) -> list[FoodCoopShift]:
    date_element = shift_day.locator("p b").first
    _, date = date_element.inner_text().strip().split()

    shifts = []
    for shift in shift_day.locator("a.shift").all():
        url = shift.get_attribute("href")
        assert url, "Shift url is missing"

        start_time = shift.locator("b").inner_text()

        _, label = shift.inner_text().strip().split(maxsplit=1)

        shifts.append(
            FoodCoopShift(
                start_time=datetime.strptime(
                    f"{date} {start_time}", "%m/%d/%Y %I:%M%p"
                ),
                url=url.strip(),
                label=label,
            )
        )

    return shifts


def parse_shifts_from_calendar_page(page: Page) -> list[FoodCoopShift]:
    shift_day_locators = page.locator(".grid-container div.col").all()

    return list(
        itertools.chain.from_iterable(
            parse_shifts_from_calendar_date_locator(shift_day)
            for shift_day in shift_day_locators
        )
    )


def main():
    assert FOODCOOP_USERNAME, "FOODCOOP_USERNAME is not set"
    assert FOODCOOP_PASSWORD, "FOODCOOP_PASSWORD is not set"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        # Login to the coop
        page.goto(FOODCOOP_LOGIN_URL)

        page.get_by_role("textbox", name=FOODCOOP_USERNAME_INPUT).fill(
            FOODCOOP_USERNAME
        )
        page.get_by_role("textbox", name=FOODCOOP_PASSWORD_INPUT).fill(
            FOODCOOP_PASSWORD
        )
        page.get_by_role("button", name=FOODCOOP_LOGIN_BUTTON).click()

        # Go to the shift calendar
        page.goto(FOODCOOP_SHIFT_CALENDAR_URL)

        _shifts = parse_shifts_from_calendar_page(page)

        browser.close()


if __name__ == "__main__":
    main()
