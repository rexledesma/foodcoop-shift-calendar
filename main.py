import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from gcsa.event import Event
from gcsa.google_calendar import GoogleCalendar
from google.oauth2 import service_account
from playwright.async_api import BrowserContext, Locator, async_playwright
from pydantic import BaseModel, ConfigDict, Field

GOOGLE_FOODCOOP_SHIFT_CALENDAR_ID = "9b8f99f4caf33d2afbd17ac5f64a5113c7e373686247a7126b6a0b96a8cbd462@group.calendar.google.com"
GOOGLE_FOODCOOP_LOCATION = "Park Slope Food Coop"
GOOGLE_SERVICE_ACCOUNT_JSON_PATH = (
    Path.home()
    .joinpath("Downloads", "park-slope-food-coop-7ef1097a9bc5.json")
    .as_posix()
)

FOODCOOP_SHIFT_LENGTH = timedelta(hours=2, minutes=45)
FOODCOOP_NUM_CALENDAR_SHIFT_PAGES = 6

FOODCOOP_USERNAME = os.getenv("FOODCOOP_USERNAME")
FOODCOOP_PASSWORD = os.getenv("FOODCOOP_PASSWORD")

FOODCOOP_USERNAME_INPUT = "Member Number or Email"
FOODCOOP_PASSWORD_INPUT = "Password"
FOODCOOP_LOGIN_BUTTON = "Log In"
FOODCOOP_NEXT_WEEK_BUTTON = "Next Week →"

FOODCOOP_URL = "https://members.foodcoop.com"
FOODCOOP_LOGIN_URL = f"{FOODCOOP_URL}/services/login/"
FOODCOOP_SHIFT_CALENDAR_URL = f"{FOODCOOP_URL}/services/shifts/"


async def authenticate_into_foodcoop(browser_context: BrowserContext):
    assert FOODCOOP_USERNAME, "FOODCOOP_USERNAME is not set"
    assert FOODCOOP_PASSWORD, "FOODCOOP_PASSWORD is not set"

    page = await browser_context.new_page()

    await page.goto(FOODCOOP_LOGIN_URL)

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
    key: FoodCoopShiftKey
    urls: list[str] = Field(default_factory=list)


def get_calendar_page_urls():
    today = datetime.now()

    return [
        f"{FOODCOOP_SHIFT_CALENDAR_URL}{shift_page}/0/0/{today.strftime('%Y-%m-%d')}/"
        for shift_page in range(FOODCOOP_NUM_CALENDAR_SHIFT_PAGES)
    ]


async def parse_shifts_from_calendar_date_locator(
    shift_day: Locator,
) -> Iterable[FoodCoopShift]:
    date_element = shift_day.locator("p b").first
    _, date = (await date_element.inner_text()).strip().split()

    shifts_for_key: dict[FoodCoopShiftKey, FoodCoopShift] = {}
    for shift in await shift_day.locator("a.shift").all():
        url = await shift.get_attribute("href")
        assert url, "Shift url is missing"
        url = f"{FOODCOOP_URL}{url.strip()}"

        start_time = await shift.locator("b").inner_text()
        _, label = (await shift.inner_text()).strip().lstrip("🥕").split(maxsplit=1)

        key = FoodCoopShiftKey(
            start_time=datetime.strptime(f"{date} {start_time}", "%m/%d/%Y %I:%M%p"),
            label=label,
        )

        shifts_for_key.setdefault(key, FoodCoopShift(key=key)).urls.append(url)

    return shifts_for_key.values()


async def parse_shifts_from_calendar_page(
    browser_context: BrowserContext,
    url: str,
) -> list[FoodCoopShift]:
    page = await browser_context.new_page()
    await page.goto(url)

    shifts = []
    async for task in asyncio.as_completed(
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
    shift_calendar_urls = get_calendar_page_urls()
    shifts = []

    async for task in asyncio.as_completed(
        [
            parse_shifts_from_calendar_page(browser_context, url)
            for url in shift_calendar_urls
        ]
    ):
        shifts.extend(await task)

    return shifts


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


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        browser_context = await browser.new_context()

        await authenticate_into_foodcoop(browser_context)

        shifts = await parse_shifts_from_calendar(browser_context)

        await browser.close()

    sync_shifts_to_google_calendar(shifts)


if __name__ == "__main__":
    asyncio.run(main())
