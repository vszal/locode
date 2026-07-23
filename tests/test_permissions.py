from locode.config import PermissionsConfig
from locode.permissions import AUTO, ASK, DENY, PermissionPolicy


def make(**over):
    perms = PermissionsConfig()
    for k, v in over.items():
        perms.tools[k] = v
    return perms


def test_read_is_auto_write_is_ask():
    pol = PermissionPolicy(PermissionsConfig())
    assert pol.resolve("read_file", {"path": "a"}, "/work") == AUTO
    assert pol.resolve("write_file", {"path": "a"}, "/work") == ASK


def test_auto_allow_under_sandbox():
    pol = PermissionPolicy(PermissionsConfig())  # auto_allow_under=["./sandbox"]
    assert pol.resolve("write_file", {"path": "sandbox/out.md"}, "/work") == AUTO
    assert pol.resolve("edit_file", {"path": "sandbox/x"}, "/work") == AUTO


def test_append_file_is_path_scoped_like_the_other_mutators():
    # append_file takes the same "path" arg as write_file, so it must land in
    # _PATH_MUTATING or deny_paths would silently not apply to it.
    pol = PermissionPolicy(PermissionsConfig())
    assert pol.resolve("append_file", {"path": "~/.ssh/authorized_keys"},
                       "/work") == DENY
    assert pol.resolve("append_file", {"path": "sandbox/out.md"},
                       "/work") == AUTO


def test_deny_paths_hard_block():
    pol = PermissionPolicy(PermissionsConfig())
    d = pol.resolve("write_file", {"path": "~/.ssh/authorized_keys"}, "/work")
    assert d == DENY


def test_deny_beats_yolo():
    pol = PermissionPolicy(PermissionsConfig(), yolo=True)
    assert pol.resolve("write_file", {"path": "~/.ssh/known_hosts"}, "/work") == DENY


def test_yolo_flips_ask_to_auto():
    pol = PermissionPolicy(PermissionsConfig(), yolo=True)
    assert pol.resolve("bash", {"cmd": "ls"}, "/work") == AUTO
    assert pol.resolve("write_file", {"path": "x"}, "/work") == AUTO


def test_session_override_wins():
    pol = PermissionPolicy(PermissionsConfig())
    pol.remember("bash", AUTO)
    assert pol.resolve("bash", {"cmd": "rm -rf x"}, "/work") == AUTO


def test_config_deny_is_respected():
    pol = PermissionPolicy(make(bash=DENY))
    assert pol.resolve("bash", {"cmd": "ls"}, "/work") == DENY


def test_deny_path_under_cwd_relative():
    perms = PermissionsConfig()
    perms.deny_paths = ["./secrets"]
    pol = PermissionPolicy(perms)
    assert pol.resolve("write_file", {"path": "secrets/key"}, "/work") == DENY
    assert pol.resolve("write_file", {"path": "other/key"}, "/work") == ASK


# --- move_file: deny_paths must cover BOTH src and dst (it has no "path" arg) ---

def test_move_file_deny_when_dest_under_deny_path():
    pol = PermissionPolicy(PermissionsConfig())
    assert pol.resolve("move_file", {"src": "ok.txt", "dst": "~/.ssh/stolen"}, "/work") == DENY


def test_move_file_deny_when_source_under_deny_path():
    pol = PermissionPolicy(PermissionsConfig())
    assert pol.resolve("move_file", {"src": "~/.ssh/id_rsa", "dst": "/work/x"}, "/work") == DENY


def test_move_file_deny_beats_yolo_both_ends():
    pol = PermissionPolicy(PermissionsConfig(), yolo=True)
    assert pol.resolve("move_file", {"src": "ok", "dst": "~/.ssh/x"}, "/work") == DENY
    assert pol.resolve("move_file", {"src": "~/.ssh/id_rsa", "dst": "/work/x"}, "/work") == DENY


def test_move_file_normal_is_ask_then_yolo_auto():
    assert PermissionPolicy(PermissionsConfig()).resolve(
        "move_file", {"src": "a.txt", "dst": "b.txt"}, "/work") == ASK
    assert PermissionPolicy(PermissionsConfig(), yolo=True).resolve(
        "move_file", {"src": "a.txt", "dst": "b.txt"}, "/work") == AUTO


def test_move_file_auto_allow_under_sandbox():
    pol = PermissionPolicy(PermissionsConfig())  # auto_allow_under=["./sandbox"]
    assert pol.resolve("move_file", {"src": "sandbox/a", "dst": "sandbox/b"}, "/work") == AUTO


def test_an_unlisted_tool_falls_back_to_its_declared_permission():
    # Round 2 left `Tool.permission` decorative: the policy ignored it, so any
    # tool the config did not list resolved to ASK — silently denied headless.
    pol = PermissionPolicy(PermissionsConfig())
    assert pol.resolve("some_new_tool", {}, "/work") == ASK          # declares nothing
    assert pol.resolve("some_new_tool", {}, "/work", "auto") == AUTO
    assert pol.resolve("some_new_tool", {}, "/work", "deny") == DENY


def test_config_still_beats_the_declared_permission():
    pol = PermissionPolicy(PermissionsConfig(tools={"bash": "deny"}))
    assert pol.resolve("bash", {"cmd": "ls"}, "/work", "auto") == DENY


def test_ask_user_is_not_silently_denied_headless():
    # The install-escalation path tells the model to call ask_user when a global
    # tool is missing. Headless, ask_user must reach its own run() and decline
    # with a usable message — not be refused before it gets there.
    pol = PermissionPolicy(PermissionsConfig())
    assert pol.resolve("ask_user", {"question": "?", "options": ["a"]},
                       "/work") == AUTO
