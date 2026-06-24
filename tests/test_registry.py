import pytest

from toadies import registry

WB = "https://dees-workbench.local/"
DT = "http://dees-desktop.local:11434/"

VALID = f'''
[boxes.workbench]
url = "{WB}"

[boxes.desktop]
url = "{DT}"

[routing]
fallback = "workbench"

[toadies.gremlin]
tier = "deterministic"
handler = "gremlin_compress"

[toadies.scribe]
tier = "cpu-model"
box = "desktop"
model = "llama3.2:3b"
timeout_s = 30
'''


def _write(tmp_path, text):
    p = tmp_path / "toady_registry.toml"
    p.write_text(text)
    return str(p)


def test_resolve_deterministic_toady_returns_handler_route(tmp_path):
    reg = registry.load(_write(tmp_path, VALID))
    route = reg.resolve("gremlin")
    assert route.tier == "deterministic"
    assert route.handler == "gremlin_compress"


def test_resolve_model_toady_fills_box_url_and_model(tmp_path):
    reg = registry.load(_write(tmp_path, VALID))
    route = reg.resolve("scribe")
    assert route.tier == "cpu-model"
    assert route.box == "desktop"
    assert route.url == DT
    assert route.model == "llama3.2:3b"
    assert route.timeout_s == 30


def test_load_rejects_unknown_tier(tmp_path):
    bad = VALID.replace('tier = "cpu-model"', 'tier = "quantum-model"')
    with pytest.raises(registry.RegistryError):
        registry.load(_write(tmp_path, bad))


def test_load_rejects_model_toady_with_unknown_box(tmp_path):
    bad = VALID.replace('box = "desktop"', 'box = "nonesuch"')
    with pytest.raises(registry.RegistryError):
        registry.load(_write(tmp_path, bad))


def test_load_rejects_model_toady_with_no_model(tmp_path):
    bad = VALID.replace('model = "llama3.2:3b"\n', "")
    with pytest.raises(registry.RegistryError):
        registry.load(_write(tmp_path, bad))


def test_load_rejects_unknown_fallback_box(tmp_path):
    bad = VALID.replace('fallback = "workbench"', 'fallback = "ghost"')
    with pytest.raises(registry.RegistryError):
        registry.load(_write(tmp_path, bad))


def test_resolve_unknown_toady_raises(tmp_path):
    reg = registry.load(_write(tmp_path, VALID))
    with pytest.raises(registry.RegistryError):
        reg.resolve("nobody")


def test_load_default_resolves_the_built_deterministic_toadies():
    reg = registry.load_default()
    gremlin = reg.resolve("gremlin")
    assert gremlin.tier == "deterministic"
    assert gremlin.handler == "gremlin_compress"
    assert reg.resolve("bouncer").handler == "bouncer_scan"
