"""
Local VOC AI tagging controller.

What it does:
1. Reads enabled labels from voc_label_taxonomy.
2. Selects rows from configurable source wide table where:
   - raw_feedback is not empty
   - level4_category is empty
   - the configured result table has no row for the selected label_version
3. Tags each row with one four-level label.
4. Writes results into the configured result table.

Dependencies:
    pip install pymysql

AI API:
    Uses an OpenAI-compatible Chat Completions endpoint.
    Default base URL: https://api.openai.com/v1

Run:
    python scripts/voc_ai_tag_controller.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import socket
import ssl
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
import base64
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

try:
    import pymysql
except ImportError:
    pymysql = None


APP_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = APP_DIR.parent
CONFIG_PATH = APP_DIR / "voc_tagger_config.json"
SCHEDULE_TASK_SCRIPT = APP_DIR / "install_scheduled_task.ps1"
DEFAULT_SCHEDULE_TASK_NAME = "VOC AI Tagger"
DEFAULT_PROFILE_ID = "business"
PROMPT_VERSION = "voc_prompt_v1"
RULE_VERSION = "voc_rule_v1"
LABEL_CACHE: dict[str, dict[str, Any]] = {}
SENSITIVE_CONFIG_KEYS = {"source_db_password", "target_db_password", "db_password", "api_key"}
DPAPI_PREFIX = "dpapi:v1:"
DEFAULT_SOURCE_TABLE_NAME = "dwd_rpa_voc_business"
DEFAULT_RESULT_TABLE_NAME = "voc_tag_result"
DEFAULT_RESULT_DB_CONNECTION = "target"
DEFAULT_RESULT_WRITE_MODE = "mysql"
DEFAULT_SCHEDULE_MODE = "daily"
DEFAULT_SCHEDULE_DAILY_TIME = "02:00"
DEFAULT_SCHEDULE_INTERVAL_MINUTES = "30"
SCHEDULE_CONFIG_KEYS = {
    "schedule_enabled",
    "schedule_mode",
    "schedule_daily_time",
    "schedule_interval_minutes",
}
PROFILE_IDS_BY_CONFIG_NAME = {
    "voc_tagger_config.json": "business",
    "voc_business_tagger_config.json": "business",
    "voc_original_statement_tagger_config.json": "original_statement",
}
MUTEX_ALREADY_EXISTS = 183
DEFAULT_SYSTEM_PROMPT = (
    "\u4f60\u662fVOC\u56db\u7ea7\u6807\u7b7e\u5206\u7c7b\u5668\u3002"
    "\u8bf7\u6839\u636e\u7528\u6237\u8bc4\u8bba\uff0c\u4ece\u7ed9\u5b9a\u5019\u9009\u6807\u7b7e\u4e2d\u53ea\u9009\u62e9\u4e00\u4e2a\u6700\u5408\u9002\u7684\u56db\u7ea7\u7c7b\u76ee\uff0c\u4e0d\u80fd\u521b\u9020\u65b0\u6807\u7b7e\u3002"
    "\u5224\u65ad\u65f6\u5148\u770b\u56db\u7ea7\u7c7b\u76ee\u7684\u5b9a\u4e49\uff0c\u5176\u6b21\u770b\u8bc6\u522b\u5173\u952e\u8bcd\uff0c\u6700\u540e\u770b\u56db\u7ea7\u540d\u79f0\u3002"
    "\u5148\u786e\u5b9a\u56db\u7ea7\u7c7b\u76ee\uff0c\u518d\u8fd4\u56de\u8be5\u5019\u9009\u6807\u7b7e\u81ea\u5e26\u7684\u4e00\u4e8c\u4e09\u7ea7\u7c7b\u76ee\uff0c\u4e0d\u8981\u81ea\u884c\u7f16\u9020\u5c42\u7ea7\u3002"
    "\u5019\u9009\u6807\u7b7e\u4e0d\u5fc5\u548c\u8bc4\u8bba\u5b57\u9762\u4e00\u81f4\uff0c\u6309\u8bed\u4e49\u6700\u63a5\u8fd1\u539f\u5219\u5224\u65ad\u3002"
    "\u82e5\u8bc4\u8bba\u660e\u663e\u65e0\u610f\u4e49\u6216\u4e0eVOC\u65e0\u5173\uff0c\u8fd4\u56deis_irrelevant=true\uff0clevel4_name=\"\u975eVOC\"\uff0clevel1_name/level2_name/level3_name\u4e3a\u7a7a\u5b57\u7b26\u4e32\u3002"
    "\u82e5\u5019\u9009\u6807\u7b7e\u786e\u5b9e\u65e0\u76f8\u5173\u7c7b\u76ee\u6216\u8bc1\u636e\u4e0d\u8db3\uff0c\u8fd4\u56dereview_required=true\u3002"
    "\u5fc5\u987b\u53ea\u8f93\u51fa\u4e00\u4e2a\u5408\u6cd5JSON\u5bf9\u8c61\uff0c\u4e0d\u8981\u89e3\u91ca\u3001\u8868\u683c\u3001Markdown\u6216\u53cd\u95ee\u3002"
)
DEFAULT_DECISION_RULES = "\n".join(
    [
        "\u5148\u770b\u56db\u7ea7\u5b9a\u4e49\uff0c\u518d\u770b\u5173\u952e\u8bcd\uff0c\u6700\u540e\u770b\u56db\u7ea7\u540d\u79f0\u3002",
        "\u53ea\u8fd4\u56de\u4e00\u4e2a\u6700\u4e3b\u8981\u6807\u7b7e\u3002",
        "\u56db\u7ea7\u786e\u5b9a\u540e\uff0c\u4e00\u4e8c\u4e09\u7ea7\u4f7f\u7528\u5019\u9009\u6807\u7b7e\u81ea\u5e26\u5c42\u7ea7\u3002",
        "\u6309\u8bed\u4e49\u6700\u63a5\u8fd1\u539f\u5219\u9009\u62e9\uff0c\u4e0d\u8981\u6c42\u5b57\u9762\u5b8c\u5168\u4e00\u81f4\u3002",
        "\u660e\u663e\u65e0\u610f\u4e49\u5185\u5bb9\u8fd4\u56de\u56db\u7ea7\u7c7b\u76ee\u975eVOC\u3002",
        "\u53ea\u8f93\u51fa\u5408\u6cd5JSON\uff0c\u4e0d\u8981\u89e3\u91ca\u3002",
    ]
)


def resolve_profile_id(
    profile_id: str = "",
    config_path: Path = CONFIG_PATH,
) -> str:
    if profile_id.strip():
        return profile_id.strip().lower()
    return PROFILE_IDS_BY_CONFIG_NAME.get(
        Path(config_path).name.lower(),
        DEFAULT_PROFILE_ID,
    )


def profile_mutex_name(profile_id: str = DEFAULT_PROFILE_ID) -> str:
    stable_profile_id = resolve_profile_id(profile_id)
    identity = f"{PACKAGE_DIR.resolve()}|{stable_profile_id}".lower().encode("utf-8")
    return "Local\\VOC_AI_Tagger_" + hashlib.md5(identity).hexdigest()


def acquire_profile_mutex(profile_id: str = DEFAULT_PROFILE_ID):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateMutexW(None, False, profile_mutex_name(profile_id))
    if not handle:
        raise ctypes.WinError()
    if ctypes.get_last_error() == MUTEX_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return handle


def release_profile_mutex(handle) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.ReleaseMutex(handle)
    kernel32.CloseHandle(handle)


def normalized_schedule(values: dict[str, str]) -> dict[str, str]:
    mode = values.get("schedule_mode", DEFAULT_SCHEDULE_MODE).strip().lower()
    if mode == "daily":
        daily_time = values.get(
            "schedule_daily_time",
            DEFAULT_SCHEDULE_DAILY_TIME,
        ).strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", daily_time):
            raise ValueError("每天执行时间必须使用 HH:mm 格式，例如 02:00。")
        try:
            parsed_time = datetime.strptime(daily_time, "%H:%M")
        except ValueError as exc:
            raise ValueError("每天执行时间必须使用 HH:mm 格式，例如 02:00。") from exc
        return {
            "mode": "Daily",
            "daily_time": parsed_time.strftime("%H:%M"),
        }
    if mode == "minutes":
        interval_text = values.get(
            "schedule_interval_minutes",
            DEFAULT_SCHEDULE_INTERVAL_MINUTES,
        ).strip()
        try:
            interval_minutes = int(interval_text)
        except ValueError as exc:
            raise ValueError("间隔分钟必须是整数，且不能小于 5。") from exc
        if interval_minutes < 5:
            raise ValueError("间隔分钟不能小于 5。")
        return {
            "mode": "Minutes",
            "interval_minutes": str(interval_minutes),
        }
    raise ValueError("定时方式只能选择每天固定时间或每隔 N 分钟。")


def build_scheduled_task_command(
    operation: str,
    task_name: str,
    config_path: Path,
    profile_name: str,
    values: dict[str, str] | None = None,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> list[str]:
    normalized_operation = operation.strip().capitalize()
    if normalized_operation not in {"Install", "Remove", "Query"}:
        raise ValueError(f"不支持的定时任务操作：{operation}")
    if not task_name.strip():
        raise ValueError("定时任务名称不能为空。")

    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCHEDULE_TASK_SCRIPT),
        "-Operation",
        normalized_operation,
        "-TaskName",
        task_name.strip(),
    ]
    if normalized_operation == "Query":
        command.extend(
            [
                "-ConfigPath",
                str(Path(config_path).resolve()),
                "-ProfileName",
                profile_name.strip() or "默认配置",
                "-ProfileId",
                resolve_profile_id(profile_id, config_path),
            ]
        )
    if normalized_operation == "Install":
        schedule = normalized_schedule(values or {})
        command.extend(
            [
                "-ConfigPath",
                str(Path(config_path).resolve()),
                "-ProfileName",
                profile_name.strip() or "默认配置",
                "-ProfileId",
                resolve_profile_id(profile_id, config_path),
                "-Mode",
                schedule["mode"],
            ]
        )
        if schedule["mode"] == "Daily":
            command.extend(["-DailyTime", schedule["daily_time"]])
        else:
            command.extend(["-EveryMinutes", schedule["interval_minutes"]])
    return command


def invoke_scheduled_task(
    operation: str,
    task_name: str,
    config_path: Path,
    profile_name: str,
    values: dict[str, str] | None = None,
    runner=None,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> str:
    command = build_scheduled_task_command(
        operation,
        task_name,
        config_path,
        profile_name,
        values,
        profile_id,
    )
    run_command = runner or subprocess.run
    run_options = {
        "cwd": PACKAGE_DIR,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "check": False,
    }
    if os.name == "nt":
        run_options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = run_command(command, **run_options)
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode != 0:
        detail = stderr or stdout or f"PowerShell 返回状态 {completed.returncode}"
        raise RuntimeError("Windows 定时任务操作失败：" + detail)
    return stdout


def parse_scheduled_task_status(output: str) -> tuple[bool, str]:
    marker = next(
        (
            line.strip()
            for line in reversed(output.splitlines())
            if line.strip().startswith("VOC_TASK_STATUS:")
        ),
        "",
    )
    if marker in {"VOC_TASK_STATUS:NOT_FOUND", "VOC_TASK_STATUS:REMOVED"}:
        return False, "未启用"
    if marker == "VOC_TASK_STATUS:DISABLED":
        return False, "已停用"
    if marker.startswith("VOC_TASK_STATUS:LEGACY"):
        return False, "旧任务待升级"
    if marker.startswith("VOC_TASK_STATUS:INSTALLED"):
        state = marker.partition("VOC_TASK_STATUS:INSTALLED:")[2]
        if state == "Disabled":
            return False, "已停用"
        state_text = {
            "Ready": "已启用",
            "Running": "正在运行",
            "Queued": "等待运行",
        }.get(state, "已启用")
        return True, state_text
    raise RuntimeError("无法识别 Windows 定时任务返回状态。")


@dataclass
class Label:
    id: int
    label_version: str
    level1_name: str
    level2_name: str
    level3_name: str
    level4_name: str
    definition: str
    keywords: str
    keyword_list: list[str] | None = None

    def path(self) -> str:
        return " / ".join(
            [self.level1_name, self.level2_name, self.level3_name, self.level4_name]
        )

    def keywords_list(self) -> list[str]:
        if self.keyword_list is None:
            self.keyword_list = split_keywords(self.keywords)
        return self.keyword_list


@dataclass
class TagResult:
    label_source: str
    taxonomy_label_id: int | None
    level1_name: str
    level2_name: str
    level3_name: str
    level4_name: str
    is_valid_voc: int | None
    is_irrelevant: int
    confidence: float | None
    matched_keywords: list[str]
    candidate_labels: list[dict[str, Any]]
    reason: str
    model_name: str | None = None
    ai_response: dict[str, Any] | None = None
    tag_status: str = "done"
    review_required: int = 0
    error_message: str | None = None


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def md5_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


BUSINESS_HASH_FIELDS = (
    "data_source",
    "channel",
    "platform_order_no",
    "sub_order_no",
    "register_time",
    "raw_feedback",
)


def normalize_hash_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value).strip()


def source_key_value(row: dict[str, Any]) -> str:
    return normalize_hash_value(row.get("voc_hash") or row.get("source_key"))


def text_input_hash(row: dict[str, Any]) -> str:
    return md5_text(normalize_text(row.get("raw_feedback")))


def business_identity(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(normalize_hash_value(row.get(field)) for field in BUSINESS_HASH_FIELDS)


def business_input_hash(row: dict[str, Any]) -> str:
    payload = json.dumps(business_identity(row), ensure_ascii=False, separators=(",", ":"))
    return md5_text(payload)


def stable_result_id(source_table: str, label_version: str, input_hash: str) -> int:
    payload = json.dumps(
        (source_table.strip(), label_version.strip(), input_hash.strip()),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return int(md5_text(payload)[:15], 16) or 1


def input_hashes_for_row(row: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys([text_input_hash(row), business_input_hash(row)]))


def result_matches_source_row(result_row: dict[str, Any], source_row: dict[str, Any]) -> bool:
    existing_hash = normalize_hash_value(result_row.get("input_hash"))
    if existing_hash == business_input_hash(source_row):
        return True
    if existing_hash == text_input_hash(source_row) and business_identity(result_row) == business_identity(source_row):
        return True
    source_key = source_key_value(source_row)
    return bool(source_key and normalize_hash_value(result_row.get("source_key")) == source_key)


def build_result_lookup(result_rows: list[dict[str, Any]]) -> tuple[set[str], set[tuple[str, tuple[str, ...]]], set[str]]:
    hashes: set[str] = set()
    text_identities: set[tuple[str, tuple[str, ...]]] = set()
    source_keys: set[str] = set()
    for row in result_rows:
        input_hash = normalize_hash_value(row.get("input_hash"))
        if input_hash:
            hashes.add(input_hash)
            text_identities.add((input_hash, business_identity(row)))
        source_key = normalize_hash_value(row.get("source_key"))
        if source_key:
            source_keys.add(source_key)
    return hashes, text_identities, source_keys


def result_lookup_matches_source_row(
    lookup: tuple[set[str], set[tuple[str, tuple[str, ...]]], set[str]],
    source_row: dict[str, Any],
) -> bool:
    hashes, text_identities, source_keys = lookup
    if business_input_hash(source_row) in hashes:
        return True
    if (text_input_hash(source_row), business_identity(source_row)) in text_identities:
        return True
    source_key = source_key_value(source_row)
    return bool(source_key and source_key in source_keys)


def split_keywords(value: str) -> list[str]:
    parts = re.split(r"[,，、;；\n\r\t]+", value or "")
    return [p.strip() for p in parts if p and p.strip()]


def text_units(value: str) -> set[str]:
    compact = re.sub(r"\s+", "", value or "")
    units = {compact[i : i + 2] for i in range(max(len(compact) - 1, 0))}
    units.update({compact[i : i + 3] for i in range(max(len(compact) - 2, 0))})
    units.update(ch for ch in compact if "\u4e00" <= ch <= "\u9fff")
    return {unit for unit in units if unit}


def parse_json_response(content: str) -> dict[str, Any]:
    raw = (content or "").strip()
    if not raw:
        raise ValueError("AI返回内容为空，无法解析JSON")

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.S | re.I)
    if fenced:
        return json.loads(fenced.group(1))

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = raw[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            repaired = repair_common_json_issues(candidate)
            if repaired != candidate:
                return json.loads(repaired)

    preview = raw[:300].replace("\n", " ")
    raise ValueError(f"AI返回不是合法JSON：{preview}")


def repair_common_json_issues(raw: str) -> str:
    # Claude sometimes returns an unescaped quote inside reason, e.g.
    # "reason": "最接近"效果不满"类目", "review_required": false
    match = re.search(r'("reason"\s*:\s*")(.*?)("\s*,\s*"review_required"\s*:)', raw, flags=re.S)
    if not match:
        return raw
    reason = match.group(2)
    escaped_reason = reason.replace("\\", "\\\\").replace('"', '\\"')
    return raw[: match.start(2)] + escaped_reason + raw[match.end(2) :]


def repair_json_with_ai(
    endpoint: str,
    api_key: str,
    model_name: str,
    bad_content: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是严格的JSON修复器。你的唯一任务是把用户提供的文本转换为一个合法JSON对象。"
                    "不要解释，不要总结，不要输出表格，不要输出Markdown，不要询问用户。"
                    "如果文本里已经包含分类字段，请保留这些字段并修复语法。只输出JSON对象。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请把下面内容修复为合法JSON对象。只输出JSON，不要解释：\n\n"
                    "<content>\n"
                    + bad_content[:12000]
                    + "\n</content>"
                ),
            },
        ],
    }
    return post_chat_completion(endpoint, api_key, payload, timeout_seconds, allow_repair=False)


def is_obviously_irrelevant(text: str) -> tuple[bool, str]:
    cleaned = re.sub(r"\s+", "", text or "")
    if not cleaned:
        return True, "空内容"
    if re.fullmatch(r"[\d.。!！?？,，、~～\-_=+*/\\|()[\]{}<>《》:：;；'\"“”‘’]+", cleaned):
        return True, "仅包含数字或符号"
    if re.fullmatch(r"\d+", cleaned):
        return True, "纯数字"

    lowered = cleaned.lower()
    meaningless = {
        "哈哈",
        "哈哈哈",
        "哈哈哈哈",
        "呵呵",
        "嘿嘿",
        "无评价",
        "默认好评",
        "系统默认好评",
    }
    if lowered in meaningless:
        return True, "无明确反馈信息"

    if re.fullmatch(r"(哈|啊|嗯|哦|额|呵|嘿){3,}", cleaned):
        return True, "闲聊或语气词"

    if len(set(cleaned)) == 1 and len(cleaned) >= 4:
        repeated_char = cleaned[0]
        if re.fullmatch(r"[\d.。!！?？,，、~～\-_=+*/\\|()[\]{}<>《》:：;；'\"“”‘’]", repeated_char):
            return True, "重复无意义符号或数字"
        if repeated_char in {"哈", "啊", "嗯", "哦", "额", "呵", "嘿"}:
            return True, "重复语气词"

    return False, ""

def build_db_config(values: dict[str, str], prefix: str) -> dict[str, Any]:
    return {
        "host": values[f"{prefix}_db_host"].strip(),
        "port": int(values[f"{prefix}_db_port"].strip() or "3306"),
        "user": values[f"{prefix}_db_user"].strip(),
        "password": values[f"{prefix}_db_password"],
        "database": values[f"{prefix}_db_name"].strip(),
        "charset": "utf8mb4",
        "autocommit": False,
    }


def validated_table_name(value: str, default: str, field_label: str) -> str:
    table_name = (value or default).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?", table_name):
        raise ValueError(f"{field_label}只能填写表名或库名.表名")
    return table_name


def source_table_name(values: dict[str, str]) -> str:
    return validated_table_name(
        values.get("source_table_name", ""),
        DEFAULT_SOURCE_TABLE_NAME,
        "宽表表名",
    )


def result_table_name(values: dict[str, str]) -> str:
    return validated_table_name(
        values.get("result_table_name", ""),
        DEFAULT_RESULT_TABLE_NAME,
        "结果表表名",
    )


def result_db_connection(values: dict[str, str]) -> str:
    value = (values.get("result_db_connection", "") or DEFAULT_RESULT_DB_CONNECTION).strip().lower()
    if value not in {"source", "target"}:
        raise ValueError("结果表连接只能选择 source 或 target")
    return value


def result_write_mode(values: dict[str, str]) -> str:
    value = (values.get("result_write_mode", "") or DEFAULT_RESULT_WRITE_MODE).strip().lower()
    if value not in {"mysql", "primary_key"}:
        raise ValueError("结果表写入模式只能选择 mysql 或 primary_key")
    return value


class DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob_from_bytes(data: bytes) -> DataBlob:
    buffer = ctypes.create_string_buffer(data)
    blob = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob._buffer = buffer
    return blob


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    input_blob = _blob_from_bytes(value.encode("utf-8"))
    output_blob = DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        return DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(output_blob.pbData)


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(DPAPI_PREFIX):
        return ""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    encrypted = base64.b64decode(value[len(DPAPI_PREFIX) :])
    input_blob = _blob_from_bytes(encrypted)
    output_blob = DataBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(output_blob.pbData)


def connect_db(values: dict[str, str], prefix: str):
    if pymysql is None:
        raise RuntimeError("缺少 pymysql，请先执行：pip install pymysql")
    return pymysql.connect(**build_db_config(values, prefix), cursorclass=pymysql.cursors.DictCursor)


def load_labels(conn, label_version: str) -> tuple[list[Label], bool]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MAX(updated_at) AS max_updated_at, COUNT(*) AS label_count
            FROM voc_label_taxonomy
            WHERE label_version = %s
              AND enabled = 1
            """,
            (label_version,),
        )
        meta = cur.fetchone()

    cache_key = label_version
    cached = LABEL_CACHE.get(cache_key)
    if (
        cached
        and cached.get("max_updated_at") == meta.get("max_updated_at")
        and cached.get("label_count") == meta.get("label_count")
    ):
        return cached["labels"], True

    sql = """
        SELECT
          id, label_version, level1_name, level2_name, level3_name, level4_name,
          COALESCE(definition, '') AS definition,
          COALESCE(keywords, '') AS keywords
        FROM voc_label_taxonomy
        WHERE label_version = %s
          AND enabled = 1
        ORDER BY id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (label_version,))
        rows = cur.fetchall()
    labels = [Label(**row) for row in rows]
    for label in labels:
        label.keyword_list = split_keywords(label.keywords)

    LABEL_CACHE[cache_key] = {
        "max_updated_at": meta.get("max_updated_at"),
        "label_count": meta.get("label_count"),
        "labels": labels,
    }
    return labels, False


def source_rows_for_warehouse_sync(
    source_conn,
    source_table: str,
    result_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_keys = list(
        dict.fromkeys(normalize_hash_value(row.get("source_key")) for row in result_rows)
    )
    platform_order_nos = list(
        dict.fromkeys(normalize_hash_value(row.get("platform_order_no")) for row in result_rows)
    )
    sub_order_nos = list(
        dict.fromkeys(normalize_hash_value(row.get("sub_order_no")) for row in result_rows)
    )
    register_times = list(
        dict.fromkeys(row.get("register_time") for row in result_rows if row.get("register_time"))
    )

    clauses: list[str] = []
    params: list[Any] = []
    for column, values in [
        ("w.voc_hash", [value for value in source_keys if value]),
        ("w.platform_order_no", [value for value in platform_order_nos if value]),
        ("w.sub_order_no", [value for value in sub_order_nos if value]),
        ("w.register_time", register_times),
    ]:
        if values:
            clauses.append(f"{column} IN ({', '.join(['%s'] * len(values))})")
            params.extend(values)

    if not clauses:
        return []

    sql = f"""
        SELECT
          w.voc_hash,
          w.data_source,
          w.channel,
          w.platform_order_no,
          w.sub_order_no,
          w.register_time,
          w.raw_feedback,
          w.warehouse_name
        FROM {source_table} w
        WHERE w.warehouse_name IS NOT NULL
          AND TRIM(w.warehouse_name) <> ''
          AND ({' OR '.join(clauses)})
    """
    with source_conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def warehouse_source_lookup(
    source_rows: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[tuple[str, tuple[str, ...]], dict[str, Any]],
]:
    by_source_key: dict[str, dict[str, Any]] = {}
    by_business_hash: dict[str, dict[str, Any]] = {}
    by_text_identity: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for row in source_rows:
        source_key = source_key_value(row)
        if source_key:
            by_source_key.setdefault(source_key, row)
        by_business_hash.setdefault(business_input_hash(row), row)
        by_text_identity.setdefault((text_input_hash(row), business_identity(row)), row)
    return by_source_key, by_business_hash, by_text_identity


def matching_warehouse_source(
    result_row: dict[str, Any],
    lookup: tuple[
        dict[str, dict[str, Any]],
        dict[str, dict[str, Any]],
        dict[tuple[str, tuple[str, ...]], dict[str, Any]],
    ],
) -> dict[str, Any] | None:
    by_source_key, by_business_hash, by_text_identity = lookup
    source_key = normalize_hash_value(result_row.get("source_key"))
    if source_key and source_key in by_source_key:
        return by_source_key[source_key]

    input_hash = normalize_hash_value(result_row.get("input_hash"))
    if input_hash and input_hash in by_business_hash:
        return by_business_hash[input_hash]
    if input_hash:
        return by_text_identity.get((input_hash, business_identity(result_row)))
    return None


def sync_missing_warehouse_names(
    source_conn,
    target_conn,
    source_table: str,
    log,
    dry_run: bool,
    should_stop,
    batch_size: int = 500,
) -> bool:
    log("开始同步结果表仓库名称：仅补齐 warehouse_name 为空的记录。")
    last_id = 0
    checked = updated = unmatched = 0

    while True:
        if should_stop():
            log("收到暂停请求，仓库名称同步已停止，本次不会继续打标。")
            return True

        with target_conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  id, source_key, input_hash,
                  data_source, channel, platform_order_no, sub_order_no,
                  register_time, raw_feedback
                FROM voc_tag_result
                WHERE source_table = %s
                  AND id > %s
                  AND (warehouse_name IS NULL OR TRIM(warehouse_name) = '')
                ORDER BY id
                LIMIT %s
                """,
                (source_table, last_id, batch_size),
            )
            result_rows = list(cur.fetchall())

        if not result_rows:
            break

        last_id = int(result_rows[-1]["id"])
        source_rows = source_rows_for_warehouse_sync(source_conn, source_table, result_rows)
        lookup = warehouse_source_lookup(source_rows)
        updates: list[tuple[str, int]] = []
        for result_row in result_rows:
            source_row = matching_warehouse_source(result_row, lookup)
            warehouse_name = normalize_text(source_row.get("warehouse_name")) if source_row else ""
            if warehouse_name:
                updates.append((warehouse_name, int(result_row["id"])))
            else:
                unmatched += 1

        checked += len(result_rows)
        updated += len(updates)
        if updates and not dry_run:
            with target_conn.cursor() as cur:
                cur.executemany(
                    """
                    UPDATE voc_tag_result
                    SET warehouse_name = %s,
                        update_time = CURRENT_TIMESTAMP
                    WHERE id = %s
                      AND (warehouse_name IS NULL OR TRIM(warehouse_name) = '')
                    """,
                    updates,
                )
            target_conn.commit()

    log(
        f"仓库名称同步完成：检查={checked}, {'可更新' if dry_run else '已更新'}={updated}, "
        f"未匹配或源仓库为空={unmatched}, dry_run={dry_run}"
    )
    return False


def matching_result_rows(
    result_conn,
    result_table: str,
    source_table: str,
    label_version: str,
    input_hashes: list[str],
    source_keys: list[str],
    failed_only: bool,
) -> list[dict[str, Any]]:
    input_hashes = [value for value in dict.fromkeys(input_hashes) if value]
    source_keys = [value for value in dict.fromkeys(source_keys) if value]
    if not input_hashes and not source_keys:
        return []

    params: list[Any] = [source_table, label_version]
    match_clauses: list[str] = []
    if input_hashes:
        placeholders = ", ".join(["%s"] * len(input_hashes))
        match_clauses.append(f"input_hash IN ({placeholders})")
        params.extend(input_hashes)
    if source_keys:
        placeholders = ", ".join(["%s"] * len(source_keys))
        match_clauses.append(f"source_key IN ({placeholders})")
        params.extend(source_keys)

    status_clause = "COALESCE(tag_status, 'done') = 'failed'" if failed_only else "COALESCE(tag_status, 'done') <> 'failed'"
    sql = f"""
        SELECT
          id,
          source_key,
          input_hash,
          data_source,
          channel,
          platform_order_no,
          sub_order_no,
          register_time,
          raw_feedback
        FROM {result_table}
        WHERE source_table = %s
          AND label_version = %s
          AND {status_clause}
          AND ({" OR ".join(match_clauses)})
    """
    with result_conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def find_failed_result_id(
    conn,
    row: dict[str, Any],
    result_table: str,
    source_table: str,
    label_version: str,
) -> int | None:
    rows = matching_result_rows(
        conn,
        result_table,
        source_table,
        label_version,
        input_hashes_for_row(row),
        [source_key_value(row)],
        failed_only=True,
    )
    for result_row in rows:
        if result_matches_source_row(result_row, row):
            return int(result_row["id"])
    return None


def fetch_pending_rows(
    source_conn,
    result_conn,
    source_table: str,
    result_table: str,
    label_version: str,
    register_month: str,
    limit: int,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    last_key = ""
    scan_batch = max(limit * 5, 100)
    month_filter = ""
    params_prefix: list[Any] = []
    if register_month:
        month_filter = "          AND w.register_month = %s\n"
        params_prefix.append(register_month)

    sql = f"""
        SELECT
          w.voc_hash,
          w.data_source,
          w.channel,
          w.brand,
          w.shop_name,
          w.register_month,
          w.register_time,
          w.platform_order_no,
          w.sub_order_no,
          w.sku_code,
          w.product_name,
          w.warehouse_name,
          w.raw_feedback
        FROM {source_table} w
        WHERE w.raw_feedback IS NOT NULL
          AND TRIM(w.raw_feedback) <> ''
          AND (w.level4_category IS NULL OR TRIM(w.level4_category) = '')
{month_filter}          AND w.voc_hash > %s
        ORDER BY w.voc_hash
        LIMIT %s
    """
    while len(collected) < limit:
        with source_conn.cursor() as cur:
            cur.execute(sql, [*params_prefix, last_key, scan_batch])
            rows = cur.fetchall()
        if not rows:
            break

        last_key = str(rows[-1]["voc_hash"])
        existing_rows = matching_result_rows(
            result_conn,
            result_table,
            source_table,
            label_version,
            [value for row in rows for value in input_hashes_for_row(row)],
            [source_key_value(row) for row in rows],
            failed_only=False,
        )
        existing_lookup = build_result_lookup(existing_rows)
        for row in rows:
            if not result_lookup_matches_source_row(existing_lookup, row):
                collected.append(row)
                if len(collected) >= limit:
                    break

    return collected


def keyword_match(text: str, labels: list[Label]) -> tuple[Label | None, list[str], list[Label]]:
    candidates: list[tuple[int, int, Label, list[str]]] = []
    for label in labels:
        matched = [kw for kw in label.keywords_list() if kw and kw in text]
        if matched:
            longest = max(len(kw) for kw in matched)
            candidates.append((len(matched), longest, label, matched))

    if not candidates:
        return None, [], []

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best = candidates[0]
    candidate_labels = [item[2] for item in candidates[:12]]
    return best[2], best[3], candidate_labels


def score_label_for_recall(text: str, label: Label) -> int:
    score = 0
    text_feature = text_units(text)

    for kw in label.keywords_list():
        if kw in text:
            score += 25 + min(len(kw) * 2, 20)

    for token, weight in [
        (label.level4_name, 18),
        (label.level3_name, 10),
        (label.level2_name, 6),
        (label.level1_name, 3),
    ]:
        if token and token in text:
            score += weight

    definition_feature = text_units(label.definition)
    keyword_feature = set()
    for kw in label.keywords_list():
        keyword_feature.update(text_units(kw))
    level4_feature = text_units(label.level4_name)

    definition_overlap = len(text_feature & definition_feature)
    keyword_overlap = len(text_feature & keyword_feature)
    level4_overlap = len(text_feature & level4_feature)

    score += min(definition_overlap * 4, 40)
    score += min(keyword_overlap * 5, 30)
    score += min(level4_overlap * 3, 18)
    return score


def scored_candidates(text: str, labels: list[Label]) -> list[tuple[int, Label]]:
    scored: list[tuple[int, Label]] = []
    for label in labels:
        score = score_label_for_recall(text, label)
        if score > 0:
            scored.append((score, label))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored


def recall_candidates(text: str, labels: list[Label], max_candidates: int = 20) -> list[Label]:
    scored = scored_candidates(text, labels)
    candidates = [label for _, label in scored[:max_candidates]]
    if candidates:
        return candidates
    return labels[:max_candidates]


def labels_to_json(labels: list[Label]) -> list[dict[str, Any]]:
    return [
        {
            "taxonomy_label_id": label.id,
            "level1_name": label.level1_name,
            "level2_name": label.level2_name,
            "level3_name": label.level3_name,
            "level4_name": label.level4_name,
            "definition": label.definition,
            "keywords": label.keywords_list(),
        }
        for label in labels
    ]


def labels_to_prompt_lines(labels: list[Label]) -> str:
    lines = []
    for label in labels:
        keywords = "、".join(label.keywords_list()[:20])
        lines.append(
            "\n".join(
                [
                    f"ID: {label.id}",
                    f"四级路径: {label.level1_name} / {label.level2_name} / {label.level3_name} / {label.level4_name}",
                    f"四级定义: {label.definition or '无'}",
                    f"识别关键词: {keywords or '无'}",
                ]
            )
        )
    return "\n\n".join(lines)


def classification_user_prompt(text: str, candidates: list[Label], decision_rules: str) -> str:
    rules = [line.strip() for line in decision_rules.splitlines() if line.strip()]
    rules_text = "\n".join(f"- {rule}" for rule in rules)
    return f"""
请完成VOC四级分类任务。不要解释，不要反问，不要输出Markdown。

用户评论：
{text}

候选标签如下。你只能从这些候选标签中选择一个，不能创造新标签：
{labels_to_prompt_lines(candidates)}

判断规则：
{rules_text}

输出要求：
只输出一个合法JSON对象，不要解释，不要表格，不要Markdown。字段必须如下：
{{
  "taxonomy_label_id": 123,
  "level1_name": "一级类目，无关评论时为空字符串",
  "level2_name": "二级类目，无关评论时为空字符串",
  "level3_name": "三级类目，无关评论时为空字符串",
  "level4_name": "四级类目，无关评论时固定为非VOC",
  "is_valid_voc": true,
  "is_irrelevant": false,
  "confidence": 0.86,
  "matched_keywords": ["证据词"],
  "reason": "简短中文原因",
  "review_required": false
}}
如果无关评论，taxonomy_label_id必须为null，level1_name/level2_name/level3_name必须为空字符串，level4_name必须为"非VOC"。
""".strip()


def candidate_recall_user_prompt(text: str, labels: list[Label], max_candidates: int) -> str:
    return f"""
请完成VOC候选标签召回任务。不要解释，不要反问，不要输出Markdown。

用户评论：
{text}

标签清单如下。请优先根据四级定义判断语义相关性，选出最多 {max_candidates} 个可能相关的标签ID：
{labels_to_prompt_lines(labels)}

输出要求：
只输出一个合法JSON对象，不要解释，不要表格，不要Markdown：
{{
  "candidate_label_ids": [123, 456],
  "reason": "简短中文说明"
}}
""".strip()

def rule_tag(text: str, labels: list[Label]) -> TagResult | None:
    irrelevant, reason = is_obviously_irrelevant(text)
    if irrelevant:
        return TagResult(
            label_source="rule",
            taxonomy_label_id=None,
            level1_name="",
            level2_name="",
            level3_name="",
            level4_name="非VOC",
            is_valid_voc=0,
            is_irrelevant=1,
            confidence=0.98,
            matched_keywords=[],
            candidate_labels=[],
            reason=reason,
            review_required=0,
        )

    best, matched, candidates = keyword_match(text, labels)
    if best and len(matched) >= 1:
        return TagResult(
            label_source="rule",
            taxonomy_label_id=best.id,
            level1_name=best.level1_name,
            level2_name=best.level2_name,
            level3_name=best.level3_name,
            level4_name=best.level4_name,
            is_valid_voc=1,
            is_irrelevant=0,
            confidence=0.86 if len(matched) == 1 else 0.92,
            matched_keywords=matched,
            candidate_labels=labels_to_json(candidates),
            reason=f"命中关键词：{', '.join(matched)}",
            review_required=0,
        )

    return None


def call_ai(
    *,
    api_key: str,
    base_url: str,
    model_name: str,
    text: str,
    candidates: list[Label],
    timeout_seconds: int,
    system_prompt: str,
    decision_rules: str,
) -> dict[str, Any]:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    user_prompt = classification_user_prompt(text, candidates, decision_rules)
    payload = {
        "model": model_name,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    try:
        return post_chat_completion(endpoint, api_key, payload, timeout_seconds)
    except RuntimeError as exc:
        message = str(exc)
        if "response_format" not in message and "json" not in message.lower() and "400" not in message:
            raise
        fallback_payload = dict(payload)
        fallback_payload.pop("response_format", None)
        return post_chat_completion(endpoint, api_key, fallback_payload, timeout_seconds)


def post_chat_completion(
    endpoint: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: int,
    allow_repair: bool = True,
) -> dict[str, Any]:
    body = ""
    max_attempts = 3
    retry_delay_seconds = 2

    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            retryable = exc.code in {408, 429, 500, 502, 503, 504}
            quota_error = "quota" in detail.lower() or "billing" in detail.lower()
            if attempt < max_attempts and retryable and not quota_error:
                time.sleep(retry_delay_seconds * attempt)
                continue
            raise RuntimeError(f"AI接口HTTP错误 {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, ssl.SSLError, socket.timeout) as exc:
            if attempt < max_attempts:
                time.sleep(retry_delay_seconds * attempt)
                continue
            raise RuntimeError(f"AI接口网络错误，已重试{max_attempts}次：{exc}") from exc

    data = json.loads(body)
    content = data["choices"][0]["message"]["content"]
    lowered_content = (content or "").lower()
    if "what would you like me to do" in lowered_content or "how can i help" in lowered_content:
        preview = (content or "")[:300].replace("\n", " ")
        raise RuntimeError(f"AI没有执行分类任务，而是返回了反问：{preview}")
    if "it looks like" in lowered_content and "json" in lowered_content:
        preview = (content or "")[:300].replace("\n", " ")
        raise RuntimeError(f"AI没有输出JSON，而是在解释JSON内容：{preview}")
    try:
        return parse_json_response(content)
    except (json.JSONDecodeError, ValueError) as exc:
        if allow_repair:
            return repair_json_with_ai(
                endpoint=endpoint,
                api_key=api_key,
                model_name=str(payload.get("model") or ""),
                bad_content=content,
                timeout_seconds=timeout_seconds,
            )
        preview = (content or "")[:300].replace("\n", " ")
        raise RuntimeError(f"AI返回JSON格式错误，且自动修复失败：{exc}；返回预览：{preview}") from exc


def call_ai_candidate_recall(
    *,
    api_key: str,
    base_url: str,
    model_name: str,
    text: str,
    labels: list[Label],
    timeout_seconds: int,
    max_candidates: int = 20,
) -> list[int]:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model_name,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是VOC标签候选召回器。请根据评论语义，优先参考四级类目的定义，"
                    "从给定标签清单中选出最可能相关的标签ID。只输出JSON。"
                ),
            },
            {
                "role": "user",
                "content": candidate_recall_user_prompt(text, labels, max_candidates),
            },
        ],
    }
    try:
        parsed = post_chat_completion(endpoint, api_key, payload, timeout_seconds)
    except RuntimeError as exc:
        message = str(exc)
        if "response_format" not in message and "json" not in message.lower() and "400" not in message:
            raise
        fallback_payload = dict(payload)
        fallback_payload.pop("response_format", None)
        parsed = post_chat_completion(endpoint, api_key, fallback_payload, timeout_seconds)

    ids = parsed.get("candidate_label_ids") or []
    return [int(item) for item in ids if str(item).isdigit()]


def ai_tag(text: str, labels: list[Label], values: dict[str, str]) -> TagResult:
    scored = scored_candidates(text, labels)
    candidates = [label for _, label in scored[:20]] or labels[:20]
    top_score = scored[0][0] if scored else 0

    if top_score < 12:
        recalled_ids = call_ai_candidate_recall(
            api_key=values["api_key"].strip(),
            base_url=values["api_base_url"].strip() or "https://api.openai.com/v1",
            model_name=values["model_name"].strip(),
            text=text,
            labels=labels,
            timeout_seconds=int(values["api_timeout"].strip() or "60"),
            max_candidates=20,
        )
        labels_by_id = {label.id: label for label in labels}
        ai_candidates = [labels_by_id[label_id] for label_id in recalled_ids if label_id in labels_by_id]
        if ai_candidates:
            merged: list[Label] = []
            seen_ids: set[int] = set()
            for label in [*ai_candidates, *candidates]:
                if label.id not in seen_ids:
                    merged.append(label)
                    seen_ids.add(label.id)
            candidates = merged[:20]

    ai_json = call_ai(
        api_key=values["api_key"].strip(),
        base_url=values["api_base_url"].strip() or "https://api.openai.com/v1",
        model_name=values["model_name"].strip(),
        text=text,
        candidates=candidates,
        timeout_seconds=int(values["api_timeout"].strip() or "60"),
        system_prompt=values.get("system_prompt", DEFAULT_SYSTEM_PROMPT).strip() or DEFAULT_SYSTEM_PROMPT,
        decision_rules=values.get("decision_rules", DEFAULT_DECISION_RULES).strip() or DEFAULT_DECISION_RULES,
    )

    is_irrelevant = bool(ai_json.get("is_irrelevant"))
    if is_irrelevant:
        return TagResult(
            label_source="ai",
            taxonomy_label_id=None,
            level1_name="",
            level2_name="",
            level3_name="",
            level4_name="非VOC",
            is_valid_voc=0,
            is_irrelevant=1,
            confidence=float(ai_json.get("confidence") or 0),
            matched_keywords=list(ai_json.get("matched_keywords") or []),
            candidate_labels=labels_to_json(candidates),
            reason=normalize_text(ai_json.get("reason")),
            model_name=values["model_name"].strip(),
            ai_response=ai_json,
            review_required=1 if ai_json.get("review_required") else 0,
        )

    label_id = ai_json.get("taxonomy_label_id")
    selected = next((label for label in candidates if label.id == label_id), None)
    if selected is None:
        selected = next(
            (
                label
                for label in labels
                if label.id == label_id
                or (
                    label.level1_name == ai_json.get("level1_name")
                    and label.level2_name == ai_json.get("level2_name")
                    and label.level3_name == ai_json.get("level3_name")
                    and label.level4_name == ai_json.get("level4_name")
                )
            ),
            None,
        )

    if selected is None:
        return TagResult(
            label_source="ai",
            taxonomy_label_id=None,
            level1_name="",
            level2_name="",
            level3_name="",
            level4_name="",
            is_valid_voc=None,
            is_irrelevant=0,
            confidence=float(ai_json.get("confidence") or 0),
            matched_keywords=list(ai_json.get("matched_keywords") or []),
            candidate_labels=labels_to_json(candidates),
            reason="AI返回的标签不在知识库候选范围内",
            model_name=values["model_name"].strip(),
            ai_response=ai_json,
            tag_status="review",
            review_required=1,
        )

    return TagResult(
        label_source="ai",
        taxonomy_label_id=selected.id,
        level1_name=selected.level1_name,
        level2_name=selected.level2_name,
        level3_name=selected.level3_name,
        level4_name=selected.level4_name,
        is_valid_voc=1 if ai_json.get("is_valid_voc") is not False else 0,
        is_irrelevant=0,
        confidence=float(ai_json.get("confidence") or 0),
        matched_keywords=list(ai_json.get("matched_keywords") or []),
        candidate_labels=labels_to_json(candidates),
        reason=normalize_text(ai_json.get("reason")),
        model_name=values["model_name"].strip(),
        ai_response=ai_json,
        review_required=1 if ai_json.get("review_required") else 0,
    )


def make_failed_result(error: str) -> TagResult:
    return TagResult(
        label_source="ai",
        taxonomy_label_id=None,
        level1_name="",
        level2_name="",
        level3_name="",
        level4_name="",
        is_valid_voc=None,
        is_irrelevant=0,
        confidence=None,
        matched_keywords=[],
        candidate_labels=[],
        reason="打标失败",
        tag_status="failed",
        review_required=1,
        error_message=error[:4000],
    )


def insert_result(
    conn,
    row: dict[str, Any],
    result: TagResult,
    result_table: str,
    source_table: str,
    label_version: str,
    batch_id: str,
    request_id: str,
    write_mode: str = DEFAULT_RESULT_WRITE_MODE,
) -> None:
    write_mode = result_write_mode({"result_write_mode": write_mode})
    id_column = "          id,\n" if write_mode == "primary_key" else ""
    id_value = "          %(id)s,\n" if write_mode == "primary_key" else ""
    upsert_clause = ""
    if write_mode == "mysql":
        upsert_clause = """
        ON DUPLICATE KEY UPDATE
          source_id = VALUES(source_id),
          source_key = VALUES(source_key),
          data_source = VALUES(data_source),
          channel = VALUES(channel),
          brand = VALUES(brand),
          shop_name = VALUES(shop_name),
          register_month = VALUES(register_month),
          register_time = VALUES(register_time),
          platform_order_no = VALUES(platform_order_no),
          sub_order_no = VALUES(sub_order_no),
          sku_code = VALUES(sku_code),
          product_name = VALUES(product_name),
          warehouse_name = VALUES(warehouse_name),
          raw_feedback = VALUES(raw_feedback),
          input_hash = VALUES(input_hash),
          label_source = VALUES(label_source),
          taxonomy_label_id = VALUES(taxonomy_label_id),
          level1_name = VALUES(level1_name),
          level2_name = VALUES(level2_name),
          level3_name = VALUES(level3_name),
          level4_name = VALUES(level4_name),
          is_valid_voc = VALUES(is_valid_voc),
          is_irrelevant = VALUES(is_irrelevant),
          confidence = VALUES(confidence),
          matched_keywords = VALUES(matched_keywords),
          candidate_labels = VALUES(candidate_labels),
          reason = VALUES(reason),
          model_name = VALUES(model_name),
          prompt_version = VALUES(prompt_version),
          rule_version = VALUES(rule_version),
          ai_response = VALUES(ai_response),
          tag_batch_id = VALUES(tag_batch_id),
          request_id = VALUES(request_id),
          tag_status = VALUES(tag_status),
          review_required = VALUES(review_required),
          review_status = VALUES(review_status),
          error_message = VALUES(error_message),
          update_time = CURRENT_TIMESTAMP
        """
    sql = f"""
        INSERT INTO {result_table} (
{id_column}          source_table, source_id, source_key,
          data_source, channel, brand, shop_name, register_month, register_time,
          platform_order_no, sub_order_no, sku_code, product_name, warehouse_name, raw_feedback,
          input_hash,
          label_version, label_source, taxonomy_label_id,
          level1_name, level2_name, level3_name, level4_name,
          is_valid_voc, is_irrelevant,
          confidence, matched_keywords, candidate_labels, reason,
          model_name, prompt_version, rule_version, ai_response,
          tag_batch_id, request_id,
          tag_status, review_required, review_status, error_message
        ) VALUES (
{id_value}          %(source_table)s, %(source_id)s, %(source_key)s,
          %(data_source)s, %(channel)s, %(brand)s, %(shop_name)s, %(register_month)s, %(register_time)s,
          %(platform_order_no)s, %(sub_order_no)s, %(sku_code)s, %(product_name)s, %(warehouse_name)s, %(raw_feedback)s,
          %(input_hash)s,
          %(label_version)s, %(label_source)s, %(taxonomy_label_id)s,
          %(level1_name)s, %(level2_name)s, %(level3_name)s, %(level4_name)s,
          %(is_valid_voc)s, %(is_irrelevant)s,
          %(confidence)s, %(matched_keywords)s, %(candidate_labels)s, %(reason)s,
          %(model_name)s, %(prompt_version)s, %(rule_version)s, %(ai_response)s,
          %(tag_batch_id)s, %(request_id)s,
          %(tag_status)s, %(review_required)s, %(review_status)s, %(error_message)s
        ){upsert_clause}
    """
    raw_feedback = normalize_text(row.get("raw_feedback"))
    input_hash = business_input_hash(row)
    params = {
        "source_id": None,
        "source_key": source_key_value(row),
        "source_table": source_table,
        "data_source": row.get("data_source"),
        "channel": row.get("channel"),
        "brand": row.get("brand"),
        "shop_name": row.get("shop_name"),
        "register_month": row.get("register_month"),
        "register_time": row.get("register_time"),
        "platform_order_no": row.get("platform_order_no"),
        "sub_order_no": row.get("sub_order_no"),
        "sku_code": row.get("sku_code"),
        "product_name": row.get("product_name"),
        "warehouse_name": row.get("warehouse_name"),
        "raw_feedback": raw_feedback,
        "input_hash": input_hash,
        "label_version": label_version,
        "label_source": result.label_source,
        "taxonomy_label_id": result.taxonomy_label_id,
        "level1_name": result.level1_name,
        "level2_name": result.level2_name,
        "level3_name": result.level3_name,
        "level4_name": result.level4_name,
        "is_valid_voc": result.is_valid_voc,
        "is_irrelevant": result.is_irrelevant,
        "confidence": result.confidence,
        "matched_keywords": json.dumps(result.matched_keywords, ensure_ascii=False),
        "candidate_labels": json.dumps(result.candidate_labels, ensure_ascii=False),
        "reason": result.reason,
        "model_name": result.model_name,
        "prompt_version": PROMPT_VERSION,
        "rule_version": RULE_VERSION,
        "ai_response": json.dumps(result.ai_response, ensure_ascii=False) if result.ai_response else None,
        "tag_batch_id": batch_id,
        "request_id": request_id,
        "tag_status": result.tag_status,
        "review_required": result.review_required,
        "review_status": "pending" if result.review_required else "none",
        "error_message": result.error_message,
    }
    if write_mode == "primary_key":
        params["id"] = stable_result_id(source_table, label_version, input_hash)
        with conn.cursor() as cur:
            cur.execute(sql, params)
        return

    retry_result_id = find_failed_result_id(conn, row, result_table, source_table, label_version)
    with conn.cursor() as cur:
        if retry_result_id:
            params["id"] = retry_result_id
            update_sql = f"""
                UPDATE {result_table}
                SET
                  source_table = %(source_table)s,
                  source_id = %(source_id)s,
                  source_key = %(source_key)s,
                  data_source = %(data_source)s,
                  channel = %(channel)s,
                  brand = %(brand)s,
                  shop_name = %(shop_name)s,
                  register_month = %(register_month)s,
                  register_time = %(register_time)s,
                  platform_order_no = %(platform_order_no)s,
                  sub_order_no = %(sub_order_no)s,
                  sku_code = %(sku_code)s,
                  product_name = %(product_name)s,
                  warehouse_name = %(warehouse_name)s,
                  raw_feedback = %(raw_feedback)s,
                  input_hash = %(input_hash)s,
                  label_version = %(label_version)s,
                  label_source = %(label_source)s,
                  taxonomy_label_id = %(taxonomy_label_id)s,
                  level1_name = %(level1_name)s,
                  level2_name = %(level2_name)s,
                  level3_name = %(level3_name)s,
                  level4_name = %(level4_name)s,
                  is_valid_voc = %(is_valid_voc)s,
                  is_irrelevant = %(is_irrelevant)s,
                  confidence = %(confidence)s,
                  matched_keywords = %(matched_keywords)s,
                  candidate_labels = %(candidate_labels)s,
                  reason = %(reason)s,
                  model_name = %(model_name)s,
                  prompt_version = %(prompt_version)s,
                  rule_version = %(rule_version)s,
                  ai_response = %(ai_response)s,
                  tag_batch_id = %(tag_batch_id)s,
                  request_id = %(request_id)s,
                  tag_status = %(tag_status)s,
                  review_required = %(review_required)s,
                  review_status = %(review_status)s,
                  error_message = %(error_message)s,
                  update_time = CURRENT_TIMESTAMP
                WHERE id = %(id)s
            """
            cur.execute(update_sql, params)
        else:
            cur.execute(sql, params)


def run_batch(values: dict[str, str], log, should_stop=None) -> None:
    if should_stop is None:
        should_stop = lambda: False
    source_table = source_table_name(values)
    result_table = result_table_name(values)
    result_connection = result_db_connection(values)
    write_mode = result_write_mode(values)
    label_version = values["label_version"].strip()
    register_month = values.get("register_month", "").strip()
    limit = int(values["batch_limit"].strip() or "20")
    use_ai = values.get("use_ai") == "1"
    dry_run = values.get("dry_run") == "1"
    batch_id = f"voc-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    source_conn = None
    target_conn = None

    try:
        source_conn = connect_db(values, "source")
        target_conn = connect_db(values, "target")
        result_conn = source_conn if result_connection == "source" else target_conn
        if (
            result_connection == "target"
            and write_mode == "mysql"
            and result_table == DEFAULT_RESULT_TABLE_NAME
        ):
            sync_stopped = sync_missing_warehouse_names(
                source_conn,
                result_conn,
                source_table,
                log,
                dry_run,
                should_stop,
            )
            if sync_stopped:
                return

        labels, labels_from_cache = load_labels(target_conn, label_version)
        if not labels:
            raise RuntimeError(f"标签知识库没有启用标签：label_version={label_version}")
        cache_text = "缓存命中" if labels_from_cache else "重新加载"
        log(f"已加载标签 {len(labels)} 条，版本：{label_version}（{cache_text}）")

        rows = fetch_pending_rows(
            source_conn,
            result_conn,
            source_table,
            result_table,
            label_version,
            register_month,
            limit,
        )
        month_text = register_month if register_month else "全部月份"
        log(
            f"待处理宽表记录 {len(rows)} 条，来源表：{source_table}，"
            f"结果表：{result_table}（{result_connection}/{write_mode}），"
            f"月份：{month_text}。批次：{batch_id}"
        )
        if not rows:
            return

        done = failed = review = 0
        for index, row in enumerate(rows, 1):
            if should_stop():
                log("收到暂停请求，当前批次已停止。")
                break
            source_key = row["voc_hash"]
            text = normalize_text(row.get("raw_feedback"))
            request_id = uuid.uuid4().hex
            try:
                result = rule_tag(text, labels)
                if result is None:
                    if not use_ai:
                        result = TagResult(
                            label_source="rule",
                            taxonomy_label_id=None,
                            level1_name="",
                            level2_name="",
                            level3_name="",
                            level4_name="",
                            is_valid_voc=None,
                            is_irrelevant=0,
                            confidence=None,
                            matched_keywords=[],
                            candidate_labels=labels_to_json(recall_candidates(text, labels)),
                            reason="规则无法确定，未启用AI",
                            tag_status="review",
                            review_required=1,
                        )
                    else:
                        result = ai_tag(text, labels, values)

                if result.review_required:
                    result.tag_status = "review" if result.tag_status == "done" else result.tag_status

                if dry_run:
                    log(
                        f"[DRY-RUN {index}/{len(rows)}] voc_hash={source_key} -> "
                        f"{result.level1_name}/{result.level2_name}/{result.level3_name}/{result.level4_name} "
                        f"source={result.label_source} confidence={result.confidence}"
                    )
                else:
                    insert_result(
                        result_conn,
                        row,
                        result,
                        result_table,
                        source_table,
                        label_version,
                        batch_id,
                        request_id,
                        write_mode,
                    )
                    result_conn.commit()
                    log(
                        f"[{index}/{len(rows)}] voc_hash={source_key} -> "
                        f"{result.level1_name}/{result.level2_name}/{result.level3_name}/{result.level4_name} "
                        f"source={result.label_source} status={result.tag_status}"
                    )

                if result.tag_status == "failed":
                    failed += 1
                elif result.review_required:
                    review += 1
                else:
                    done += 1
            except Exception as exc:
                result_conn.rollback()
                failed += 1
                error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
                fail_result = make_failed_result(error)
                if not dry_run:
                    insert_result(
                        result_conn,
                        row,
                        fail_result,
                        result_table,
                        source_table,
                        label_version,
                        batch_id,
                        request_id,
                        write_mode,
                    )
                    result_conn.commit()
                log(f"[失败 {index}/{len(rows)}] voc_hash={source_key}: {error}")

            time.sleep(float(values["sleep_seconds"].strip() or "0"))

        log(f"批次完成：done={done}, review={review}, failed={failed}, dry_run={dry_run}")
    finally:
        if source_conn:
            source_conn.close()
        if target_conn:
            target_conn.close()


class VocTaggerApp:
    def __init__(
        self,
        root: tk.Tk,
        config_path: Path = CONFIG_PATH,
        profile_name: str = "",
        profile_defaults: dict[str, str] | None = None,
        schedule_task_name: str = DEFAULT_SCHEDULE_TASK_NAME,
        profile_id: str = DEFAULT_PROFILE_ID,
    ):
        self.root = root
        self.config_path = Path(config_path).resolve()
        self.profile_name = profile_name.strip() or "默认配置"
        self.schedule_task_name = schedule_task_name.strip()
        self.profile_id = resolve_profile_id(profile_id, self.config_path)
        self.profile_defaults = profile_defaults or {}
        self.root.title(f"VOC AI 打标控制器 - {self.profile_name}")
        self.root.geometry("980x640")
        self.root.minsize(860, 560)
        self.vars: dict[str, tk.StringVar] = {}
        self.text_widgets: dict[str, tk.Text] = {}
        self.encrypted_secret_fallbacks: dict[str, str] = {}
        self.unresolved_secret_keys: set[str] = set()
        self.running = False
        self.stop_requested = False
        self.profile_mutex_handle = None
        self.schedule_busy = False
        self.ui_events: queue.Queue = queue.Queue()
        self._build_ui()
        self._load_config()
        self.root.after(50, self._drain_ui_events)
        self.root.after(100, self.refresh_schedule_status)

    def _add_entry(self, parent, row: int, label: str, key: str, default: str = "", show: str | None = None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=3)
        var = tk.StringVar(value=default)
        self.vars[key] = var
        entry = ttk.Entry(parent, textvariable=var, show=show, width=38)
        entry.grid(row=row, column=1, sticky="ew", padx=6, pady=3)
        return entry

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        frame = ttk.Frame(canvas, padding=(4, 0, 4, 4))
        window_id = canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda _event=None: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
        canvas.bind_all("<MouseWheel>", lambda event: canvas.yview_scroll(int(-1 * (event.delta / 120)), "units"))

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        source_db_frame = ttk.LabelFrame(frame, text="数仓宽表数据库", padding=8)
        source_db_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        source_db_frame.columnconfigure(1, weight=1)
        self._add_entry(source_db_frame, 0, "Host", "source_db_host", "127.0.0.1")
        self._add_entry(source_db_frame, 1, "Port", "source_db_port", "3306")
        self._add_entry(source_db_frame, 2, "User", "source_db_user", "root")
        self._add_entry(source_db_frame, 3, "Password", "source_db_password", "", show="*")
        self._add_entry(source_db_frame, 4, "DB Name", "source_db_name", "")
        self._add_entry(
            source_db_frame,
            5,
            "宽表表名",
            "source_table_name",
            self.profile_defaults.get("source_table_name", DEFAULT_SOURCE_TABLE_NAME),
        )

        target_db_frame = ttk.LabelFrame(frame, text="标签知识库 / 结果设置", padding=8)
        target_db_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        target_db_frame.columnconfigure(1, weight=1)
        self._add_entry(target_db_frame, 0, "Host", "target_db_host", "127.0.0.1")
        self._add_entry(target_db_frame, 1, "Port", "target_db_port", "3306")
        self._add_entry(target_db_frame, 2, "User", "target_db_user", "root")
        self._add_entry(target_db_frame, 3, "Password", "target_db_password", "", show="*")
        self._add_entry(target_db_frame, 4, "DB Name", "target_db_name", "")
        self._add_entry(
            target_db_frame,
            5,
            "结果表表名",
            "result_table_name",
            self.profile_defaults.get("result_table_name", DEFAULT_RESULT_TABLE_NAME),
        )
        ttk.Label(target_db_frame, text="结果表连接").grid(row=6, column=0, sticky="w", padx=6, pady=3)
        self.vars["result_db_connection"] = tk.StringVar(
            value=self.profile_defaults.get("result_db_connection", DEFAULT_RESULT_DB_CONNECTION)
        )
        ttk.Combobox(
            target_db_frame,
            textvariable=self.vars["result_db_connection"],
            values=("target", "source"),
            state="readonly",
            width=35,
        ).grid(row=6, column=1, sticky="ew", padx=6, pady=3)
        ttk.Label(target_db_frame, text="结果表写入模式").grid(row=7, column=0, sticky="w", padx=6, pady=3)
        self.vars["result_write_mode"] = tk.StringVar(
            value=self.profile_defaults.get("result_write_mode", DEFAULT_RESULT_WRITE_MODE)
        )
        ttk.Combobox(
            target_db_frame,
            textvariable=self.vars["result_write_mode"],
            values=("mysql", "primary_key"),
            state="readonly",
            width=35,
        ).grid(row=7, column=1, sticky="ew", padx=6, pady=3)

        ai_frame = ttk.LabelFrame(frame, text="AI 设置", padding=8)
        ai_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        ai_frame.columnconfigure(1, weight=1)
        self._add_entry(
            ai_frame,
            0,
            "API Base URL",
            "api_base_url",
            self.profile_defaults.get("api_base_url", "https://api.openai.com/v1"),
        )
        self._add_entry(ai_frame, 1, "API Key", "api_key", "", show="*")
        self._add_entry(
            ai_frame,
            2,
            "Model",
            "model_name",
            self.profile_defaults.get("model_name", "gpt-4.1-mini"),
        )
        self._add_entry(ai_frame, 3, "Timeout 秒", "api_timeout", "60")
        self._add_entry(ai_frame, 4, "每条间隔 秒", "sleep_seconds", "0")

        run_frame = ttk.LabelFrame(frame, text="执行设置", padding=8)
        run_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        run_frame.columnconfigure(1, weight=1)
        self._add_entry(run_frame, 0, "标签版本", "label_version", "2026-05-20")
        self._add_entry(run_frame, 1, "本批数量", "batch_limit", "20")
        self._add_entry(run_frame, 2, "登记月份（留空=全部）", "register_month", "")
        self.vars["use_ai"] = tk.StringVar(value="1")
        self.vars["dry_run"] = tk.StringVar(value="1")
        ttk.Checkbutton(run_frame, text="启用 AI 兜底", variable=self.vars["use_ai"], onvalue="1", offvalue="0").grid(row=0, column=2, sticky="w", padx=12)
        ttk.Checkbutton(run_frame, text="Dry-run 不写库", variable=self.vars["dry_run"], onvalue="1", offvalue="0").grid(row=1, column=2, sticky="w", padx=12)

        schedule_frame = ttk.LabelFrame(frame, text="定时设置", padding=8)
        schedule_frame.grid(row=3, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        schedule_frame.columnconfigure(1, weight=1)
        ttk.Label(schedule_frame, text="定时方式").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.vars["schedule_mode"] = tk.StringVar(value=DEFAULT_SCHEDULE_MODE)
        mode_frame = ttk.Frame(schedule_frame)
        mode_frame.grid(row=0, column=1, sticky="w", padx=6, pady=3)
        ttk.Radiobutton(
            mode_frame,
            text="每天固定时间",
            variable=self.vars["schedule_mode"],
            value="daily",
        ).pack(side="left", padx=(0, 14))
        ttk.Radiobutton(
            mode_frame,
            text="每隔 N 分钟",
            variable=self.vars["schedule_mode"],
            value="minutes",
        ).pack(side="left")
        self._add_entry(
            schedule_frame,
            1,
            "每天时间（HH:mm）",
            "schedule_daily_time",
            DEFAULT_SCHEDULE_DAILY_TIME,
        )
        self._add_entry(
            schedule_frame,
            2,
            "间隔分钟（至少 5）",
            "schedule_interval_minutes",
            DEFAULT_SCHEDULE_INTERVAL_MINUTES,
        )
        self.vars["schedule_enabled"] = tk.StringVar(value="0")
        self.schedule_status_var = tk.StringVar(value="正在检查...")
        ttk.Label(schedule_frame, text="当前状态").grid(row=0, column=2, sticky="e", padx=(18, 6), pady=3)
        ttk.Label(schedule_frame, textvariable=self.schedule_status_var).grid(row=0, column=3, sticky="w", padx=6, pady=3)
        schedule_buttons = ttk.Frame(schedule_frame)
        schedule_buttons.grid(row=1, column=2, columnspan=2, rowspan=2, sticky="w", padx=(18, 6), pady=3)
        self.apply_schedule_btn = ttk.Button(
            schedule_buttons,
            text="应用 / 更新定时",
            command=self.apply_schedule,
        )
        self.apply_schedule_btn.pack(side="left", padx=(0, 6))
        self.disable_schedule_btn = ttk.Button(
            schedule_buttons,
            text="停用定时",
            command=self.disable_schedule,
        )
        self.disable_schedule_btn.pack(side="left", padx=6)
        self.refresh_schedule_btn = ttk.Button(
            schedule_buttons,
            text="刷新状态",
            command=lambda: self.refresh_schedule_status(show_errors=True),
        )
        self.refresh_schedule_btn.pack(side="left", padx=6)

        prompt_frame = ttk.LabelFrame(frame, text="AI 提示词设置", padding=8)
        prompt_frame.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=5, pady=5)
        prompt_frame.columnconfigure(0, weight=1)
        prompt_frame.columnconfigure(1, weight=1)
        ttk.Label(prompt_frame, text="系统提示词").grid(row=0, column=0, sticky="w", padx=4)
        ttk.Label(prompt_frame, text="判断规则（一行一条）").grid(row=0, column=1, sticky="w", padx=4)
        system_prompt = tk.Text(prompt_frame, height=4, wrap="word")
        system_prompt.insert("1.0", DEFAULT_SYSTEM_PROMPT)
        system_prompt.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        decision_rules = tk.Text(prompt_frame, height=4, wrap="word")
        decision_rules.insert("1.0", DEFAULT_DECISION_RULES)
        decision_rules.grid(row=1, column=1, sticky="nsew", padx=4, pady=4)
        self.text_widgets["system_prompt"] = system_prompt
        self.text_widgets["decision_rules"] = decision_rules

        log_frame = ttk.LabelFrame(frame, text="运行日志", padding=8)
        log_frame.grid(row=5, column=0, columnspan=2, sticky="nsew", padx=5, pady=5)
        log_frame.columnconfigure(0, weight=1)
        self.log_box = scrolledtext.ScrolledText(log_frame, height=12)
        self.log_box.grid(row=0, column=0, sticky="nsew")

        btn_frame = ttk.Frame(outer, padding=(0, 8, 0, 0))
        btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Button(btn_frame, text="保存配置", command=self.save_config).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="测试数据库连接", command=self.test_db).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="测试AI接口", command=self.test_ai).pack(side="left", padx=4)
        self.start_btn = ttk.Button(btn_frame, text="开始打标", command=self.start)
        self.start_btn.pack(side="left", padx=4)
        self.pause_btn = ttk.Button(btn_frame, text="暂停", command=self.pause, state="disabled")
        self.pause_btn.pack(side="left", padx=4)

    def values(self) -> dict[str, str]:
        values = {key: var.get() for key, var in self.vars.items()}
        for key, widget in self.text_widgets.items():
            values[key] = widget.get("1.0", "end").strip()
        return values

    def log(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.insert("end", f"[{timestamp}] {message}\n")
        self.log_box.see("end")
        self.root.update_idletasks()

    def _load_config(self):
        if not self.config_path.exists():
            self.log(f"配置文件尚未创建：{self.config_path}")
            return
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.log(f"配置读取失败：{self.config_path}；{exc}")
            return
        decrypt_failed = False
        for key, value in data.items():
            if key in SENSITIVE_CONFIG_KEYS:
                if isinstance(value, str):
                    try:
                        value = decrypt_secret(value)
                    except Exception:
                        if value:
                            self.encrypted_secret_fallbacks[key] = value
                            self.unresolved_secret_keys.add(key)
                        value = ""
                        decrypt_failed = True
                else:
                    value = ""
            if key in self.vars:
                self.vars[key].set(str(value))
            elif key in self.text_widgets:
                self.text_widgets[key].delete("1.0", "end")
                self.text_widgets[key].insert("1.0", str(value))
        if decrypt_failed:
            self.log("部分密码或 API Key 无法由当前 Windows 用户解密，请重新填写并保存。")

    def save_config(
        self,
        show_confirmation: bool = True,
        overrides: dict[str, str] | None = None,
        preserve_schedule_metadata: bool = False,
    ) -> dict[str, str]:
        saved_schedule_metadata = {}
        if preserve_schedule_metadata and self.config_path.exists():
            try:
                saved_values = json.loads(self.config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                saved_values = {}
            saved_schedule_metadata = {
                key: saved_values[key]
                for key in SCHEDULE_CONFIG_KEYS
                if key in saved_values
            }
        values = self.values()
        if overrides:
            values.update(overrides)
        encrypted_fallbacks = getattr(self, "encrypted_secret_fallbacks", {})
        unresolved_keys = getattr(self, "unresolved_secret_keys", set())
        for key in SENSITIVE_CONFIG_KEYS:
            plain_value = values.get(key, "")
            if plain_value:
                values[key] = encrypt_secret(plain_value)
                encrypted_fallbacks[key] = values[key]
                unresolved_keys.discard(key)
            elif key in unresolved_keys and encrypted_fallbacks.get(key):
                values[key] = encrypted_fallbacks[key]
            else:
                values[key] = encrypt_secret(plain_value)
        if preserve_schedule_metadata:
            for key in SCHEDULE_CONFIG_KEYS:
                if key in saved_schedule_metadata:
                    values[key] = saved_schedule_metadata[key]
                else:
                    values.pop(key, None)
        self.config_path.write_text(
            json.dumps(values, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if show_confirmation:
            messagebox.showinfo(
                "已保存",
                f"配置已保存到：{self.config_path}\n"
                "数据库密码和 API Key 已使用当前 Windows 用户加密。\n"
                "定时任务只有点击“应用 / 更新定时”后才会改变。",
            )
        return values

    def _drain_ui_events(self):
        try:
            while True:
                callback, args = self.ui_events.get_nowait()
                callback(*args)
        except queue.Empty:
            pass
        try:
            self.root.after(50, self._drain_ui_events)
        except tk.TclError:
            pass

    def _post_ui(self, callback, *args):
        self.ui_events.put((callback, args))

    def _set_schedule_busy(self, busy: bool, status_text: str | None = None):
        self.schedule_busy = busy
        button_state = "disabled" if busy else "normal"
        for button_name in (
            "apply_schedule_btn",
            "disable_schedule_btn",
            "refresh_schedule_btn",
        ):
            button = getattr(self, button_name, None)
            if button is not None:
                button.configure(state=button_state)
        if status_text is not None:
            self.schedule_status_var.set(status_text)

    def _run_schedule_in_background(
        self,
        operation: str,
        values: dict[str, str] | None,
        status_text: str,
        success_callback,
        failure_label: str,
        show_errors: bool = True,
    ):
        self._set_schedule_busy(True, status_text)

        def worker():
            try:
                output = invoke_scheduled_task(
                    operation,
                    self.schedule_task_name,
                    self.config_path,
                    self.profile_name,
                    values,
                    profile_id=self.profile_id,
                )
            except Exception as exc:
                self._post_ui(
                    self._finish_schedule_error,
                    failure_label,
                    str(exc),
                    show_errors,
                )
                return
            self._post_ui(success_callback, output)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_schedule_error(
        self,
        action_label: str,
        error_message: str,
        show_errors: bool,
    ):
        self._set_schedule_busy(False, f"{action_label}失败")
        self.log(f"定时任务{action_label}失败：{error_message}")
        if show_errors:
            messagebox.showerror(f"定时任务{action_label}失败", error_message)

    def refresh_schedule_status(self, show_errors: bool = False):
        if not self.schedule_task_name:
            self.schedule_status_var.set("此入口未配置定时任务")
            return
        if self.schedule_busy:
            if show_errors:
                messagebox.showwarning("请稍候", "定时任务操作正在进行中。")
            return
        self._run_schedule_in_background(
            "Query",
            None,
            "正在检查...",
            self._finish_schedule_query,
            "状态检查",
            show_errors,
        )

    def _finish_schedule_query(self, output: str):
        try:
            enabled, status_text = parse_scheduled_task_status(output)
        except Exception as exc:
            self._finish_schedule_error("状态检查", str(exc), False)
            return
        self.vars["schedule_enabled"].set("1" if enabled else "0")
        self._set_schedule_busy(
            False,
            f"{status_text}（{self.schedule_task_name}）",
        )

    def apply_schedule(self):
        if self.schedule_busy:
            messagebox.showwarning("请稍候", "定时任务操作正在进行中。")
            return
        values = self.values()
        required_secret_keys = {"source_db_password", "target_db_password"}
        if values.get("use_ai") == "1":
            required_secret_keys.add("api_key")
        unresolved = [
            key
            for key in required_secret_keys
            if key in self.unresolved_secret_keys and not values.get(key, "").strip()
        ]
        if unresolved:
            labels = {
                "source_db_password": "来源数据库密码",
                "target_db_password": "目标数据库密码",
                "api_key": "API Key",
            }
            messagebox.showwarning(
                "请重新填写密钥",
                "以下内容无法由当前 Windows 用户解密：\n"
                + "\n".join(f"- {labels[key]}" for key in unresolved)
                + "\n请重新填写后再应用定时任务。原密文尚未被覆盖。",
            )
            return
        try:
            schedule = normalized_schedule(values)
        except ValueError as exc:
            messagebox.showwarning("定时配置错误", str(exc))
            return

        schedule_metadata = {
            "schedule_mode": values.get("schedule_mode", DEFAULT_SCHEDULE_MODE).strip().lower(),
            "schedule_daily_time": values.get(
                "schedule_daily_time",
                DEFAULT_SCHEDULE_DAILY_TIME,
            ).strip(),
            "schedule_interval_minutes": values.get(
                "schedule_interval_minutes",
                DEFAULT_SCHEDULE_INTERVAL_MINUTES,
            ).strip(),
        }
        if schedule["mode"] == "Daily":
            schedule_metadata["schedule_daily_time"] = schedule["daily_time"]
        else:
            schedule_metadata["schedule_interval_minutes"] = schedule["interval_minutes"]

        self.save_config(
            show_confirmation=False,
            preserve_schedule_metadata=True,
        )
        schedule_text = (
            f"每天 {schedule['daily_time']}"
            if schedule["mode"] == "Daily"
            else f"每隔 {schedule['interval_minutes']} 分钟"
        )
        self._run_schedule_in_background(
            "Install",
            values,
            "正在应用...",
            lambda output: self._finish_apply_schedule(
                output,
                schedule_text,
                schedule_metadata,
            ),
            "应用",
        )

    def _finish_apply_schedule(
        self,
        output: str,
        schedule_text: str,
        schedule_metadata: dict[str, str],
    ):
        try:
            enabled, status_text = parse_scheduled_task_status(output)
            if not enabled:
                raise RuntimeError("Windows 未返回已启用状态。")
        except Exception as exc:
            self._finish_schedule_error("应用", str(exc), True)
            return
        self.vars["schedule_enabled"].set("1")
        self.save_config(
            show_confirmation=False,
            overrides={**schedule_metadata, "schedule_enabled": "1"},
        )
        self._set_schedule_busy(False, f"{status_text}（{self.schedule_task_name}）")
        self.log(f"定时任务已应用：{self.schedule_task_name}，{schedule_text}")
        messagebox.showinfo(
            "定时任务已启用",
            f"{self.profile_name}：{schedule_text}\n"
            "Windows 登录时也会自动补跑一次。",
        )

    def disable_schedule(self):
        if self.schedule_busy:
            messagebox.showwarning("请稍候", "定时任务操作正在进行中。")
            return
        if not messagebox.askyesno(
            "确认停用",
            f"确认停用“{self.profile_name}”的定时任务吗？\n"
            "当前正在执行的批次会继续完成，后续触发将被停用。",
        ):
            return
        self._run_schedule_in_background(
            "Remove",
            None,
            "正在停用...",
            self._finish_disable_schedule,
            "停用",
        )

    def _finish_disable_schedule(self, output: str):
        try:
            _enabled, status_text = parse_scheduled_task_status(output)
        except Exception as exc:
            self._finish_schedule_error("停用", str(exc), True)
            return
        self.vars["schedule_enabled"].set("0")
        if self.config_path.exists():
            self.save_config(show_confirmation=False, overrides={"schedule_enabled": "0"})
        self._set_schedule_busy(False, f"{status_text}（{self.schedule_task_name}）")
        self.log(f"定时任务已停用：{self.schedule_task_name}")
        messagebox.showinfo("已停用", f"已停用“{self.profile_name}”的定时任务。")

    def test_db(self):
        source_conn = None
        target_conn = None
        try:
            values = self.values()
            source_table = source_table_name(values)
            result_table = result_table_name(values)
            result_connection = result_db_connection(values)
            source_conn = connect_db(values, "source")
            with source_conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS cnt FROM {source_table}")
                wide_count = cur.fetchone()["cnt"]

            target_conn = connect_db(values, "target")
            with target_conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS cnt FROM voc_label_taxonomy")
                label_count = cur.fetchone()["cnt"]

            result_conn = source_conn if result_connection == "source" else target_conn
            with result_conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS cnt FROM {result_table}")
                result_count = cur.fetchone()["cnt"]

            messagebox.showinfo(
                "连接成功",
                f"来源表 {source_table}：{wide_count} 行\n"
                f"标签知识库：{label_count} 行\n"
                f"结果表 {result_table}（{result_connection}）：{result_count} 行",
            )
        except Exception as exc:
            messagebox.showerror("连接失败", str(exc))
        finally:
            if source_conn:
                source_conn.close()
            if target_conn:
                target_conn.close()

    def test_ai(self):
        values = self.values()
        if not values["api_key"].strip():
            messagebox.showwarning("缺少 API Key", "请先填写 API Key。")
            return
        if not values["model_name"].strip():
            messagebox.showwarning("缺少模型", "请先填写 Model。")
            return

        def worker():
            target_conn = None
            try:
                label_version = values["label_version"].strip()
                test_text = "漏发眉刷，客服说补发但一直没收到"
                self.log("开始测试 AI 接口（真实打标结构）...")
                self.log(f"测试评论：{test_text}")

                target_conn = connect_db(values, "target")
                labels, labels_from_cache = load_labels(target_conn, label_version)
                if not labels:
                    raise RuntimeError(f"标签知识库没有启用标签：label_version={label_version}")
                self.log(f"已加载标签 {len(labels)} 条，版本：{label_version}，{'缓存命中' if labels_from_cache else '重新加载'}")

                local_scored = scored_candidates(test_text, labels)
                self.log("本地召回Top候选：")
                for score, label in local_scored[:8]:
                    self.log(f"  score={score} id={label.id} {label.path()}")

                result = ai_tag(test_text, labels, values)
                self.log("AI真实结构测试成功：" + json.dumps({
                    "label_source": result.label_source,
                    "taxonomy_label_id": result.taxonomy_label_id,
                    "level1_name": result.level1_name,
                    "level2_name": result.level2_name,
                    "level3_name": result.level3_name,
                    "level4_name": result.level4_name,
                    "confidence": result.confidence,
                    "review_required": result.review_required,
                    "reason": result.reason,
                    "candidate_count": len(result.candidate_labels),
                }, ensure_ascii=False))
                self.log("最终候选标签摘要：")
                for item in result.candidate_labels[:12]:
                    self.log(
                        f"  id={item.get('taxonomy_label_id')} "
                        f"{item.get('level1_name')}/{item.get('level2_name')}/"
                        f"{item.get('level3_name')}/{item.get('level4_name')}"
                    )
                messagebox.showinfo("AI接口正常", "AI真实打标结构测试成功，详情见日志。")
            except Exception as exc:
                self.log("AI 接口测试失败：" + str(exc))
                messagebox.showerror("AI接口失败", str(exc))
            finally:
                if target_conn:
                    target_conn.close()

        threading.Thread(target=worker, daemon=True).start()

    def start(self):
        if self.running:
            messagebox.showwarning("正在运行", "当前批次还在运行。")
            return
        try:
            values = self.values()
            source_table = source_table_name(values)
            result_table = result_table_name(values)
            result_connection = result_db_connection(values)
            write_mode = result_write_mode(values)
        except ValueError as exc:
            messagebox.showwarning("配置错误", str(exc))
            return
        if self.vars["dry_run"].get() != "1":
            if not messagebox.askyesno(
                "确认写库",
                f"当前不是 Dry-run。\n来源表：{source_table}\n"
                f"结果表：{result_table}\n结果连接：{result_connection}\n"
                f"写入模式：{write_mode}\n确认开始吗？",
            ):
                return

        try:
            self.profile_mutex_handle = acquire_profile_mutex(self.profile_id)
        except Exception as exc:
            messagebox.showerror("无法启动", f"无法创建运行锁：{exc}")
            return
        if self.profile_mutex_handle is None:
            messagebox.showwarning(
                "当前打标器正在运行",
                "同一打标器已有手工或定时批次在运行，请等待该批次结束后再试。",
            )
            return

        self.running = True
        self.stop_requested = False
        self.start_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal")
        thread = threading.Thread(target=self._run_thread, daemon=True)
        try:
            thread.start()
        except Exception:
            release_profile_mutex(self.profile_mutex_handle)
            self.profile_mutex_handle = None
            self.running = False
            self.start_btn.configure(state="normal")
            self.pause_btn.configure(state="disabled")
            raise

    def pause(self):
        if self.running:
            self.stop_requested = True
            self.pause_btn.configure(state="disabled")
            self.log("已请求暂停：当前正在处理的这一条完成后会停止。")

    def _run_thread(self):
        try:
            run_batch(self.values(), self.log, should_stop=lambda: self.stop_requested)
        except Exception as exc:
            self.log("运行失败：")
            self.log(str(exc))
            self.log(traceback.format_exc())
        finally:
            if self.profile_mutex_handle is not None:
                release_profile_mutex(self.profile_mutex_handle)
                self.profile_mutex_handle = None
            self.running = False
            self.start_btn.configure(state="normal")
            self.pause_btn.configure(state="disabled")

def parse_runtime_args(argv=None):
    parser = argparse.ArgumentParser(description="VOC AI 打标控制器")
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help="当前打标器使用的独立配置文件",
    )
    parser.add_argument(
        "--profile-name",
        default="",
        help="显示在窗口标题中的配置名称",
    )
    parser.add_argument(
        "--profile-id",
        default="",
        help="用于区分并发锁的稳定打标器标识",
    )
    parser.add_argument(
        "--schedule-task-name",
        default=DEFAULT_SCHEDULE_TASK_NAME,
        help="当前打标器对应的 Windows 定时任务名称",
    )
    parser.add_argument("--default-source-table", default=DEFAULT_SOURCE_TABLE_NAME)
    parser.add_argument("--default-result-table", default=DEFAULT_RESULT_TABLE_NAME)
    parser.add_argument(
        "--default-result-db-connection",
        default=DEFAULT_RESULT_DB_CONNECTION,
        choices=("target", "source"),
    )
    parser.add_argument(
        "--default-result-write-mode",
        default=DEFAULT_RESULT_WRITE_MODE,
        choices=("mysql", "primary_key"),
    )
    parser.add_argument("--default-api-base-url", default="https://api.openai.com/v1")
    parser.add_argument("--default-model", default="gpt-4.1-mini")
    args = parser.parse_args(argv)
    args.profile_id = resolve_profile_id(args.profile_id, args.config)
    return args


def main(argv=None):
    args = parse_runtime_args(argv)
    root = tk.Tk()
    profile_defaults = {
        "source_table_name": args.default_source_table,
        "result_table_name": args.default_result_table,
        "result_db_connection": args.default_result_db_connection,
        "result_write_mode": args.default_result_write_mode,
        "api_base_url": args.default_api_base_url,
        "model_name": args.default_model,
    }
    app = VocTaggerApp(
        root,
        config_path=args.config,
        profile_name=args.profile_name,
        profile_defaults=profile_defaults,
        schedule_task_name=args.schedule_task_name,
        profile_id=args.profile_id,
    )
    app.log(f"当前配置：{args.profile_name or app.config_path.name}")
    app.log("小范围测试建议：先勾选 Dry-run，本批数量设为 5。确认结果后再取消 Dry-run 写库。")
    root.mainloop()


if __name__ == "__main__":
    main()




