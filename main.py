import os

from playwright.sync_api import sync_playwright

FOODCOOP_USERNAME = os.getenv("FOODCOOP_USERNAME")
FOODCOOP_PASSWORD = os.getenv("FOODCOOP_PASSWORD")

FOODCOOP_USERNAME_INPUT = "Member Number or Email"
FOODCOOP_PASSWORD_INPUT = "Password"
FOODCOOP_LOGIN_BUTTON = "Log In"

FOODCOOP_LOGIN_URL = "https://members.foodcoop.com/services/login/"


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

        browser.close()


if __name__ == "__main__":
    main()
