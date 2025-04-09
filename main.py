import asyncio
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, cast
from zoneinfo import ZoneInfo

import uvloop
from dotenv import load_dotenv
from gcsa.event import Event
from gcsa.google_calendar import GoogleCalendar
from google.oauth2 import service_account
from playwright.async_api import BrowserContext, Locator, async_playwright
from pydantic import BaseModel, ConfigDict

load_dotenv()

GOOGLE_FOODCOOP_SHIFT_CALENDAR_ID = "9b8f99f4caf33d2afbd17ac5f64a5113c7e373686247a7126b6a0b96a8cbd462@group.calendar.google.com"
GOOGLE_FOODCOOP_LOCATION = "Park Slope Food Coop"
GOOGLE_SERVICE_ACCOUNT_JSON_PATH = Path("credentials.json")

FOODCOOP_SHIFT_LENGTH = timedelta(hours=2, minutes=45)
FOODCOOP_NUM_SHIFT_CALENDAR_PAGES = 6

FOODCOOP_USERNAME = os.getenv("FOODCOOP_USERNAME")
FOODCOOP_PASSWORD = os.getenv("FOODCOOP_PASSWORD")

FOODCOOP_USERNAME_INPUT = "Member Number or Email"
FOODCOOP_PASSWORD_INPUT = "Password"
FOODCOOP_LOGIN_BUTTON = "Log In"

FOODCOOP_URL = "https://members.foodcoop.com"
FOODCOOP_LOGIN_URL = f"{FOODCOOP_URL}/services/login/"
FOODCOOP_SHIFT_CALENDAR_URL = f"{FOODCOOP_URL}/services/shifts/"


async def authenticate_into_foodcoop(browser_context: BrowserContext):
    assert FOODCOOP_USERNAME, "FOODCOOP_USERNAME is not set"
    assert FOODCOOP_PASSWORD, "FOODCOOP_PASSWORD is not set"

    page = await browser_context.new_page()

    await page.goto(FOODCOOP_LOGIN_URL, wait_until="domcontentloaded")

    await page.get_by_role("textbox", name=FOODCOOP_USERNAME_INPUT).fill(
        FOODCOOP_USERNAME
    )
    await page.get_by_role("textbox", name=FOODCOOP_PASSWORD_INPUT).fill(
        FOODCOOP_PASSWORD
    )
    await page.get_by_role("button", name=FOODCOOP_LOGIN_BUTTON).click()


class FoodCoopShiftKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_time: datetime
    label: str


class FoodCoopShift(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: FoodCoopShiftKey
    urls: frozenset[str]

    @staticmethod
    def from_event(event: Event) -> "FoodCoopShift":
        start_time = cast(datetime, event.start)
        label = cast(str, event.summary)
        description = cast(str, event.description)

        urls = [
            line.strip().lstrip("<li>").rstrip("</li>")
            for line in description.splitlines()
            if line.strip().startswith("<li>")
        ]

        shift = FoodCoopShift(
            key=FoodCoopShiftKey(start_time=start_time, label=label),
            urls=frozenset(urls),
        )

        return shift


def get_calendar_page_urls(
    num_pages: int = FOODCOOP_NUM_SHIFT_CALENDAR_PAGES,
) -> list[str]:
    today = datetime.now()

    return [
        f"{FOODCOOP_SHIFT_CALENDAR_URL}{shift_page}/0/0/{today.strftime('%Y-%m-%d')}/"
        for shift_page in range(num_pages)
    ]


async def parse_shifts_from_calendar_date_locator(
    shift_day: Locator,
) -> Iterable[FoodCoopShift]:
    date_element = shift_day.locator("p b").first
    _, date = (await date_element.inner_text()).strip().split()

    shifts_for_key: dict[FoodCoopShiftKey, list[str]] = {}
    for shift in await shift_day.locator("a.shift:not(.my_shift)").all():
        url = await shift.get_attribute("href")
        assert url, "Shift url is missing"
        url = f"{FOODCOOP_URL}{url.strip().rstrip('/')}"

        start_time = await shift.locator("b").inner_text()
        _, label = (await shift.inner_text()).strip().lstrip("🥕").split(maxsplit=1)
        shift_name, emoji = label.rsplit(maxsplit=1)

        # Put the emoji in front of the label for easier visual parsing on the calendar
        label = f"{emoji} {shift_name}"

        start_time = datetime.strptime(f"{date} {start_time}", "%m/%d/%Y %I:%M%p")
        start_time = start_time.replace(tzinfo=ZoneInfo("US/Eastern"))

        key = FoodCoopShiftKey(start_time=start_time, label=label)

        shifts_for_key.setdefault(key, []).append(url)

    shifts = [
        FoodCoopShift(key=key, urls=frozenset(urls))
        for key, urls in shifts_for_key.items()
    ]

    return shifts


async def parse_shifts_from_calendar_page(
    browser_context: BrowserContext,
    url: str,
) -> list[FoodCoopShift]:
    page = await browser_context.new_page()
    await page.goto(url, wait_until="domcontentloaded")

    shifts = []
    for task in asyncio.as_completed(
        [
            parse_shifts_from_calendar_date_locator(shift_day_locator)
            for shift_day_locator in (
                await page.locator(".grid-container div.col").all()
            )
        ]
    ):
        shifts.extend(await task)

    return shifts


async def parse_shifts_from_calendar(
    browser_context: BrowserContext,
) -> list[FoodCoopShift]:
    shifts = []
    for task in asyncio.as_completed(
        [
            parse_shifts_from_calendar_page(browser_context, url)
            for url in get_calendar_page_urls()
        ]
    ):
        shifts.extend(await task)

    return shifts


def create_event_from_shift(
    shift: FoodCoopShift,
) -> Event:
    return Event(
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


def reconcile_shifts_to_google_calendar(shifts: list[FoodCoopShift]):
    foodcoop_shift_calendar = GoogleCalendar(
        default_calendar=GOOGLE_FOODCOOP_SHIFT_CALENDAR_ID,
        credentials=service_account.Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_JSON_PATH,
            scopes=[
                "https://www.googleapis.com/auth/calendar",
            ],
        ),  # type: ignore
    )

    existing_shifts_for_key: dict[FoodCoopShiftKey, tuple[FoodCoopShift, Event]] = {}
    for event in foodcoop_shift_calendar.get_events():
        existing_shift = FoodCoopShift.from_event(event)

        if existing_shift.key in existing_shifts_for_key:
            foodcoop_shift_calendar.delete_event(event)
        else:
            existing_shifts_for_key[existing_shift.key] = (existing_shift, event)

    parsed_shifts_for_key = {shift.key: shift for shift in shifts}

    print(f"Found {len(existing_shifts_for_key)} shifts in calendar.")
    print(f"Found {len(parsed_shifts_for_key)} shifts in parsed calendar.")

    # Add shifts that don't exist in the calendar
    shifts_to_add = [
        shift for shift in shifts if shift.key not in existing_shifts_for_key
    ]

    print(f"Adding {len(shifts_to_add)} shifts to calendar.")
    for shift in shifts_to_add:
        foodcoop_shift_calendar.add_event(create_event_from_shift(shift))

    # Remove shifts that no longer exist
    events_to_remove = [
        event
        for (shift, event) in existing_shifts_for_key.values()
        if shift.key not in parsed_shifts_for_key
    ]

    print(f"Removing {len(events_to_remove)} shifts to calendar.")
    for event in events_to_remove:
        foodcoop_shift_calendar.delete_event(event)

    # Update shifts that have changed
    shifts_to_update = [
        (shift, event)
        for (shift, event) in existing_shifts_for_key.values()
        if shift.key in parsed_shifts_for_key
        and shift.urls != parsed_shifts_for_key[shift.key].urls
    ]

    print(f"Updating {len(shifts_to_update)} shifts to calendar.")
    for shift, event in shifts_to_update:
        event.description = create_event_from_shift(shift).description
        foodcoop_shift_calendar.update_event(event)


async def main():
    start_time = time.time()

    print("Parsing shifts from foodcoop calendar...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        browser_context = await browser.new_context()

        await authenticate_into_foodcoop(browser_context)

        shifts = await parse_shifts_from_calendar(browser_context)

        await browser.close()

    print(f"Parsed {len(shifts)} shifts in {time.time() - start_time:.2f} seconds.")

    start_time = time.time()

    print("Reconciling shifts to Google Calendar...")

    reconcile_shifts_to_google_calendar(shifts)

    print(
        f"Finished reconciling shifts to calendar in {time.time() - start_time:.2f} seconds."
    )


if __name__ == "__main__":
    uvloop.run(main())
