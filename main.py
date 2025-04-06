import itertools
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from gcsa.event import Event
from gcsa.google_calendar import GoogleCalendar
from google.oauth2 import service_account
from playwright.sync_api import Locator, Page, sync_playwright
from pydantic import BaseModel, ConfigDict, Field

GOOGLE_FOODCOOP_SHIFT_CALENDAR_ID = "9b8f99f4caf33d2afbd17ac5f64a5113c7e373686247a7126b6a0b96a8cbd462@group.calendar.google.com"
GOOGLE_FOODCOOP_LOCATION = "Park Slope Food Coop"
GOOGLE_SERVICE_ACCOUNT_JSON_PATH = (
    Path.home()
    .joinpath("Downloads", "park-slope-food-coop-7ef1097a9bc5.json")
    .as_posix()
)

FOODCOOP_SHIFT_LENGTH = timedelta(hours=2, minutes=45)

FOODCOOP_USERNAME = os.getenv("FOODCOOP_USERNAME")
FOODCOOP_PASSWORD = os.getenv("FOODCOOP_PASSWORD")

FOODCOOP_USERNAME_INPUT = "Member Number or Email"
FOODCOOP_PASSWORD_INPUT = "Password"
FOODCOOP_LOGIN_BUTTON = "Log In"
FOODCOOP_NEXT_WEEK_BUTTON = "Next Week →"

FOODCOOP_URL = "https://members.foodcoop.com"
FOODCOOP_LOGIN_URL = f"{FOODCOOP_URL}/services/login/"
FOODCOOP_SHIFT_CALENDAR_URL = f"{FOODCOOP_URL}/services/shifts/"


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
        url = f"{FOODCOOP_URL}{url.strip()}"

        start_time = shift.locator("b").inner_text()
        _, label = shift.inner_text().strip().lstrip("🥕").split(maxsplit=1)

        key = FoodCoopShiftKey(
            start_time=datetime.strptime(f"{date} {start_time}", "%m/%d/%Y %I:%M%p"),
            label=label,
        )

        shifts_for_key.setdefault(key, FoodCoopShift(key=key)).urls.append(url)

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


def sync_shifts_to_google_calendar(shifts: list[FoodCoopShift]):
    foodcoop_shift_calendar = GoogleCalendar(
        default_calendar=GOOGLE_FOODCOOP_SHIFT_CALENDAR_ID,
        credentials=service_account.Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_JSON_PATH,
            scopes=["https://www.googleapis.com/auth/calendar"],
        ),  # type: ignore
    )

    # Delete existing shifts on calendar before syncing
    for event in foodcoop_shift_calendar.get_events():
        foodcoop_shift_calendar.delete_event(event)

    # Sync shifts to calendar
    for shift in shifts:
        foodcoop_shift_calendar.add_event(
            event=Event(
                summary=shift.key.label,
                start=shift.key.start_time,
                end=shift.key.start_time + FOODCOOP_SHIFT_LENGTH,
                description="\n".join(
                    [
                        f"{len(shift.urls)} shift(s) available for {shift.key.label}:",
                        "<ul>",
                        *(f"<li>{url}</li>" for url in shift.urls),
                        "</ul>",
                    ]
                ),
                location=GOOGLE_FOODCOOP_LOCATION,
            )
        )


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        authenticate_into_foodcoop(page)

        page.goto(FOODCOOP_SHIFT_CALENDAR_URL)

        shifts = parse_shifts_from_calendar_page(page)

        browser.close()

    sync_shifts_to_google_calendar(shifts)


if __name__ == "__main__":
    main()
