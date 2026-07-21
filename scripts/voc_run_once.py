"""Run one VOC tagging batch without opening the desktop window."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import voc_ai_tag_controller as controller


APP_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = APP_DIR.parent
CONFIG_PATH = APP_DIR / "voc_tagger_config.json"
LOG_DIR = PACKAGE_DIR / "logs"
MUTEX_ALREADY_EXISTS = 183


class RunLogger:
    def __init__(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOG_DIR / f"voc_run_{datetime.now():%Y%m%d}.log"
        self.failed_rows = 0

    def __call__(self, message: str) -> None:
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
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


def load_config() -> dict[str, str]:
    if not CONFIG_PATH.exists():
        raise RuntimeError(
            "未找到配置文件。请先双击 start_desktop.bat，填写配置并点击保存配置。"
        )

    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
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


def acquire_mutex():
    identity = str(PACKAGE_DIR).lower().encode("utf-8")
    name = "Local\\VOC_AI_Tagger_" + __import__("hashlib").md5(identity).hexdigest()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError()
    if ctypes.get_last_error() == MUTEX_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return handle


def release_mutex(handle) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.ReleaseMutex(handle)
    kernel32.CloseHandle(handle)


def main() -> int:
    cleanup_old_logs()
    log = RunLogger()
    mutex = acquire_mutex()
    if mutex is None:
        log("已有一个 VOC 打标任务正在运行，本次定时触发已跳过。")
        return 0

    try:
        values = load_config()
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
