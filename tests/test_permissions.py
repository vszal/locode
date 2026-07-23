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
