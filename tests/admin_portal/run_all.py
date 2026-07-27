"""Admin Portal E2E 骨架统一运行器。

依次运行 login / users / projects / stats 四个端到端用例,
任一失败即非零退出 (供 CI / 自动开发流水线使用)。

前置: 启动静态+代理服务
    python scripts/serve_admin_portal.py --port 4321
"""
import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))

TESTS = [
    "test_login_e2e.py",
    "test_users_projects_e2e.py",
    "test_stats_e2e.py",
]


def main():
    failed = []
    for t in TESTS:
        print(f"\n=== {t} ===")
        r = subprocess.run([sys.executable, os.path.join(HERE, t)])
        if r.returncode != 0:
            failed.append(t)
    if failed:
        print(f"\nFAILED: {failed}")
        sys.exit(1)
    print("\nALL PASS: admin-portal E2E 骨架全部通过 "
          "(login / users / projects / stats, 0 pageerror/console/.js+.css 404)")


if __name__ == "__main__":
    main()
