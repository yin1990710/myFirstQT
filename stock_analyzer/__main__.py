"""支持 python -m stock_analyzer 调用。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
