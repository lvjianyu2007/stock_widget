"""把实现计划里某一个任务的全文抽成一个简报文件。

移植自 subagent-driven-development 技能的 scripts/task-brief（bash 版），
因为 Windows 环境里没有 bash。用法：

    python task_brief.py <计划文件> <任务号> [输出文件]

默认输出到本脚本同目录的 task-<N>-brief.md。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HEADING = re.compile(r"^#+[ \t]*(?:Task|任务)[ \t]*(\d+)")


def extract(plan_path: Path, number: int) -> str:
    lines = []
    in_fence = False
    in_task = False
    for line in plan_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        if not in_fence:
            match = HEADING.match(line)
            if match:
                in_task = int(match.group(1)) == number
        if in_task:
            lines.append(line)
    return "\n".join(lines) + "\n" if lines else ""


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print("usage: python task_brief.py PLAN_FILE TASK_NUMBER [OUTFILE]", file=sys.stderr)
        return 2
    plan = Path(argv[1])
    number = int(argv[2])
    if not plan.is_file():
        print(f"no such plan file: {plan}", file=sys.stderr)
        return 2
    out = Path(argv[3]) if len(argv) == 4 else Path(__file__).parent / f"task-{number}-brief.md"
    text = extract(plan, number)
    if not text.strip():
        print(f"task {number} not found in {plan}", file=sys.stderr)
        return 3
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}: {len(text.splitlines())} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
