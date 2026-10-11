def pytest_addoption(parser):
    parser.addoption(
        "--library-backend", choices=("pinned", "native"), default=None,
        help="Require b1 backports or the native public library API; prevents masked upgrade tests.",
    )
