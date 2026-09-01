from __future__ import annotations

import html
import json
import threading
import time
import traceback
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from voc_ai_tag_controller import (
    DEFAULT_DECISION_RULES,
    DEFAULT_RESULT_DB_CONNECTION,
    DEFAULT_RESULT_TABLE_NAME,
    DEFAULT_RESULT_WRITE_MODE,
    DEFAULT_SOURCE_TABLE_NAME,
    DEFAULT_SYSTEM_PROMPT,
    SENSITIVE_CONFIG_KEYS,
    decrypt_secret,
    encrypt_secret,
    run_batch,
)


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "voc_scheduler_config.json"
HOST = "127.0.0.1"
PORT = 8793

DEFAULT_CONFIG = {
    "source_db_host": "127.0.0.1",
    "source_db_port": "3306",
    "source_db_user": "root",
    "source_db_password": "",
    "source_db_name": "",
    "source_table_name": DEFAULT_SOURCE_TABLE_NAME,
    "target_db_host": "127.0.0.1",
    "target_db_port": "3306",
    "target_db_user": "root",
    "target_db_password": "",
    "target_db_name": "",
    "result_db_connection": DEFAULT_RESULT_DB_CONNECTION,
    "result_write_mode": DEFAULT_RESULT_WRITE_MODE,
    "result_table_name": DEFAULT_RESULT_TABLE_NAME,
    "api_base_url": "https://api.openai.com/v1",
    "api_key": "",
    "model_name": "gpt-4.1-mini",
    "api_timeout": "60",
    "sleep_seconds": "0",
    "label_version": "2026-05-20",
    "batch_limit": "20",
    "register_month": "2026年04月",
    "use_ai": "1",
    "dry_run": "1",
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "decision_rules": DEFAULT_DECISION_RULES,
    "schedule_enabled": "0",
    "interval_minutes": "60",
}


state_lock = threading.Lock()
state = {
    "running": False,
    "stop_requested": False,
    "last_status": "idle",
    "last_started_at": "",
    "last_finished_at": "",
    "next_run_at": "",
    "last_error": "",
}
logs: deque[str] = deque(maxlen=300)


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def add_log(message: str) -> None:
    line = f"[{now_text()}] {message}"
    with state_lock:
        logs.append(line)
    print(line, flush=True)


def load_raw_config() -> dict[str, str]:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                config.update({str(k): "" if v is None else str(v) for k, v in data.items()})
        except Exception as exc:
            add_log(f"读取调度配置失败：{exc}")
    return config


def load_runtime_config() -> dict[str, str]:
    config = load_raw_config()
    for key in SENSITIVE_CONFIG_KEYS:
        value = config.get(key, "")
        if value:
            try:
                config[key] = decrypt_secret(value)
            except Exception:
                config[key] = ""
    return config


def save_config(form: dict[str, str]) -> None:
    existing = load_raw_config()
    config = dict(DEFAULT_CONFIG)
    config.update(existing)

    for key in DEFAULT_CONFIG:
        if key in SENSITIVE_CONFIG_KEYS:
            submitted = form.get(key, "")
            if submitted:
                config[key] = encrypt_secret(submitted)
            elif key not in existing:
                config[key] = ""
        else:
            config[key] = form.get(key, DEFAULT_CONFIG[key])

    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    refresh_next_run(config)


def refresh_next_run(config: dict[str, str]) -> None:
    with state_lock:
        if config.get("schedule_enabled") == "1":
            interval = max(float(config.get("interval_minutes") or 60), 1)
            state["next_run_at"] = datetime.fromtimestamp(time.time() + interval * 60).strftime("%Y-%m-%d %H:%M:%S")
        else:
            state["next_run_at"] = ""


def should_stop() -> bool:
    with state_lock:
        return bool(state["stop_requested"])


def start_job(trigger: str) -> bool:
    with state_lock:
        if state["running"]:
            return False
        state["running"] = True
        state["stop_requested"] = False
        state["last_status"] = "running"
        state["last_error"] = ""
        state["last_started_at"] = now_text()
        state["last_finished_at"] = ""

    thread = threading.Thread(target=run_job, args=(trigger,), daemon=True)
    thread.start()
    return True


def run_job(trigger: str) -> None:
    add_log(f"任务开始，触发方式：{trigger}")
    try:
        values = load_runtime_config()
        run_batch(values, add_log, should_stop=should_stop)
        with state_lock:
            state["last_status"] = "stopped" if state["stop_requested"] else "success"
            state["last_finished_at"] = now_text()
    except Exception as exc:
        with state_lock:
            state["last_status"] = "failed"
            state["last_error"] = str(exc)
            state["last_finished_at"] = now_text()
        add_log("任务失败：" + str(exc))
        add_log(traceback.format_exc())
    finally:
        with state_lock:
            state["running"] = False
            state["stop_requested"] = False
        config = load_raw_config()
        refresh_next_run(config)
        add_log("任务结束")


def scheduler_loop() -> None:
    while True:
        try:
            config = load_raw_config()
            if config.get("schedule_enabled") == "1":
                with state_lock:
                    running = state["running"]
                    next_run_at = state["next_run_at"]
                if not next_run_at:
                    refresh_next_run(config)
                elif not running:
                    try:
                        due = datetime.strptime(next_run_at, "%Y-%m-%d %H:%M:%S").timestamp()
                        if time.time() >= due:
                            start_job("schedule")
                    except ValueError:
                        refresh_next_run(config)
            time.sleep(5)
        except Exception as exc:
            add_log(f"调度循环异常：{exc}")
            time.sleep(10)


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def checked(config: dict[str, str], key: str) -> str:
    return "checked" if config.get(key) == "1" else ""


def render_page(message: str = "") -> str:
    config = load_raw_config()
    with state_lock:
        snapshot = dict(state)
        recent_logs = list(logs)[-120:]

    def input_row(label: str, key: str, password: bool = False) -> str:
        value = "" if password else config.get(key, "")
        input_type = "password" if password else "text"
        placeholder = "留空则保留已保存密文" if password else ""
        return f"""
        <label>
          <span>{esc(label)}</span>
          <input type="{input_type}" name="{esc(key)}" value="{esc(value)}" placeholder="{esc(placeholder)}">
        </label>
        """

    def select_row(label: str, key: str, options: tuple[str, ...]) -> str:
        selected_value = config.get(key, "")
        option_html = "".join(
            f'<option value="{esc(option)}"{" selected" if option == selected_value else ""}>{esc(option)}</option>'
            for option in options
        )
        return f"""
        <label>
          <span>{esc(label)}</span>
          <select name="{esc(key)}">{option_html}</select>
        </label>
        """

    log_text = "\n".join(recent_logs)
    status_color = {
        "running": "#0b72f0",
        "success": "#137333",
        "failed": "#b3261e",
        "stopped": "#8a5a00",
        "idle": "#5f6368",
    }.get(snapshot["last_status"], "#5f6368")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>VOC AI 打标调度后台</title>
  <style>
    body {{ margin: 0; font-family: "Microsoft YaHei", Arial, sans-serif; background: #f6f7f9; color: #1f2328; }}
    header {{ background: #111827; color: white; padding: 14px 22px; display: flex; justify-content: space-between; align-items: center; }}
    main {{ max-width: 1180px; margin: 18px auto; padding: 0 16px 24px; }}
    .status {{ color: {status_color}; font-weight: 700; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    section {{ background: white; border: 1px solid #d7dce2; border-radius: 8px; padding: 14px; }}
    h2 {{ font-size: 16px; margin: 0 0 12px; }}
    label {{ display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 10px; align-items: center; margin: 8px 0; }}
    input, select, textarea {{ width: 100%; box-sizing: border-box; border: 1px solid #c7cdd4; border-radius: 6px; padding: 7px 9px; font: inherit; }}
    textarea {{ min-height: 86px; resize: vertical; }}
    .full {{ grid-column: 1 / -1; }}
    .checks label {{ display: inline-flex; grid-template-columns: none; gap: 8px; margin-right: 18px; }}
    .actions {{ display: flex; gap: 10px; margin: 14px 0; flex-wrap: wrap; }}
    button {{ border: 0; border-radius: 6px; padding: 9px 14px; font: inherit; cursor: pointer; background: #1a73e8; color: white; }}
    button.secondary {{ background: #5f6368; }}
    button.danger {{ background: #b3261e; }}
    button:disabled {{ opacity: .55; cursor: not-allowed; }}
    pre {{ background: #0f172a; color: #d1e7ff; padding: 12px; border-radius: 8px; min-height: 180px; max-height: 360px; overflow: auto; white-space: pre-wrap; }}
    .msg {{ background: #e8f0fe; border: 1px solid #b8cdf8; color: #174ea6; padding: 10px 12px; border-radius: 8px; margin-bottom: 12px; }}
    .meta {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }}
    .meta div {{ background: #f1f3f4; border-radius: 6px; padding: 8px; }}
    .meta b {{ display: block; font-size: 12px; color: #5f6368; margin-bottom: 3px; }}
  </style>
</head>
<body>
  <header>
    <div>VOC AI 打标调度后台</div>
    <div>状态：<span class="status">{esc(snapshot["last_status"])}</span></div>
  </header>
  <main>
    {f'<div class="msg">{esc(message)}</div>' if message else ''}
    <section class="full">
      <h2>运行状态</h2>
      <div class="meta">
        <div><b>是否运行</b>{'是' if snapshot["running"] else '否'}</div>
        <div><b>上次开始</b>{esc(snapshot["last_started_at"])}</div>
        <div><b>上次结束</b>{esc(snapshot["last_finished_at"])}</div>
        <div><b>下次调度</b>{esc(snapshot["next_run_at"])}</div>
      </div>
      {f'<p style="color:#b3261e;">{esc(snapshot["last_error"])}</p>' if snapshot["last_error"] else ''}
      <div class="actions">
        <form method="post" action="/start"><button {'disabled' if snapshot["running"] else ''}>立即执行</button></form>
        <form method="post" action="/stop"><button class="danger" {'disabled' if not snapshot["running"] else ''}>暂停</button></form>
        <form method="get" action="/"><button class="secondary">刷新页面</button></form>
      </div>
    </section>

    <form method="post" action="/save">
      <div class="grid">
        <section>
          <h2>数仓宽表数据库</h2>
          {input_row("Host", "source_db_host")}
          {input_row("Port", "source_db_port")}
          {input_row("User", "source_db_user")}
          {input_row("Password", "source_db_password", True)}
          {input_row("DB Name", "source_db_name")}
          {input_row("宽表表名", "source_table_name")}
        </section>
        <section>
          <h2>标签知识库 / 结果设置</h2>
          {input_row("Host", "target_db_host")}
          {input_row("Port", "target_db_port")}
          {input_row("User", "target_db_user")}
          {input_row("Password", "target_db_password", True)}
          {input_row("DB Name", "target_db_name")}
          {select_row("结果表连接", "result_db_connection", ("target", "source"))}
          {select_row("结果写入模式", "result_write_mode", ("mysql", "primary_key"))}
          {input_row("结果表表名", "result_table_name")}
        </section>
        <section>
          <h2>AI 设置</h2>
          {input_row("API Base URL", "api_base_url")}
          {input_row("API Key", "api_key", True)}
          {input_row("Model", "model_name")}
          {input_row("Timeout 秒", "api_timeout")}
          {input_row("每条间隔 秒", "sleep_seconds")}
        </section>
        <section>
          <h2>任务设置</h2>
          {input_row("标签版本", "label_version")}
          {input_row("登记月份", "register_month")}
          {input_row("本批数量", "batch_limit")}
          {input_row("间隔分钟", "interval_minutes")}
          <div class="checks">
            <label><input type="checkbox" name="use_ai" value="1" {checked(config, "use_ai")}>启用 AI</label>
            <label><input type="checkbox" name="dry_run" value="1" {checked(config, "dry_run")}>Dry-run 不写库</label>
            <label><input type="checkbox" name="schedule_enabled" value="1" {checked(config, "schedule_enabled")}>启用定时</label>
          </div>
        </section>
        <section class="full">
          <h2>提示词</h2>
          <label style="display:block;"><span>系统提示词</span><textarea name="system_prompt">{esc(config.get("system_prompt", ""))}</textarea></label>
          <label style="display:block;"><span>判断规则</span><textarea name="decision_rules">{esc(config.get("decision_rules", ""))}</textarea></label>
          <div class="actions"><button>保存配置</button></div>
        </section>
      </div>
    </form>

    <section class="full">
      <h2>简易运行日志</h2>
      <pre>{esc(log_text)}</pre>
    </section>
  </main>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.respond(render_page())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length).decode("utf-8")
        form = {key: values[-1] if values else "" for key, values in parse_qs(body).items()}
        path = urlparse(self.path).path

        if path == "/save":
            for checkbox in ("use_ai", "dry_run", "schedule_enabled"):
                form[checkbox] = "1" if checkbox in form else "0"
            save_config(form)
            add_log("配置已保存")
            self.respond(render_page("配置已保存"))
            return

        if path == "/start":
            ok = start_job("manual")
            self.respond(render_page("任务已启动" if ok else "任务正在运行中"))
            return

        if path == "/stop":
            with state_lock:
                state["stop_requested"] = True
            add_log("已请求暂停")
            self.respond(render_page("已请求暂停，当前处理完成后停止"))
            return

        self.respond(render_page("未知操作"))

    def log_message(self, _format, *_args):
        return

    def respond(self, body: str):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    add_log(f"调度后台启动：http://{HOST}:{PORT}")
    threading.Thread(target=scheduler_loop, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
