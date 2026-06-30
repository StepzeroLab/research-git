"""Guard against the global guidance block drifting from the real CLI/skills.

The block hard-codes `rgit ...` commands and skill names. Without a test, a
flag rename would silently teach every installed agent a command that no longer
exists. These tests fail loudly if the prose and the code disagree.
"""
import re
import shlex

import pytest

from rgit import agent_guidance, cli, installer

_BLOCK = agent_guidance.render_global_block()


def _rgit_commands(block: str) -> list[str]:
    # `rgit <args>` in backticks; the trailing space excludes `rgit-capture` etc.
    return re.findall(r"`rgit (.+?)`", block)


def test_block_references_some_rgit_commands():
    assert _rgit_commands(_BLOCK), "no `rgit ...` commands found in the block"


@pytest.mark.parametrize("cmd", _rgit_commands(_BLOCK))
def test_block_rgit_commands_parse(cmd):
    # parse_args raises SystemExit on an unknown subcommand/flag.
    cli.build_parser().parse_args(shlex.split(cmd))


def test_block_skill_names_exist_in_plugin():
    names = set(re.findall(r"`(rgit-[a-z-]+)`", _BLOCK))
    assert names
    skills_root = installer.plugin_dir() / "skills"
    available = {p.name for p in skills_root.iterdir() if p.is_dir()}
    missing = names - available
    assert not missing, f"block references unknown skills: {missing}"
