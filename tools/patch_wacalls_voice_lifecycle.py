"""Narrow, fail-closed lifecycle/error fixes for the pinned experimental WaCalls."""
from __future__ import annotations

import sys
from pathlib import Path


def replace(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError("WaCalls lifecycle source changed")
    return source.replace(old, new)


def manager(source: str) -> str:
    if "canDisposed bool" in source:
        return source
    source = replace(source, "\tmu          sync.Mutex\n",
                     "\tmu          sync.Mutex\n\tcanRelayMu sync.Mutex\n\tcanDisposed bool\n"
                     "\tcanContext context.Context\n\tcanCancel context.CancelFunc\n")
    source = replace(source, "\tm := &CallManager{\n",
                     "\tcanContext,canCancel:=context.WithCancel(context.Background())\n"
                     "\tm := &CallManager{\n\t\tcanContext:canContext, canCancel:canCancel,\n")
    source = replace(source,
                     "\tif m.currentCall != nil && !m.currentCall.IsEnded() {\n",
                     "\tif m.canDisposed {\n\t\tm.mu.Unlock()\n"
                     '\t\treturn &CallError{"call engine is disposed"}\n\t}\n'
                     "\tif m.currentCall != nil && !m.currentCall.IsEnded() {\n")
    source = replace(source, "go m.HandleCallAck(context.Background(), ackNode)",
                     "go m.canHandleCallAck(m.canContext, ackNode, callID)")
    source = replace(source,
                     "\t_ = call.ApplyTransition(Transition{Type: TransitionLocalAccepted})\n",
                     "\tif m.canDisposed || len(call.EncryptionKey)==0 {\n"
                     '\t\tm.mu.Unlock()\n\t\treturn &CallError{"call key unavailable"}\n\t}\n'
                     "\t_ = call.ApplyTransition(Transition{Type: TransitionLocalAccepted})\n")
    source = replace(source, '\t\t\tm.log.Error("build accept failed", "err", err)\n',
                     '\t\t\tm.log.Error("build accept failed", "err", err)\n\t\t\treturn err\n')
    source = replace(source, '\t\t\tm.log.Error("accept send error", "err", err)\n',
                     '\t\t\tm.log.Error("accept send error", "err", err)\n\t\t\treturn err\n')
    # Command contexts end when their response is produced. An unowned goroutine
    # could therefore miss the reject/hangup entirely. Stop media first, then
    # await signaling under the caller's deadline and propagate failures.
    asynchronous = "\tgo func() { _, _ = m.sock.Query(ctx, node) }()\n"
    cleanup = "\tm.cleanupMedia()\n\treturn nil\n"
    if source.count(asynchronous) != 2 or source.count(cleanup) != 2:
        raise ValueError("WaCalls teardown source changed")
    return source.replace(asynchronous, "").replace(
        cleanup, "\tm.cleanupMedia()\n\t_,err:=m.sock.Query(ctx,node)\n\treturn err\n",
    )


def relay(source: str) -> str:
    if "// CAN serialize relay setup" in source:
        return source
    source = replace(source,
                     "func (m *CallManager) connectRelays(endpoints []core.RelayEndpoint) {\n",
                     "func (m *CallManager) connectRelays(endpoints []core.RelayEndpoint) {\n"
                     "\t// CAN serialize relay setup with cleanup; late ACKs cannot reopen it.\n"
                     "\tm.canRelayMu.Lock()\n\tdefer m.canRelayMu.Unlock()\n")
    source = replace(source, "\tm.mu.Lock()\n\tm.relay.SetSsrc(m.selfSsrc)\n",
                     "\tm.mu.Lock()\n"
                     "\tif m.canDisposed || m.currentCall==nil || m.currentCall.IsEnded() {\n"
                     "\t\tm.mu.Unlock()\n\t\treturn\n\t}\n"
                     "\tm.relay.SetSsrc(m.selfSsrc)\n")
    return replace(source, "func (m *CallManager) cleanupMedia() {\n",
                   "func (m *CallManager) cleanupMedia() {\n"
                   "\tm.canRelayMu.Lock()\n\tdefer m.canRelayMu.Unlock()\n")


def signaling(source: str) -> str:
    if "func (m *CallManager) canHandleCallAck(" in source:
        return source
    source = replace(source,
                     "func (m *CallManager) HandleCallAck(ctx context.Context, "
                     "node *waBinary.Node) {\n",
                     "func (m *CallManager) HandleCallAck(ctx context.Context, "
                     "node *waBinary.Node) {\n"
                     '\tm.canHandleCallAck(ctx, node, "")\n}\n\n'
                     "func (m *CallManager) canHandleCallAck(ctx context.Context, "
                     "node *waBinary.Node, expectedID string) {\n")
    return replace(source,
                   "\tif call == nil {\n\t\tm.mu.Unlock()\n\t\treturn\n\t}\n\tcall.RelayData =",
                   "\tif call == nil || m.canDisposed || call.IsEnded() || "
                   '(expectedID!="" && call.CallID!=expectedID) {\n'
                   "\t\tm.mu.Unlock()\n\t\treturn\n\t}\n\tcall.RelayData =")


DISPOSE = '''package call
import (
    "context"
    "wacalls/internal/voip/core"
)

// Dispose permanently retires this engine. It never disconnects WhatsMeow.
func (m *CallManager) Dispose(ctx context.Context) {
    m.mu.Lock()
    m.canDisposed = true
    m.mu.Unlock()
    m.canCancel()
    _ = m.EndCall(ctx, core.EndCallReasonCancelled)
    m.cleanupMedia()
}
'''


def patch(root: Path) -> None:
    directory = root / "internal/voip/call"
    transforms = {"callmanager.go": manager, "callmanager_relay.go": relay,
                  "callmanager_signaling.go": signaling}
    outputs = {name: transform((directory / name).read_text(encoding="utf-8"))
               for name, transform in transforms.items()}
    for name, body in outputs.items():
        (directory / name).write_text(body, encoding="utf-8", newline="\n")
    (directory / "can_dispose.go").write_text(DISPOSE, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
