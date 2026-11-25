"""允许 `python3 -m app` 直接启动看板。"""
from .server import main

raise SystemExit(main())
