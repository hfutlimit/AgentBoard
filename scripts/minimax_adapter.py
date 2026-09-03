"""minimax-cli 无头适配器（MiniMax Code 生态 CLI → Worker 无头协议）。

Worker 的 SubprocessProcessorInvoker 协议：
    stdin 喂 prompt → stdout 回读决策 JSON（{"action": ...}）

minimax-cli（npm/bun 安装，AGPL-3.0）提供无头模式 ``minimax -p "<prompt>"``，
但它从**命令行参数**接收 prompt（非 stdin），且输出为对话文本（agent 会按
prompt 指示在回复中打印 JSON 决策，由 worker 的 extract_decision_json 提取）。
本适配器把两者桥接起来：stdin → 子进程参数 → stdout 透传。

**能力边界（重要）**：
- minimax-cli 当前版本无 MCP 集成，agent 无法经 AgentBoard MCP 调用
  set_status / submit_task_for_review 等写库工具 → 仅适用于**不需要 MCP 的
  决策轮**（Proposal 澄清的 ask/finalize/fail；Story/Ticket 执行轮需 MCP，
  请使用 codebuddy 通道，见 docs/minimax-code-integration.md）。
- Windows 命令行长度上限约 32K，prompt 超限会报错（Story 全量重放场景
  上下文较大，建议该类任务走 codebuddy 通道）。

环境变量：
    MINIMAX_CLI_PATH    minimax-cli 命令（可多段，如 node + 入口 js；默认 "minimax"）
    MINIMAX_MODEL       模型名（minimax-pro / minimax-fast-1 等，默认不传）
    MINIMAX_DIRECTORY   工作目录（默认当前目录）
    MINIMAX_TIMEOUT     子进程超时秒数（默认 600）

Windows 说明（与 codebuddy 同款坑）：
- npm 全局安装的 bin 是 `minimax.cmd` 包装，CreateProcess 不认 → 适配器自动
  `cmd /c` 包装；若提示词含双引号建议改用 node 显式执行：
  MINIMAX_CLI_PATH="node <npm-global>/node_modules/minimax-cli/dist/cli.js"。
"""
import json
import os
import shlex
import shutil
import subprocess
import sys

MAX_ARG_LEN = 20000  # 预留安全余量（Windows 32K 上限）


def _split_cmd(cmd: str) -> list[str]:
    """拆命令模板为 argv（Windows 兼容：posix=False + 去成对引号，同 worker.split_command）。"""
    if os.name != "nt":
        return shlex.split(cmd)
    out = []
    for tok in shlex.split(cmd, posix=False):
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        out.append(tok)
    return out


def _build_cmd(prompt: str) -> list[str]:
    cli = os.environ.get("MINIMAX_CLI_PATH", "minimax")
    argv = _split_cmd(cli) or ["minimax"]
    if os.name == "nt" and len(argv) == 1:
        resolved = shutil.which(argv[0])
        if resolved and resolved.lower().endswith((".cmd", ".bat")):
            # npm 全局 .cmd 包装：CreateProcess 不认，走 cmd /c
            return ["cmd", "/c", resolved, "-p", prompt]
    return argv + ["-p", prompt]


def _cmd(argv: list[str]) -> list[str]:
    model = os.environ.get("MINIMAX_MODEL") or ""
    if model:
        argv += ["-m", model]
    return argv


def json_fail(msg: str) -> str:
    return json.dumps({"action": "fail", "error": msg})


def _assistant_text(stdout: str) -> str:
    """minimax-cli 输出 JSONL（每行一个 role 对象），决策 JSON 嵌套在 assistant
    content 字符串内（顶层无 action 键，worker 的括号配对扫描提取不到）。

    这里把 assistant 消息 content 重组为纯文本输出：worker 的
    extract_decision_json 即可扫描到顶层决策 JSON；工具结果行忽略。
    """
    lines: list[str] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            lines.append(line)  # 非 JSONL 行原样保留（兼容其它输出）
            continue
        if not isinstance(obj, dict):
            continue
        role = obj.get("role")
        if role == "assistant":
            content = obj.get("content")
            if isinstance(content, str):
                lines.append(content)
            elif isinstance(content, list):  # OpenAI 风格 content 数组
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        lines.append(str(part.get("text", "")))
        elif role == "tool":
            continue  # 工具结果忽略
        # user/system 行忽略（决策只来自 assistant）
    return "\n".join(lines)


def main() -> int:
    prompt = sys.stdin.read()
    if not prompt.strip():
        print(json_fail("stdin 为空（worker 未喂入 prompt）"))
        return 0
    if len(prompt) > MAX_ARG_LEN:
        print(json_fail(
            f"prompt 过长（{len(prompt)} > {MAX_ARG_LEN}），minimax 通道不适用，"
            "请使用 codebuddy 通道"))
        return 0
    # minimax-cli v1.0.1 的 -p 只取 prompt 第一行（多行被截断，实测限制）：
    # 把真实换行转义为字面 \n，保证完整协议/提案内容一次性送达模型。
    prompt = prompt.replace("\n", "\\n")
    cmd = _cmd(_build_cmd(prompt))
    directory = os.environ.get("MINIMAX_DIRECTORY") or None
    timeout = float(os.environ.get("MINIMAX_TIMEOUT", "600"))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=directory, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        print(json_fail(f"minimax-cli 调用超时（>{timeout}s）"))
        return 0
    except (FileNotFoundError, OSError) as e:
        print(json_fail(
            f"minimax-cli 无法启动：{e}（请先 npm install -g minimax-cli 并配置 API Key；"
            "Windows 下若报 WinError 193，请用 node 显式执行入口 js）"))
        return 0
    if proc.stdout:
        sys.stdout.write(_assistant_text(proc.stdout))  # JSONL → assistant 纯文本
    if proc.returncode != 0 and not proc.stdout:
        sys.stdout.write(json_fail(f"minimax-cli 退出码 {proc.returncode}：{(proc.stderr or '')[:300]}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
