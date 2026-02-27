def test_billing_platform_importable() -> None:
    import billing_platform

    assert billing_platform.__version__ == "0.1.0"
