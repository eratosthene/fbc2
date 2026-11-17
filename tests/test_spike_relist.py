"""The throwaway re-list spike (scripts/spike_relist.py) only verifies."""

import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parent.parent / "scripts" / "spike_relist.py"


@pytest.fixture
def spike():
    spec = importlib.util.spec_from_file_location("spike_relist", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # It pushes an app context for its own use; the test has one already.
    module.context.pop()
    return module


@pytest.mark.parametrize(
    "verb",
    [
        "RelistFixedPriceItem",
        "RelistItem",
        "VerifyRelistFixedPriceItem",
        "EndFixedPriceItem",
        "AddFixedPriceItem",
        "ReviseItem",
    ],
)
def test_it_refuses_to_send_anything_but_a_verify_or_a_read(spike, monkeypatch, verb):
    sent = []
    monkeypatch.setattr(spike.api, "_call", lambda *a: sent.append(a))

    with pytest.raises(SystemExit):
        spike.call(verb, {})

    assert sent == []


def test_it_sends_nothing_in_dev_mode(spike, monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(spike.api, "_call", lambda *a: sent.append(a))
    monkeypatch.setattr(spike.devmode, "on", lambda: True)
    monkeypatch.setattr("sys.argv", ["spike_relist.py"])

    assert spike.main() == 1

    assert sent == []
    assert "Dev mode is on" in capsys.readouterr().out
