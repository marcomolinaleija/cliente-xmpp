from __future__ import annotations

import argparse
import re
from pathlib import Path

if __package__:
    from .patch_slidge_message_info_v33 import replace
else:
    from patch_slidge_message_info_v33 import replace

# ruff: noqa: E501 -- guarded native source anchors

def patch_source(source: str) -> str:
    source = replace(source, "type Session struct {\n", "type Session struct {\n"
        "\tcanCallRecoveryMutex sync.Mutex\n"
        "\tcanCallRecoveries map[string]*canCallRecoveryEntry\n")
    source = replace(source, "\tcallback := func() { s.eventHandler(kind, payload) }\n",
        "\tif kind == EventCall { s.canObserveCall(payload) }\n"
        "\tcallback := func() { s.eventHandler(kind, payload) }\n")
    source = replace(source, '\ts.gateway.logger.Debugf("Handling event',
        '\ts.canTraceCallSignal(evt)\n\ts.gateway.logger.Debugf("Handling event')
    source = replace(source, "\t\ts.client = nil\n", "\t\ts.canCancelCallRecoveries()\n\t\ts.client = nil\n")
    source = replace(source, "\t\tif _, connected := evt.(*events.Connected); connected {\n",
        "\t\tif _, connected := evt.(*events.Connected); connected {\n"
        '\t\t\ts.canCallLog("CAN_CALL stage=diagnostics_ready")\n')
    return replace(source,
        "s.ctx, client, callStateOffered, callDirectionIncoming, callKindUnknown, 1, \"\", evt.BasicCallMeta,",
        "s.ctx, client, callStateOffered, callDirectionIncoming, canCallOfferKind(evt.Data), 1, \"\", evt.BasicCallMeta,")


def patch_package(site_packages: Path) -> bool:
    package = site_packages / "slidge_whatsapp"
    target = package / "session.go"
    source = target.read_text(encoding="utf-8")
    helper = Path(__file__).with_name("bridge_call_recovery_v33.go").read_text(encoding="utf-8")
    helper_target = package / "can_call_recovery.go"
    if re.search(r"\bcanCallRecoveryMutex\s+sync.Mutex\b", source):
        if (not helper_target.exists()
                or helper_target.read_text(encoding="utf-8").rstrip("\n") != helper.rstrip("\n")):
            raise ValueError("Existing call recovery helper differs; refusing partial replacement")
        return False
    if helper_target.exists():
        raise ValueError("Unexpected call recovery helper already exists")
    changed = patch_source(source)  # All guards before any write.
    target.write_text(changed, encoding="utf-8", newline="\n")
    helper_target.write_text(helper, encoding="utf-8", newline="\n")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recover incoming call records without guessing duration")
    parser.add_argument("site_packages", type=Path)
    print("Call recovery applied" if patch_package(parser.parse_args().site_packages) else "Call recovery already present")
