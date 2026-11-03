import argparse
import os

import uvicorn

from .app import create_app


def main() -> None:
    ap = argparse.ArgumentParser(description="大肥鱼数字人服务")
    ap.add_argument("--host", default=os.environ.get("YUYAYA_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("YUYAYA_PORT", 8848)))
    ap.add_argument("--config", help="配置文件路径，默认 config.json（不存在则用 config.example.json）")
    args = ap.parse_args()
    if args.config:
        os.environ["YUYAYA_CONFIG"] = args.config
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
