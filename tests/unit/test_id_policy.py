from billing_platform.domain.ids import generate_uuidv7


def test_uuidv7_is_version_7() -> None:
    u = generate_uuidv7()
    assert u.version == 7
