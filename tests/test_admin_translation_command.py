from cogs.admin import Admin

ADMIN_ROLE_ID = 1020638168237740042


def get_command(name):
    for cmd in Admin.__cog_commands__:
        if cmd.name == name:
            return cmd
    raise AssertionError(f"command {name!r} not found on Admin cog")


def test_set_translation_engine_command_exists():
    cmd = get_command("set_translation_engine")
    assert cmd is not None


def test_set_translation_engine_requires_admin_role_like_other_admin_commands():
    reference = get_command("ban")  # an existing, known admin-only command
    target = get_command("set_translation_engine")

    def role_ids(command):
        ids = set()
        for check in command.checks:
            # commands.has_role wraps a closure; inspect its cell for the
            # role id it was constructed with.
            for cell in getattr(check, "__closure__", None) or ():
                if isinstance(cell.cell_contents, int):
                    ids.add(cell.cell_contents)
        return ids

    assert role_ids(target) == role_ids(reference) == {ADMIN_ROLE_ID}
