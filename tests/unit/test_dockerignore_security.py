"""`.dockerignore` 安全回归测试（P0 整改 B-A3 / Story 291 / Epic 145）。

背景：
    Dockerfile:24 `COPY . .` 会把构建上下文里所有未被 `.dockerignore`
    排除的文件烤进镜像层。`.env` 含生产密钥（MINIMAX_API_KEY / amqp 凭据），
    一旦进入镜像，任何拉取者可经 `docker history --no-trunc` 还原层内容。

本测试用静态方式校验 `.dockerignore` 排除 `.env*`，无需 docker，CI 友好。
真正的镜像层审计留给 CI 加固步骤（`docker run --rm test sh -c "test ! -f .env"`）。
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest

# 仓库根：tests/unit/xx.py -> 上两级
REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERIGNORE_PATH = REPO_ROOT / ".dockerignore"
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"


def _read_dockerignore_patterns() -> list[str]:
    """读取 .dockerignore，返回有效模式列表（去注释、去空行、保留否定式 `!`）。"""
    assert DOCKERIGNORE_PATH.is_file(), ".dockerignore 不存在"
    text = DOCKERIGNORE_PATH.read_text(encoding="utf-8")
    patterns: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _is_ignored(path: str, patterns: list[str]) -> bool:
    """模拟 dockerignore 匹配语义：后面匹配的模式覆盖前面（! 取反）。"""
    ignored = False
    for pat in patterns:
        negate = pat.startswith("!")
        pat_clean = pat[1:] if negate else pat
        if fnmatch.fnmatch(path, pat_clean):
            ignored = not negate
    return ignored


class TestDockerignoreEnvExclusion:
    """B-A3: .env* 必须被 .dockerignore 排除。"""

    @pytest.fixture(scope="class")
    @classmethod
    def patterns(cls) -> list[str]:
        return _read_dockerignore_patterns()

    def test_env_file_ignored(self, patterns: list[str]) -> None:
        """.env（含真实密钥）必须被排除。"""
        assert _is_ignored(".env", patterns), (
            ".dockerignore 未排除 .env —— Dockerfile `COPY . .` 会把含密钥的 "
            ".env 烤进镜像层（P0-关键 B-A3）"
        )

    @pytest.mark.parametrize(
        "env_file",
        [
            ".env.local",
            ".env.production",
            ".env.staging",
            ".env.development",
        ],
    )
    def test_env_variants_ignored(self, patterns: list[str], env_file: str) -> None:
        """常见 .env 变体（含密钥的本地/生产/预置）也应被排除。"""
        assert _is_ignored(env_file, patterns), (
            f".dockerignore 未排除 {env_file} —— .env.* 通配未覆盖"
        )

    def test_dockerfile_copy_all_present(self) -> None:
        """确认 Dockerfile 仍含 `COPY . .`（修复前提：上下文全量拷贝靠 .dockerignore 收口）。"""
        text = DOCKERFILE_PATH.read_text(encoding="utf-8")
        assert "COPY . ." in text, "Dockerfile 结构已变，请复查 B-A3 修复前提"


class TestNoEnvInGitTracking:
    """B-A3 关联：.env 不得被 git 跟踪（防 git 历史泄漏，与镜像层互补）。"""

    def test_env_not_tracked(self) -> None:
        from subprocess import run

        res = run(
            ["git", "ls-files", ".env"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        tracked = res.stdout.strip()
        assert tracked == "", (
            f".env 被 git 跟踪：{tracked!r} —— 密钥已进 git 历史，需立即轮换并清理历史"
        )
