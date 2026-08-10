from __future__ import annotations

from pathlib import Path

import slidge_whatsapp
import slidge_whatsapp.generated._whatsapp  # noqa: F401

package_dir = Path(slidge_whatsapp.__file__).parent
source = (package_dir / "session.go").read_text(encoding="utf-8")
subscribe_start = source.index("func (s *Session) SubscribeToPresences()")
subscribe_end = source.index("\n}\n\n// GetGroups", subscribe_start) + 3
subscribe_source = source[subscribe_start:subscribe_end]
handle_start = source.index("func (s *Session) handleEvent(evt any)")
handle_end = source.index("\n}\n\n// a JSONStringer", handle_start) + 3
handle_source = source[handle_start:handle_end]

required = (
    "clientMutex sync.RWMutex",
    "func (s *Session) startPresenceRefresh(",
    "func (s *Session) stopPresenceRefresh()",
    "case <-refreshCtx.Done():",
    "_ = s.subscribeToPresences(refreshCtx, client)",
    "client := s.currentClient()",
    "client == nil || client.Store == nil || client.Store.Contacts == nil",
    "func (s *Session) handleEvent(evt any)",
)
for fragment in required:
    assert fragment in source, f"missing presence lifecycle fragment: {fragment}"

forbidden = (
    "s.client.Store.Contacts.GetAllContacts(s.ctx)",
    "close(s.presenceChan)",
)
for fragment in forbidden:
    assert fragment not in subscribe_source, (
        f"unsafe presence lifecycle fragment remains: {fragment}"
    )

assert "client := s.currentClient()" in handle_source
assert "s.client" not in handle_source, "handleEvent still reads a mutable client directly"

binary = package_dir / "generated" / "_whatsapp.cpython-313-x86_64-linux-gnu.so"
assert binary.is_file(), "rebuilt Go binding is missing"

print("presence refresh lifecycle runtime smoke: ok")
