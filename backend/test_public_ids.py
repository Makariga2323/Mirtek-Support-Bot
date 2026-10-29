from service.public_ids import format_id, parse_id


def test_roundtrip():
    assert format_id("msg", 17) == "msg-17"
    assert parse_id("msg", "msg-17") == 17


def test_garbage_is_none():
    for bad in ["", "msg", "msg-", "msg-abc", "msg--1", "ticket-5", "msg-1.5",
                "msg-²", None, 17, "msg-" + "9" * 40]:
        assert parse_id("msg", bad) is None, bad


def test_old_style_id_gives_zero_not_crash():
    assert parse_id("ticket", "ticket-000000000000") == 0