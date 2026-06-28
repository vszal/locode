import textwrap

from locode.config import Config


def test_defaults():
    cfg = Config()
    assert cfg.model.default == "qwen14"
    assert cfg.server.port == 8081
    assert cfg.permissions.tools["bash"] == "ask"
    assert cfg.permissions.tools["read_file"] == "auto"


def test_toml_overrides_defaults(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(textwrap.dedent("""
        [server]
        port = 9090

        [model]
        default = "qwencoder14"

        [permissions]
        bash = "auto"
        deny_paths = ["~/.ssh"]

        [aliases]
        mymodel = "org/Foo-4bit"
    """))
    cfg = Config.load(p)
    assert cfg.server.port == 9090
    assert cfg.model.default == "qwencoder14"
    assert cfg.permissions.tools["bash"] == "auto"
    assert cfg.permissions.deny_paths == ["~/.ssh"]
    assert cfg.aliases["mymodel"] == "org/Foo-4bit"
    # untouched defaults survive
    assert cfg.permissions.tools["write_file"] == "ask"


def test_env_overrides_toml(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    p.write_text("[model]\ndefault = 'qwen14'\n")
    monkeypatch.setenv("LOCODE_MODEL", "phi4")
    monkeypatch.setenv("LOCODE_PORT", "7000")
    cfg = Config.load(p)
    assert cfg.model.default == "phi4"
    assert cfg.server.port == 7000


def test_cli_override_wins(tmp_path):
    cfg = Config().override(model="gemma27", port=1234)
    assert cfg.model.default == "gemma27"
    assert cfg.server.port == 1234


def test_missing_file_is_tolerant(tmp_path):
    cfg = Config.load(tmp_path / "does-not-exist.toml")
    assert cfg.model.default == "qwen14"


def test_broken_toml_falls_back(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("this is = = not valid toml [[[")
    cfg = Config.load(p)
    assert cfg.model.default == "qwen14"


# --- remote / endpoint configuration ------------------------------------------
def test_base_url_default_is_loopback_http():
    cfg = Config()
    assert cfg.base_url == "http://127.0.0.1:8081"
    assert cfg.server.is_managed() is True


def test_base_url_from_host_port_scheme(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(textwrap.dedent("""
        [server]
        host = "gpu-box.lan"
        port = 9000
        scheme = "https"
    """))
    cfg = Config.load(p)
    assert cfg.base_url == "https://gpu-box.lan:9000"
    # a non-loopback host is treated as remote/unmanaged in auto mode
    assert cfg.server.is_managed() is False


def test_explicit_base_url_overrides_and_is_remote(monkeypatch):
    monkeypatch.setenv("LOCODE_BASE_URL", "https://gpu-box:8443/")
    cfg = Config.load(None)
    assert cfg.base_url == "https://gpu-box:8443"     # trailing slash trimmed
    assert cfg.server.is_managed() is False


def test_manage_override_forces_managed(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[server]\nhost = "10.0.0.5"\nmanage = "yes"\n')
    cfg = Config.load(p)
    assert cfg.server.is_managed() is True            # explicit override wins


def test_loopback_base_url_is_managed(monkeypatch):
    monkeypatch.setenv("LOCODE_BASE_URL", "http://localhost:8081")
    cfg = Config.load(None)
    assert cfg.server.is_managed() is True


def test_cli_base_url_and_host_override():
    assert Config().override(base_url="http://1.2.3.4:5000").base_url == "http://1.2.3.4:5000"
    assert Config().override(host="192.168.1.9").base_url == "http://192.168.1.9:8081"


def test_env_scheme_and_host(monkeypatch):
    monkeypatch.setenv("LOCODE_HOST", "myhost")
    monkeypatch.setenv("LOCODE_SCHEME", "https")
    cfg = Config.load(None)
    assert cfg.base_url == "https://myhost:8081"


def test_ui_defaults_and_toml_override(tmp_path):
    assert Config().ui.markdown is True
    assert Config().ui.spinner is True
    p = tmp_path / "config.toml"
    p.write_text("[ui]\nmarkdown = false\ntiming = false\n")
    cfg = Config.load(p)
    assert cfg.ui.markdown is False
    assert cfg.ui.timing is False
    assert cfg.ui.spinner is True   # untouched default survives
