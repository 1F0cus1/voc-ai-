"""Run one VOC tagging batch without opening the desktop window."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import voc_ai_tag_controller as controller


APP_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = APP_DIR.parent
CONFIG_PATH = APP_DIR / "voc_tagger_config.json"
LOG_DIR = PACKAGE_DIR / "logs"
class RunLogger:
    def __init__(self, config_path: Path = CONFIG_PATH, profile_name: str = "") -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        config_stem = Path(config_path).stem.lower()
        config_stem = re.sub(r"[^a-z0-9]+", "_", config_stem).strip("_")
        self.profile_name = profile_name.strip() or config_stem or "default"
        self.path = LOG_DIR / f"voc_run_{config_stem or 'default'}_{datetime.now():%Y%m%d}.log"
        self.failed_rows = 0

    def __call__(self, message: str) -> None:
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{self.profile_name}] {message}"
        print(line, flush=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if message.startswith("[失败 "):
            self.failed_rows += 1


def cleanup_old_logs(days: int = 30) -> None:
    if not LOG_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=days)
    for path in LOG_DIR.glob("voc_run_*.log"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                path.unlink()
        except OSError:
            pass


def parse_runtime_args(argv=None):
    parser = argparse.ArgumentParser(description="运行一次 VOC AI 打标批次")
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help="本批次读取的配置文件",
    )
    parser.add_argument(
        "--profile-name",
        default="",
        help="日志中显示的打标器名称",
    )
    parser.add_argument(
        "--profile-id",
        default="",
        help="用于区分并发锁的稳定打标器标识",
    )
    args = parser.parse_args(argv)
    args.profile_id = controller.resolve_profile_id(args.profile_id, args.config)
    return args


def load_config(config_path: Path = CONFIG_PATH) -> dict[str, str]:
    config_path = Path(config_path).resolve()
    if not config_path.exists():
        raise RuntimeError(
            f"未找到配置文件：{config_path}。请先打开对应打标器，填写并保存配置。"
        )

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"配置文件读取失败：{exc}") from exc

    values: dict[str, str] = {}
    for key, value in raw.items():
        text = "" if value is None else str(value)
        if key in controller.SENSITIVE_CONFIG_KEYS:
            try:
                text = controller.decrypt_secret(text)
            except Exception as exc:
                raise RuntimeError(
                    "配置中的密码或 API Key 无法解密。请使用创建定时任务的同一 Windows 用户，"
                    "打开桌面端重新填写并保存配置。"
                ) from exc
        values[key] = text

    defaults = {
        "source_table_name": controller.DEFAULT_SOURCE_TABLE_NAME,
        "label_version": "2026-05-20",
        "batch_limit": "20",
        "register_month": "",
        "use_ai": "1",
        "dry_run": "1",
        "api_base_url": "https://api.openai.com/v1",
        "api_timeout": "60",
        "sleep_seconds": "0",
        "system_prompt": controller.DEFAULT_SYSTEM_PROMPT,
        "decision_rules": controller.DEFAULT_DECISION_RULES,
    }
    for key, value in defaults.items():
        values.setdefault(key, value)

    required = [
        "source_db_host",
        "source_db_port",
        "source_db_user",
        "source_db_name",
        "target_db_host",
        "target_db_port",
        "target_db_user",
        "target_db_name",
        "source_table_name",
        "label_version",
        "batch_limit",
    ]
    if values.get("use_ai") == "1":
        required.extend(["api_key", "model_name"])
    missing = [key for key in required if not values.get(key, "").strip()]
    if missing:
        raise RuntimeError("配置缺少必填项：" + ", ".join(missing))

    try:
        if int(values["batch_limit"]) <= 0:
            raise ValueError
        if int(values.get("api_timeout", "60")) <= 0:
            raise ValueError
        if float(values.get("sleep_seconds", "0")) < 0:
            raise ValueError
    except ValueError as exc:
        raise RuntimeError("本批数量、超时秒数必须大于 0，每条间隔不能小于 0。") from exc

    return values


def mutex_name(profile_id: str = controller.DEFAULT_PROFILE_ID) -> str:
    return controller.profile_mutex_name(profile_id)


def acquire_mutex(profile_id: str = controller.DEFAULT_PROFILE_ID):
    return controller.acquire_profile_mutex(profile_id)


def release_mutex(handle) -> None:
    controller.release_profile_mutex(handle)


def main(argv=None) -> int:
    args = parse_runtime_args(argv)
    config_path = args.config.resolve()
    cleanup_old_logs()
    log = RunLogger(config_path, args.profile_name)
    mutex = acquire_mutex(args.profile_id)
    if mutex is None:
        log("当前打标器已有一个批次正在运行，本次定时触发已跳过。")
        return 0

    try:
        values = load_config(config_path)
        month = values.get("register_month", "").strip()
        log("定时批处理开始。登记月份：" + (month or "全部月份"))
        if values.get("dry_run") == "1":
            log("注意：当前启用了 Dry-run，本次不会写入数据库。")
        controller.run_batch(values, log)
        if log.failed_rows:
            log(f"本次有 {log.failed_rows} 条失败，返回失败状态，供 Windows 定时任务自动重试。")
            return 2
        log("定时批处理正常结束。")
        return 0
    except Exception as exc:
        log("定时批处理异常：" + str(exc))
        log(traceback.format_exc().rstrip())
        return 3
    finally:
        release_mutex(mutex)


if __name__ == "__main__":
    os.chdir(PACKAGE_DIR)
    raise SystemExit(main())
