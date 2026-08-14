#!/usr/bin/env python3
"""Fail if GitHub workflows bootstrap OS/toolchain deps.

Product CI must run inside ghcr.io/xgc-team/xgc2-images/xgc2-build-* .
Forbidden: apt/pip toolchain installs, actions/setup-*, rustup, cargo install,
npm -g, curl|sh, and stock ubuntu:/ros: build containers.

Allowed: repo lockfile installs (pnpm install, npm ci, yarn install, bun install,
uv sync, go test) inside an image that already has the toolchain.
Allowed apt: installing a locally built .deb under test, and apt-get -f.

xgc2-images Dockerfiles and scripts/build/ are not scanned. This product
gate scans .github/workflows/*.yml and .xgc2/scripts/*.sh. Extra packages
belong in the xgc2-images repository, not in product CI.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

FORBIDDEN_USES = (
    "actions/setup-node",
    "actions/setup-python",
    "actions/setup-go",
    "oven-sh/setup-bun",
    "astral-sh/setup-uv",
    "pnpm/action-setup",
    "bufbuild/buf-setup-action",
    "dtolnay/rust-toolchain",
    "actions-rs/toolchain",
    "rust-lang/github-actions",
)

FORBIDDEN_CONTAINER_RE = re.compile(
    r"""(?x)
    (?:
        ubuntu:(?:latest|18\.04|20\.04|22\.04|24\.04|bionic|focal|jammy|noble)
        | ros:(?:melodic|noetic|humble|jazzy|foxy)
        | althack/ros2
        | ghcr\.io/sloretz/ros
        | osrf/ros
    )
    """,
    re.IGNORECASE,
)

ALLOWED_IMAGE_RE = re.compile(
    r"ghcr\.io/xgc-team/xgc2-images/|quay\.io/skopeo/stable",
    re.IGNORECASE,
)

HOST_B_RE = re.compile(
    r"\[self-hosted[^\]]*(?:xgc-team-b|\bxgc\b[^\]]*org[^\]]*docker)",
    re.IGNORECASE,
)

PIP_RE = re.compile(
    r"(?:^|[;&|]\s*|\s)(?:python3?\s+-m\s+)?pip(?:3)?\s+install\b",
    re.IGNORECASE,
)
NPM_GLOBAL_RE = re.compile(r"npm\s+(?:i|install)\s+(?:-[^\s]*\s+)*-g\b", re.IGNORECASE)
CARGO_INSTALL_RE = re.compile(r"\bcargo\s+install\b", re.IGNORECASE)
RUSTUP_RE = re.compile(r"sh\.rustup\.rs|\brustup\s+", re.IGNORECASE)
CURL_SH_RE = re.compile(r"curl\b[^|\n]*\|\s*(?:sudo\s+)?(?:bash|sh)\b", re.IGNORECASE)
APT_INSTALL_RE = re.compile(
    r"\b(?:apt-get|apt)\s+(?:-[^\s]+\s+)*install\b",
    re.IGNORECASE,
)


def logical_lines(text: str) -> list[tuple[int, str]]:
    raw = text.splitlines()
    out: list[tuple[int, str]] = []
    buf = ""
    start = 1
    for i, line in enumerate(raw, 1):
        stripped = line.rstrip()
        if not buf:
            start = i
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        buf += stripped
        out.append((start, buf))
        buf = ""
    if buf:
        out.append((start, buf))
    return out


def strip_comment(line: str) -> str:
    in_s = in_d = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d:
            return line[:i]
    return line


def _apt_packages(line: str) -> list[str]:
    skip_exact = {
        "sudo",
        "apt-get",
        "apt",
        "install",
        "update",
        "clean",
        "autoclean",
        "autoremove",
        "&&",
        "||",
        "true",
        "fi",
        "then",
        "do",
        "done",
        ">/dev/null",
        "2>/dev/null",
    }
    pkgs: list[str] = []
    for tok in line.replace(",", " ").split():
        low = tok.lower()
        if low in skip_exact or low.startswith("-") or low.endswith(":") or "acquire::" in low:
            continue
        pkgs.append(tok)
    return pkgs


def apt_install_allowed(line: str) -> bool:
    pkgs = _apt_packages(line)
    if not pkgs:
        return True
    return all(
        p.startswith("./")
        or p.endswith(".deb")
        or "*.deb" in p
        or "/debs/" in p
        or p.startswith("debs/")
        for p in pkgs
    )


def scan_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    findings: list[str] = []
    rel = str(path)
    for lineno, raw in logical_lines(text):
        line = strip_comment(raw).strip()
        if not line:
            continue
        for uses in FORBIDDEN_USES:
            if uses in line:
                findings.append(f"{rel}:{lineno}: forbidden toolchain action {uses}")
        if HOST_B_RE.search(line):
            findings.append(
                f"{rel}:{lineno}: public CI must not use Host B self-hosted runners"
            )
        if PIP_RE.search(line):
            findings.append(f"{rel}:{lineno}: pip/python -m pip is toolchain bootstrap")
        if NPM_GLOBAL_RE.search(line):
            findings.append(f"{rel}:{lineno}: npm install -g is toolchain bootstrap")
        if CARGO_INSTALL_RE.search(line):
            findings.append(f"{rel}:{lineno}: cargo install is toolchain bootstrap")
        if RUSTUP_RE.search(line):
            findings.append(f"{rel}:{lineno}: rustup is toolchain bootstrap")
        if CURL_SH_RE.search(line):
            findings.append(f"{rel}:{lineno}: curl|sh toolchain bootstrap")
        if APT_INSTALL_RE.search(line) and not apt_install_allowed(line):
            findings.append(f"{rel}:{lineno}: apt install of distro/toolchain packages")
        if FORBIDDEN_CONTAINER_RE.search(line) and not ALLOWED_IMAGE_RE.search(line):
            if re.search(r"\b(?:container:|docker\s+run|ubuntu_image:|image:)\b", line) or "ubuntu:" in line or "ros:" in line:
                findings.append(
                    f"{rel}:{lineno}: stock ubuntu:/ros: image; use ghcr.io/xgc-team/xgc2-images/xgc2-build-*"
                )
    return findings


def workflow_files(root: Path) -> list[Path]:
    wf = root / ".github" / "workflows"
    if not wf.is_dir():
        return []
    files = sorted(p for p in wf.iterdir() if p.suffix in {".yml", ".yaml"} and p.is_file())
    return files


def product_ci_scripts(root: Path) -> list[Path]:
    d = root / ".xgc2" / "scripts"
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix == ".sh" and p.is_file())


def scan_product_script(path: Path) -> list[str]:
    """Comments are ignored. command -v apt-get is not an install."""
    text = path.read_text(encoding="utf-8", errors="replace")
    findings: list[str] = []
    rel = str(path)
    for lineno, raw in logical_lines(text):
        line = strip_comment(raw).strip()
        if not line:
            continue
        if PIP_RE.search(line):
            findings.append(f"{rel}:{lineno}: pip/python -m pip is toolchain bootstrap")
        if APT_INSTALL_RE.search(line) and not apt_install_allowed(line):
            findings.append(f"{rel}:{lineno}: apt install of distro/toolchain packages")
    return findings


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    files = workflow_files(root)
    scripts = product_ci_scripts(root)
    if not files and not scripts:
        print(f"no GitHub workflows or product CI scripts under {root}; nothing to check")
        return 0
    findings: list[str] = []
    for path in files:
        findings.extend(scan_file(path))
    for path in scripts:
        findings.extend(scan_product_script(path))
    if findings:
        print("CI bootstrap gate failed. Use an XGC2 build image and delete these steps:", file=sys.stderr)
        print(
            "  container: ghcr.io/xgc-team/xgc2-images/xgc2-build-<ubuntu>-<layer>[-<ros>]:1.0.0",
            file=sys.stderr,
        )
        print(
            "Allowed: pnpm install / npm ci / yarn install / bun install / uv sync of the repo lockfile.",
            file=sys.stderr,
        )
        print("Forbidden: apt, pip, setup-node/python/go/uv/bun, rustup, npm -g, curl|sh.", file=sys.stderr)
        print("Add missing packages in xgc2-images, then rerun this product CI.", file=sys.stderr)
        for item in findings:
            print(item, file=sys.stderr)
        return 1
    print(
        f"CI bootstrap gate passed ({len(files)} workflow file(s), {len(scripts)} product script(s))."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
