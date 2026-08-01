#!/usr/bin/env python3
"""Validate every plugin in this marketplace.

Run it locally before you push, and let CI run it on every pull request:

    python3 scripts/validate-manifests.py

The checks exist because each failure below is silent at runtime. A skill whose
frontmatter does not parse loads with no name and no description, so Claude never
triggers it. A marketplace entry whose version disagrees with plugin.json blocks the
release tag.
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEMVER = re.compile(r"^\d+\.\d+\.\d+([-+].+)?$")

errors = []
warnings = []


def error(message):
    errors.append(message)


def warn(message):
    warnings.append(message)


def load_json(path):
    """Return the parsed file, or None when it does not parse."""
    try:
        with open(path) as handle:
            return json.load(handle)
    except FileNotFoundError:
        error(f"{os.path.relpath(path, ROOT)}: file is missing")
    except json.JSONDecodeError as exc:
        error(f"{os.path.relpath(path, ROOT)}: JSON does not parse — {exc}")
    return None


def frontmatter(path):
    """Return the YAML frontmatter block of a markdown file, or None."""
    with open(path) as handle:
        text = handle.read()
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return match.group(1) if match else None


def check_frontmatter(path, kind):
    """A skill or agent with unparseable frontmatter loads with empty metadata."""
    import yaml

    rel = os.path.relpath(path, ROOT)
    block = frontmatter(path)
    if block is None:
        error(f"{rel}: {kind} has no YAML frontmatter")
        return
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        first = str(exc).split("\n")[0]
        error(f"{rel}: frontmatter does not parse — {first}")
        return
    if not isinstance(data, dict):
        error(f"{rel}: frontmatter is not a mapping")
        return
    for field in ("name", "description"):
        if not data.get(field):
            error(f"{rel}: {kind} frontmatter is missing '{field}'")


def check_links(path):
    """A relative link that points at a missing file is dead documentation."""
    rel = os.path.relpath(path, ROOT)
    with open(path) as handle:
        text = handle.read()
    for match in re.finditer(r"\]\(([^)#\s]+\.md)\)", text):
        target = match.group(1)
        if target.startswith(("http://", "https://")):
            continue
        resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
        if not os.path.exists(resolved):
            error(f"{rel}: link points at a missing file — {target}")


def main():
    marketplace_path = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
    marketplace = load_json(marketplace_path)
    if marketplace is None:
        report()
        return

    entries = marketplace.get("plugins", [])
    if not entries:
        error(".claude-plugin/marketplace.json: lists no plugins")

    for entry in entries:
        name = entry.get("name", "<unnamed>")
        source = entry.get("source")
        if not source:
            error(f"marketplace entry '{name}': has no source")
            continue

        plugin_dir = os.path.normpath(os.path.join(ROOT, source))
        if not os.path.isdir(plugin_dir):
            error(f"marketplace entry '{name}': source directory is missing — {source}")
            continue

        manifest = load_json(os.path.join(plugin_dir, ".claude-plugin", "plugin.json"))
        if manifest is None:
            continue

        # The release tag is built from these two versions, so they must agree.
        entry_version = entry.get("version")
        manifest_version = manifest.get("version")
        if not manifest_version:
            error(f"{name}: plugin.json has no version")
        elif not SEMVER.match(str(manifest_version)):
            error(f"{name}: plugin.json version is not semver — {manifest_version}")
        if entry_version and manifest_version and entry_version != manifest_version:
            error(
                f"{name}: marketplace entry says {entry_version}, "
                f"plugin.json says {manifest_version}"
            )
        if not entry_version:
            warn(f"{name}: marketplace entry has no version, so releases cannot be tagged")

        if manifest.get("name") != name:
            error(
                f"{name}: plugin.json name is '{manifest.get('name')}', "
                f"marketplace entry name is '{name}'"
            )

        for skill in sorted(glob_dir(plugin_dir, "skills", "SKILL.md")):
            check_frontmatter(skill, "skill")
        for agent in sorted(glob_dir(plugin_dir, "agents", None)):
            check_frontmatter(agent, "agent")

        for markdown in walk_markdown(plugin_dir):
            check_links(markdown)

    for markdown in walk_markdown(ROOT, top_only=True):
        check_links(markdown)

    report()


def glob_dir(plugin_dir, subdir, filename):
    """Return skill or agent markdown files under one plugin directory."""
    base = os.path.join(plugin_dir, subdir)
    found = []
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            if filename and name != filename:
                continue
            if not filename and not name.endswith(".md"):
                continue
            found.append(os.path.join(dirpath, name))
    return found


def walk_markdown(base, top_only=False):
    """Return markdown files under a directory, skipping .git."""
    if top_only:
        return [
            os.path.join(base, name)
            for name in os.listdir(base)
            if name.endswith(".md")
        ]
    found = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        found.extend(
            os.path.join(dirpath, name) for name in filenames if name.endswith(".md")
        )
    return found


def report():
    for message in warnings:
        print(f"warning: {message}")
    for message in errors:
        print(f"error: {message}")
    if errors:
        print(f"\n{len(errors)} error(s)")
        sys.exit(1)
    print(f"validation passed ({len(warnings)} warning(s))")


if __name__ == "__main__":
    main()
