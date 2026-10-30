#!/usr/bin/env python3
"""Digital Twins menu lock: company flag shows the row and blocks the open."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wallet


def test_company_flag_locks_the_seat():
    data = {"companies": {"Amazon": {"digital_twins_locked": True}}}
    user = {"username": "mansol", "company": "Amazon", "role": "user"}
    assert wallet.digital_twins_menu_locked(user, data) is True


def test_case_insensitive_company():
    data = {"companies": {"Amazon": {"digital_twins_locked": True}}}
    user = {"username": "gcline", "company": "amazon", "role": "user"}
    assert wallet.digital_twins_menu_locked(user, data) is True


def test_other_companies_open():
    data = {"companies": {"Amazon": {"digital_twins_locked": True}, "Kartel": {}}}
    user = {"username": "bkusin", "company": "Kartel", "role": "user"}
    assert wallet.digital_twins_menu_locked(user, data) is False


def test_super_admin_never_locked():
    data = {"companies": {"Amazon": {"digital_twins_locked": True}}}
    user = {"username": "jenna", "company": "Amazon", "role": "super_admin"}
    assert wallet.digital_twins_menu_locked(user, data) is False


def test_missing_flag_opens():
    data = {"companies": {"Amazon": {"unlimited": True}}}
    user = {"username": "mansol", "company": "Amazon", "role": "user"}
    assert wallet.digital_twins_menu_locked(user, data) is False


def test_seat_flag_locks_without_company():
    user = {"username": "jaypatel14", "role": "user", "digital_twins_locked": True}
    assert wallet.digital_twins_menu_locked(user, {"companies": {}}) is True


if __name__ == "__main__":
    test_company_flag_locks_the_seat()
    test_case_insensitive_company()
    test_other_companies_open()
    test_super_admin_never_locked()
    test_missing_flag_opens()
    test_seat_flag_locks_without_company()
    print("ok")
