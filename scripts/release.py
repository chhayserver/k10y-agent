"""Release helpers. Every place that names the version, the changelog and the installers stay in step.

    python scripts/release.py bump 2.8.0     # set the version everywhere, Unreleased -> 2.8.0, regenerate installers
    python scripts/release.py check 2.8.0    # fail unless everything says 2.8.0 and the installers are current
    python scripts/release.py notes 2.8.0    # the changelog section of 2.8.0, for the GitHub release

`make release VERSION=2.8.0` runs the bump; pushing the tag v2.8.0 makes GitHub Actions build, publish and release.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "website" / "changelog.md"
LATEST = ' <Badge type="tip" text="latest" />'
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

# (file, regex with one group around the version)
VERSION_SPOTS = [
    ("pyproject.toml", r'(?m)^version = "([^"]+)"'),
    ("src/k10y_agent/__init__.py", r'__version__ = "([^"]+)"'),
    ("Makefile", r"(?m)^TAG\s+\?= (\S+)"),
    ("docker-compose.yml", r"k10y/k10y-agent:([0-9][^}\s]*)"),
    ("deploy/k8s/agent.yaml", r"k10y/k10y-agent:(\S+)"),
]
PACKAGE_JSONS = ["web/package.json"]
PACKAGE_LOCKS = ["web/package-lock.json"]


def current_versions() -> dict:
    found = {}
    for path, rx in VERSION_SPOTS:
        m = re.search(rx, (ROOT / path).read_text())
        found[path] = m.group(1) if m else None
    for path in PACKAGE_JSONS:
        found[path] = json.loads((ROOT / path).read_text())["version"]
    for path in PACKAGE_LOCKS:
        lock = json.loads((ROOT / path).read_text())
        found[path] = lock["version"]
        found[path + " (packages)"] = lock["packages"][""]["version"]
    return found


def set_version(version: str) -> None:
    for path, rx in VERSION_SPOTS:
        p = ROOT / path
        text = p.read_text()
        new, n = re.subn(rx, lambda m: m.group(0).replace(m.group(1), version), text)
        if not n:
            sys.exit(f"{path}: version not found")
        p.write_text(new)
    for path in PACKAGE_JSONS:
        p = ROOT / path
        p.write_text(re.sub(r'("version":\s*")[^"]+(")', rf"\g<1>{version}\g<2>", p.read_text(), count=1))
    for path in PACKAGE_LOCKS:
        p = ROOT / path
        lock = json.loads(p.read_text())
        lock["version"] = version
        lock["packages"][""]["version"] = version
        p.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n")


def bump_changelog(version: str) -> None:
    text = CHANGELOG.read_text()
    if f"\n## {version}" in text:
        sys.exit(f"the changelog already has {version}")
    if "\n## Unreleased\n" not in text:
        sys.exit("the changelog has no '## Unreleased' section to release")
    text = text.replace(LATEST, "", 1)
    CHANGELOG.write_text(text.replace("\n## Unreleased\n", f"\n## {version}{LATEST}\n", 1))


def notes(version: str) -> str:
    text = CHANGELOG.read_text()
    m = re.search(rf"\n## {re.escape(version)}[^\n]*\n(.*?)(?=\n## |\Z)", text, re.S)
    if not m or not m.group(1).strip():
        sys.exit(f"no changelog section for {version}")
    return (m.group(1).strip() + f"\n\nImage: `docker.io/k10y/k10y-agent:{version}` (amd64, arm64)\n")


GENERATED = [("deploy/install", "install*.yaml", "gen_install.py"), ("deploy/helm/k10y-agent", "**/*", "gen_chart.py"),
             ("deploy/helm/k10y-hub", "Chart.yaml", "gen_chart.py")]


def generate(version: str) -> None:
    for script in dict.fromkeys(script for _, _, script in GENERATED):
        subprocess.run([sys.executable, str(ROOT / "scripts" / script), version], check=True, capture_output=True)


def installers_current(version: str) -> bool:
    """Installers and the Helm chart, regenerated for `version`, are what's committed."""
    def snapshot():
        return {str(p): p.read_text() for d, pat, _ in GENERATED for p in (ROOT / d).glob(pat) if p.is_file()}
    before = snapshot()
    generate(version)
    return before == snapshot()


def check(version: str) -> None:
    problems = [f"{path} says {v}" for path, v in current_versions().items() if v != version]
    if f"\n## {version}" not in CHANGELOG.read_text():
        problems.append(f"website/changelog.md has no '## {version}' section")
    if not installers_current(version):
        problems.append("deploy/install/*.yaml or the Helm chart were out of date (regenerated now; commit them)")
    if problems:
        sys.exit("release check failed:\n  " + "\n  ".join(problems))
    print(f"release {version}: versions, changelog and installers agree")


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("bump", "check", "notes"):
        sys.exit(__doc__)
    cmd, version = sys.argv[1], sys.argv[2].removeprefix("v")
    if not SEMVER.match(version):
        sys.exit(f"not a version: {version}")
    if cmd == "bump":
        set_version(version)
        bump_changelog(version)
        generate(version)
        check(version)
    elif cmd == "check":
        check(version)
    else:
        print(notes(version), end="")


if __name__ == "__main__":
    main()
