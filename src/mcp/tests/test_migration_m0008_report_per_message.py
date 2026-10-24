# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""m0008: reports are keyed per message; earlier ones belong to no message."""
from __future__ import annotations

from unittest.mock import MagicMock


def test_m0008_is_registered_last() -> None:
    from scripts.run_migrations import MIGRATIONS

    assert MIGRATIONS[-1] == "app.db.neo4j.migrations.m0008_report_per_message"


def test_earlier_reports_are_keyed_to_no_message_and_the_key_is_indexed() -> None:
    from app.db.neo4j.migrations import m0008_report_per_message

    session = MagicMock()
    session.run.return_value.single.return_value = {"n": 3}
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    assert m0008_report_per_message.run(driver) == {"reports_keyed": 3}
    cyphers = [c.args[0] for c in session.run.call_args_list]
    assert any("ON (r.conversation_id, r.message_id)" in c for c in cyphers)
    assert any("WHERE r.message_id IS NULL SET r.message_id = ''" in c for c in cyphers)
