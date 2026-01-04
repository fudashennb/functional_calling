#!/usr/bin/env python3
"""
批量将 tts_packages.zip 文件拷贝到所有车辆的 /home/root/。
在没有 sshpass/expect 命令时，使用 Python pexpect 自动输入密码。

依赖：
  pip install pexpect

用法：
  python3 scp_packages.py [tts_packages.zip]
    tts_packages.zip 省略时默认使用脚本同目录下的 tts_packages.zip 文件。
    文件会被拷贝到目标车辆的 /home/root/ 文件夹。

  默认读取脚本同目录的 targets.csv（如不存在会自动生成）作为待升级列表，
  每成功升级一台会立即从 csv 中移除一行，断网/重启后可继续剩余目标。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import pexpect
except ImportError:
    print("Missing dependency: pexpect (pip install pexpect)", file=sys.stderr)
    sys.exit(1)

# 车辆列表
GAOJIA_IPS = [
    ("201", "10.62.237.75"),
    ("202", "10.62.237.199"),
    ("203", "10.62.237.217"),
    ("204", "10.62.237.130"),
    ("205", "10.62.237.109"),
    ("206", "10.62.237.165"),
    ("207", "10.62.237.137"),
    ("208", "10.62.237.192"),
    ("209", "10.62.237.57"),
    ("210", "10.62.237.76"),
    ("211", "10.62.237.125"),
    ("212", "10.62.237.30"),
    ("213", "10.62.237.200"),
]

DUODUO_IPS = [
    ("1301", "10.62.237.133"),
    ("1302", "10.62.237.64"),
    # ("1303", "10.62.237.114"),
    ("1304", "10.62.237.86"),
    ("1305", "10.62.237.92"),
    ("1306", "10.62.237.242"),
    ("1307", "10.62.237.221"),
    ("1308", "10.62.237.58"),
    ("1309", "10.62.237.245"),
    ("1310", "10.62.237.191"),
    ("1311", "10.62.237.33"),
    ("1312", "10.62.237.167"),
    ("1313", "10.62.237.112"),
    ("1314", "10.62.237.129"),
    ("1315", "10.62.237.60"),
    ("1316", "10.62.237.93"),
    ("1317", "10.62.237.230"),
    ("1318", "10.62.237.36"),
    ("1319", "10.62.237.139"),
]


SSH_USER = "root"
SSH_PASS = "SRpasswd@2017"
REMOTE_DIR = "/home/root"
REMOTE_FILENAME = "tts_packages.zip"
VERBOSE = os.getenv("SCP_VERBOSE", "0") not in ("0", "", "false", "False")
CONNECT_TIMEOUT = int(os.getenv("SCP_CONNECT_TIMEOUT", "8"))
EXPECT_TIMEOUT = int(os.getenv("SCP_EXPECT_TIMEOUT", "200"))  # seconds


def ensure_file_exists(path: Path) -> Path:
    if not path.is_file():
        print(f"Packages zip file not found: {path}", file=sys.stderr)
        sys.exit(1)
    return path


def _file_stats(path: Path) -> int:
    """Return file size in bytes."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _human_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f}{unit}"
        size /= 1024


def _load_failed_targets(file_path: Path) -> list[tuple[str, str]]:
    """Load label,ip pairs from a failed_targets.txt-like file."""
    pairs: list[tuple[str, str]] = []
    if not file_path.is_file():
        print(f"Failed list not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    with file_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                print(f"Skip invalid line: {line}", file=sys.stderr)
                continue
            label, ip = parts[0], parts[1]
            pairs.append((label, ip))
    if not pairs:
        print(f"No targets loaded from {file_path}", file=sys.stderr)
        sys.exit(1)
    return pairs


def _load_targets_from_csv(
    csv_path: Path,
) -> tuple[list[tuple[str, str]], str | None]:
    """
    Load label,ip pairs from a CSV file.

    支持可选表头(label,ip,...)；忽略空行和以 # 开头的注释行。
    返回值包含待升级列表和原始表头（如果存在）。
    """
    if not csv_path.is_file():
        print(f"Target csv not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    targets: list[tuple[str, str]] = []
    header: str | None = None

    with csv_path.open(encoding="utf-8") as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if idx == 0 and "label" in line.lower() and "ip" in line.lower():
                header = line
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                print(f"Skip invalid csv line: {line}", file=sys.stderr)
                continue
            targets.append((parts[0], parts[1]))

    if not targets:
        print(f"No targets loaded from {csv_path}", file=sys.stderr)
        sys.exit(1)
    return targets, header


def _write_targets_to_csv(csv_path: Path, targets: list[tuple[str, str]], header: str | None) -> None:
    """Rewrite csv with remaining targets so中断可续传。"""
    with csv_path.open("w", encoding="utf-8") as f:
        if header:
            f.write(header.rstrip() + "\n")
        for label, ip in targets:
            f.write(f"{label},{ip}\n")


def _ensure_default_target_csv(csv_path: Path) -> None:
    """
    Create a default targets.csv when it does not exist so断网重传时能自动续传。

    只在默认路径缺失时生成，不覆盖用户自定义 csv。
    """
    if csv_path.is_file():
        return
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8") as f:
        f.write("label,ip\n")
        for label, ip in GAOJIA_IPS + DUODUO_IPS:
            f.write(f"{label},{ip}\n")
    print(f"Created default target csv at {csv_path}")


def copy_one(label: str, ip: str, zip_file: Path) -> tuple[bool, str]:
    total_bytes = _file_stats(zip_file)
    src = zip_file.resolve()
    dst = f"{SSH_USER}@{ip}:{REMOTE_DIR}/{REMOTE_FILENAME}"
    scp_cmd = [
        "scp",
        "-v" if VERBOSE else "",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        f"ConnectTimeout={CONNECT_TIMEOUT}",
        str(zip_file),
        f"{SSH_USER}@{ip}:{REMOTE_DIR}/{REMOTE_FILENAME}",
    ]
    scp_cmd = [c for c in scp_cmd if c]  # drop empty flag when not verbose

    print(
        f"[{label}][{ip}] Copying {src} -> {dst} "
        f"({_human_size(total_bytes)})..."
    )
    reason = ""
    try:
        child = pexpect.spawn(" ".join(scp_cmd), timeout=EXPECT_TIMEOUT, encoding="utf-8")
        # Stream SCP output to console (progress, errors); avoid echoing sent input.
        child.logfile_read = sys.stdout
        while True:
            idx = child.expect(
                [
                    "Are you sure you want to continue connecting (yes/no/[fingerprint])?",
                    "password:",
                    pexpect.EOF,
                    pexpect.TIMEOUT,
                ]
            )
            if idx == 0:
                child.sendline("yes")
                continue
            if idx == 1:
                child.sendline(SSH_PASS)
                continue
            if idx == 2:
                break
            if idx == 3:
                reason = "timeout/no response"
                break
        child.close()
        if child.exitstatus == 0:
            print(f"[{label}][{ip}] Success")
            return True, ""
        if not reason:
            reason = f"scp exit={child.exitstatus}"
        print(f"[{label}][{ip}] Failed ({reason})", file=sys.stderr)
    except Exception as exc:  # pylint: disable=broad-except
        reason = str(exc)
        print(f"[{label}][{ip}] Failed: {reason}", file=sys.stderr)
    return False, reason


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    default_target_csv = script_dir / "targets.csv"
    default_zip_file = script_dir / "tts_packages.zip"
    zip_file = default_zip_file
    failed_list: Path | None = None
    target_csv: Path | None = None

    if len(sys.argv) > 1:
        arg1 = Path(sys.argv[1])
        if arg1.is_file() and arg1.suffix == ".txt":
            failed_list = arg1
            if len(sys.argv) > 2:
                zip_file = Path(sys.argv[2])
        elif arg1.is_file() and arg1.suffix == ".csv":
            target_csv = arg1
            if len(sys.argv) > 2:
                zip_file = Path(sys.argv[2])
        else:
            zip_file = arg1
            if len(sys.argv) > 2:
                candidate = Path(sys.argv[2])
                if candidate.is_file() and candidate.suffix == ".txt":
                    failed_list = candidate
                if candidate.is_file() and candidate.suffix == ".csv":
                    target_csv = candidate
    if target_csv is None and failed_list is None:
        # 默认使用脚本目录下的 targets.csv，缺失时自动生成
        _ensure_default_target_csv(default_target_csv)
        target_csv = default_target_csv

    zip_file = ensure_file_exists(zip_file)

    failed = []
    csv_header: str | None = None
    targets: list[tuple[str, str]]
    if target_csv:
        targets, csv_header = _load_targets_from_csv(target_csv)
    else:
        targets = _load_failed_targets(failed_list) if failed_list else GAOJIA_IPS + DUODUO_IPS

    for label, ip in list(targets):  # copy of list for safe in-loop mutation
        ok, reason = copy_one(label, ip, zip_file)
        if ok:
            if target_csv:
                targets = [(l, p) for l, p in targets if not (l == label and p == ip)]
                _write_targets_to_csv(target_csv, targets, csv_header)
        else:
            failed.append((label, ip, reason))

    if failed:
        print("Failed targets (label:ip -> reason):")
        for label, ip, reason in failed:
            print(f"  {label}:{ip} -> {reason or 'unknown'}")
        fail_log = script_dir / "failed_targets.txt"
        with fail_log.open("w", encoding="utf-8") as f:
            for label, ip, reason in failed:
                f.write(f"{label},{ip},{reason or 'unknown'}\n")
        print(f"Saved failed targets to {fail_log}")
        return 1

    print("All copy tasks finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

