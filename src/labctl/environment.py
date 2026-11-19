"""Per-experiment environment isolation (Python venv, local node_modules)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
GET_PIP_TIMEOUT = 90


@dataclass
class EnvOutcome:
    record: dict | None = None
    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def env_type_for_template(template: str) -> str:
    return template if template in {"python", "node"} else "none"


def _run(
    args: list[str],
    cwd: Path,
    *,
    timeout: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _tail(text: str, limit: int = 400) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return "…" + text[-limit:]


def setup_environment(experiment_dir: Path, template: str, *, enabled: bool = True) -> EnvOutcome:
    """Create an isolated environment for a new experiment.

    Never touches global interpreters or package managers. Failures are
    reported as warnings: a broken environment must not destroy an
    otherwise valid experiment.
    """

    outcome = EnvOutcome()
    if not enabled:
        outcome.messages.append("已跳过环境初始化（--no-env）")
        return outcome

    env_type = env_type_for_template(template)
    if env_type == "python":
        outcome.record = _setup_python(experiment_dir, outcome)
    elif env_type == "node":
        outcome.record = _setup_node(experiment_dir, outcome)
    else:
        outcome.messages.append("通用模板不创建语言环境")
    return outcome


# ----------------------------------------------------------------------
# Python
# ----------------------------------------------------------------------
def _python_has_pip(python: Path) -> bool:
    try:
        result = subprocess.run(
            [str(python), "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _cached_get_pip(outcome: EnvOutcome) -> Path | None:
    cache_dir = Path.home() / ".cache" / "lab"
    script = cache_dir / "get-pip.py"
    if script.is_file() and script.stat().st_size > 10_000:
        return script

    cache_dir.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        GET_PIP_URL,
        headers={"User-Agent": "lab-cli/0.1 (+https://bootstrap.pypa.io/get-pip.py)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=GET_PIP_TIMEOUT) as response:
            data = response.read(10_000_000 + 1)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        outcome.warnings.append(
            f"无法下载 get-pip.py（{exc}）。venv 已创建但没有 pip；"
            f"可稍后下载 {GET_PIP_URL} 到本地，再执行 `.venv/bin/python <文件路径>` 安装"
        )
        return None
    if len(data) > 10_000_000:
        outcome.warnings.append("get-pip.py 异常过大，已放弃自动安装 pip")
        return None
    from .util import atomic_write_bytes

    atomic_write_bytes(script, data)
    return script


def _bootstrap_pip(venv: Path, outcome: EnvOutcome) -> bool:
    python = venv / "bin" / "python"
    if _python_has_pip(python):
        return True
    script = _cached_get_pip(outcome)
    if script is None:
        return False
    outcome.messages.append("venv 缺少 ensurepip，正在通过 get-pip.py 安装 pip")
    try:
        result = _run(
            [str(python), str(script), "--no-warn-script-location", "--disable-pip-version-check"],
            venv,
            timeout=240,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        outcome.warnings.append(f"get-pip.py 执行失败：{exc}")
        return False
    if result.returncode != 0 or not _python_has_pip(python):
        outcome.warnings.append(f"get-pip.py 执行失败：{_tail(result.stderr) or '未知错误'}")
        return False
    outcome.messages.append("pip 已安装到实验 venv 内")
    return True


def _setup_python(experiment_dir: Path, outcome: EnvOutcome) -> dict:
    venv = experiment_dir / ".venv"
    record = {"type": "python", "path": ".venv", "ok": False, "pip": False}
    try:
        result = _run([sys.executable, "-m", "venv", str(venv)], experiment_dir, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        outcome.warnings.append(f"创建 venv 失败：{exc}")
        return record

    if result.returncode != 0 or not (venv / "bin" / "python").exists():
        detail = _tail(result.stderr)
        if venv.exists():
            shutil.rmtree(venv, ignore_errors=True)
        outcome.messages.append(
            "完整 venv 创建失败（宿主机缺少 ensurepip），改用 --without-pip 重试"
        )
        try:
            result = _run(
                [sys.executable, "-m", "venv", "--without-pip", str(venv)],
                experiment_dir,
                timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            outcome.warnings.append(f"创建 venv 失败：{exc}")
            return record
        if result.returncode != 0 or not (venv / "bin" / "python").exists():
            outcome.warnings.append(
                f"创建 venv 失败：{_tail(result.stderr) or detail or '未知错误'}"
            )
            if venv.exists():
                shutil.rmtree(venv, ignore_errors=True)
            return record

    record["ok"] = True
    outcome.messages.append("已创建独立 venv: .venv/")
    record["pip"] = _bootstrap_pip(venv, outcome)
    return record


# ----------------------------------------------------------------------
# Node.js
# ----------------------------------------------------------------------
def _setup_node(experiment_dir: Path, outcome: EnvOutcome) -> dict:
    record = {"type": "node", "path": "node_modules", "ok": False}
    npm = shutil.which("npm")
    if npm is None:
        outcome.warnings.append("未找到 npm，已跳过 node_modules 安装")
        return record

    env = dict(os.environ)
    env.setdefault("npm_config_audit", "false")
    env.setdefault("npm_config_fund", "false")
    env.setdefault("npm_config_update_notifier", "false")
    outcome.messages.append("正在安装本地依赖（npm install）…")
    try:
        result = _run(
            [npm, "install", "--no-audit", "--no-fund", "--loglevel=error"],
            experiment_dir,
            timeout=600,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        outcome.warnings.append(f"npm install 失败：{exc}")
        return record
    if result.returncode != 0:
        outcome.warnings.append(
            f"npm install 失败（实验仍可用）：{_tail(result.stderr) or '未知错误'}"
        )
        return record

    modules = experiment_dir / "node_modules"
    if not modules.exists():
        modules.mkdir()
        outcome.messages.append("已创建本地 node_modules/（模板暂无依赖）")
    else:
        outcome.messages.append("依赖已安装到本地 node_modules/")
    record["ok"] = True
    return record


def environment_summary(record: dict | None) -> str:
    if not record:
        return "无（通用模板）"
    kind = record.get("type")
    if kind == "python":
        pip = "，pip 可用" if record.get("pip") else "，pip 不可用"
        state = "" if record.get("ok") else "，创建失败"
        return f"Python venv (.venv/){pip}{state}"
    if kind == "node":
        state = "" if record.get("ok") else "，安装失败"
        return f"Node.js 本地依赖 (node_modules/){state}"
    return "无"
