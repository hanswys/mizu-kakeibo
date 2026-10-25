from __future__ import annotations

from datetime import date, timedelta

import pytest
from test_spending import entry, expense, save

from mizu_kakeibo import cli
from mizu_kakeibo.spending import render_status
from mizu_kakeibo.storage import Store
from mizu_kakeibo.subscriptions import (
    add_months,
    detect,
    merchant_key,
    render_subscriptions,
    subscriptions_row,
)

TODAY = date(2026, 10, 25)


def names(entries, today: date = TODAY) -> list[tuple[str, str]]:
    return [(item.merchant, item.interval) for item in detect(entries, today)]


def test_monthly_and_yearly_charges_are_subscriptions() -> None:
    entries = [
        entry("Netflix", 5490, date(2026, 8, 18)),
        entry("Netflix", 5490, date(2026, 9, 18)),
        entry("Netflix", 5490, date(2026, 10, 18)),
        entry("iCloud", 4900, date(2025, 3, 2)),
        entry("iCloud", 4900, date(2026, 3, 2)),
    ]
    found = detect(entries, TODAY)
    assert [(item.merchant, item.interval, item.next_charge) for item in found] == [
        ("Netflix", "monthly", date(2026, 11, 18)),
        ("iCloud", "yearly", date(2027, 3, 2)),
    ]
    assert not any(item.price_changed or item.lapsed for item in found)


def test_one_off_and_irregular_charges_are_not_listed() -> None:
    entries = [
        entry("Uniqlo", 9900, date(2026, 10, 3)),
        entry("Grab", 2400, date(2026, 9, 1)),
        entry("Grab", 2400, date(2026, 9, 12)),
        entry("Shopee", 1500, date(2026, 6, 1)),
        entry("Shopee", 1500, date(2026, 7, 1)),
        entry("Shopee", 1500, date(2026, 9, 20)),
        entry("Gym", 16000, date(2026, 8, 1)),
        entry("Gym", 16000, date(2026, 8, 1)),
        entry("Gym", 16000, date(2026, 9, 1)),
    ]
    assert detect(entries, TODAY) == []


def test_every_gap_must_fit_the_same_interval() -> None:
    entries = [
        entry("Spotify", 1590, date(2025, 9, 1)),
        entry("Spotify", 1590, date(2026, 9, 1)),
        entry("Spotify", 1590, date(2026, 10, 1)),
    ]
    assert detect(entries, TODAY) == []
    assert names(entries[1:]) == [("Spotify", "monthly")]


@pytest.mark.parametrize(
    ("gap", "interval"),
    [(25, None), (26, "monthly"), (35, "monthly"), (36, None)]
    + [(349, None), (350, "yearly"), (380, "yearly"), (381, None)],
)
def test_the_gap_between_charges_sets_the_interval(gap, interval) -> None:
    first = date(2025, 9, 1)
    charges = [entry("Spotify", 1590, first), entry("Spotify", 1590, first + timedelta(gap))]
    assert names(charges) == ([("Spotify", interval)] if interval else [])


def test_month_end_charges_clamp_to_the_shorter_month() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert add_months(date(2028, 2, 29), 12) == date(2029, 2, 28)
    entries = [
        entry("Disney+", 3990, date(2026, 1, 31)),
        entry("Disney+", 3990, date(2026, 2, 28)),
        entry("Disney+", 3990, date(2026, 3, 31)),
    ]
    [found] = detect(entries, date(2026, 4, 10))
    assert found.next_charge == date(2026, 4, 30)


def test_merchant_names_match_after_case_folding_and_spaces_only() -> None:
    assert merchant_key("  Netflix   Malaysia ") == merchant_key("NETFLIX malaysia")
    entries = [
        entry("netflix  malaysia", 5490, date(2026, 9, 18)),
        entry("Netflix Malaysia", 5490, date(2026, 10, 18)),
        entry("Netflix.com", 5490, date(2026, 11, 18)),
    ]
    assert names(entries, date(2026, 11, 20)) == [("Netflix Malaysia", "monthly")]


def test_a_price_or_currency_change_is_pointed_out() -> None:
    rise = [entry("Netflix", 5490, date(2026, 9, 18)), entry("Netflix", 5690, date(2026, 10, 18))]
    fall = [entry("Canva", 2500, date(2026, 8, 5)), entry("Canva", 2000, date(2026, 9, 5))]
    moved = [
        entry("ChatGPT", 9500, date(2026, 9, 2)),
        entry("ChatGPT", 2000, date(2026, 10, 2), "USD"),
    ]
    rendered = render_subscriptions(detect(rise + fall + moved, TODAY), TODAY)
    assert "  up MYR 2.00, new this month\n" in rendered
    assert "  down MYR 5.00\n" in rendered
    assert "  now USD 20.00, new this month\n" in rendered
    # Only this month's changes reach `status`.
    assert subscriptions_row(detect(rise + fall + moved, TODAY), TODAY) == (
        "ChatGPT now USD 20.00, Netflix up MYR 2.00 · 2 new this month"
    )


def test_a_subscription_is_new_in_the_month_of_its_second_charge() -> None:
    entries = [
        entry("Spotify", 1590, date(2026, 9, 24)),
        entry("Spotify", 1590, date(2026, 10, 24)),
        entry("Netflix", 5490, date(2026, 9, 18)),
        entry("Netflix", 5490, date(2026, 10, 18)),
        entry("Netflix", 5490, date(2026, 8, 18)),
    ]
    found = detect(entries, TODAY)
    assert subscriptions_row(found, TODAY) == "1 new this month"
    assert subscriptions_row(found, date(2026, 11, 1)) is None
    rendered = render_subscriptions(found, TODAY)
    assert rendered.count("new this month") == 1
    assert "MYR 15.90  new this month" in rendered


def test_an_overdue_subscription_is_kept_as_lapsed_and_not_counted() -> None:
    entries = [
        entry("Gym", 16000, date(2026, 6, 1)),
        entry("Gym", 16000, date(2026, 7, 1)),
        entry("Netflix", 5490, date(2026, 9, 18)),
        entry("Netflix", 5490, date(2026, 10, 18)),
    ]
    assert [item.lapsed for item in detect(entries, date(2026, 9, 1))] == [False]
    found = detect(entries, TODAY)
    assert [(item.merchant, item.lapsed) for item in found] == [("Netflix", False), ("Gym", True)]
    rendered = render_subscriptions(found, TODAY)
    assert "MYR 160.00  lapsed?\n" in rendered
    assert "  Per month  MYR 54.90\n" in rendered
    assert "is not counted" in rendered


def test_subscriptions_list_and_monthly_total_per_currency() -> None:
    entries = [
        entry("Netflix", 5490, date(2026, 8, 18)),
        entry("Netflix", 5490, date(2026, 9, 18)),
        entry("Netflix", 5490, date(2026, 10, 18)),
        entry("iCloud", 4900, date(2025, 3, 2)),
        entry("iCloud", 4900, date(2026, 3, 2)),
        entry("GitHub", 400, date(2026, 8, 10), "USD"),
        entry("GitHub", 400, date(2026, 9, 10), "USD"),
        entry("GitHub", 400, date(2026, 10, 10), "USD"),
    ]
    assert render_subscriptions(detect(entries, TODAY), TODAY) == (
        "Subscriptions · Sun 25 Oct 2026\n"
        "\n"
        "  GitHub   monthly  last 10 Oct 2026  next 10 Nov 2026   USD 4.00\n"
        "  Netflix  monthly  last 18 Oct 2026  next 18 Nov 2026  MYR 54.90\n"
        "  iCloud   yearly   last 2 Mar 2026   next 2 Mar 2027   MYR 49.00\n"
        "\n"
        "  Per month  MYR 58.98 + USD 4.00\n"
        "  A yearly subscription counts as a twelfth of its amount.\n"
        "  Each currency is totalled separately, not converted to MYR.\n"
    )


def test_a_yearly_twelfth_rounds_half_up() -> None:
    yearly = [entry("Domain", 6, date(2025, 5, 1)), entry("Domain", 6, date(2026, 5, 1))]
    assert detect(yearly, TODAY)[0].monthly_minor == 1
    yearly = [entry("Domain", 5, date(2025, 5, 1)), entry("Domain", 5, date(2026, 5, 1))]
    assert detect(yearly, TODAY)[0].monthly_minor == 0


def test_nothing_found_says_when_one_appears() -> None:
    assert render_subscriptions([], TODAY) == (
        "Subscriptions · Sun 25 Oct 2026\n\n"
        "  None found. A merchant appears after its second regular charge.\n"
    )
    assert subscriptions_row([], TODAY) is None


def test_status_adds_the_subscriptions_row_after_the_others() -> None:
    plain = render_status(TODAY, [], 0, None).splitlines()
    lines = render_status(
        TODAY, [], 0, None, jobs="1 application · 0 to review", subscriptions="1 new this month"
    ).splitlines()
    assert lines[2:9] == [
        "  Today          MYR 0.00 · no expenses",
        "  This week      MYR 0.00 · no expenses",
        "  This month     MYR 0.00 · no expenses",
        "  To review      nothing",
        "  Last sync      none recorded",
        "  Jobs           1 application · 0 to review",
        "  Subscriptions  1 new this month",
    ]
    assert plain[2] == "  Today       MYR 0.00 · no expenses"


def test_corrections_and_removals_change_the_list_at_once(
    private_live_paths, inbox_message, monkeypatch, capsys
) -> None:
    store = Store(private_live_paths / "kakeibo.sqlite3", private_live_paths / "ledger.csv")
    save(store, inbox_message, "m1", expense("NETFLIX.COM", 5490, date(2026, 8, 18)))
    save(store, inbox_message, "m2", expense("Netflix", 5490, date(2026, 9, 18)))
    save(store, inbox_message, "m3", expense("Netflix", 5690, date(2026, 10, 18)))
    save(store, inbox_message, "m4", expense("Lunch", 1200, date(2026, 10, 20)))
    monkeypatch.setattr(cli, "_today", lambda: TODAY)

    assert cli.main(["subscriptions"]) == 0
    assert "MYR 56.90  up MYR 2.00, new this month\n" in capsys.readouterr().out
    assert cli.main([]) == 0
    assert "  Subscriptions  Netflix up MYR 2.00 · 1 new this month\n" in capsys.readouterr().out

    store.correct_transaction("gmail", "m1", merchant="Netflix")
    assert cli.main(["subscriptions"]) == 0
    assert "MYR 56.90  up MYR 2.00\n" in capsys.readouterr().out

    store.remove_transaction("gmail", "m2")
    assert cli.main(["subscriptions"]) == 0
    assert "None found." in capsys.readouterr().out
    assert cli.main([]) == 0
    assert "Subscriptions" not in capsys.readouterr().out.split("\n\n")[1]


def test_subscriptions_does_not_create_a_missing_database(private_live_paths, capsys) -> None:
    with pytest.raises(SystemExit):
        cli.main(["subscriptions"])
    assert "no database at" in capsys.readouterr().err
    assert not private_live_paths.exists()
